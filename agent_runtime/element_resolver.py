"""Resolve an I-GUIDE ``element_id`` to its downloadable source file(s).

The platform backend exposes a PUBLIC element API:

    GET {IGUIDE_BACKEND_URL}/api/elements/{element_id}

which returns element metadata including source pointers. Per resource-type:

  - notebook : ``notebook-url`` (GitHub *blob* URL), ``notebook-repo``,
               ``notebook-file`` (filename), ``html-notebook`` (rendered HTML).
  - code     : ``notebook-repo`` / repo URL.
  - dataset  : ``direct-download-link`` (often a GitHub blob or a direct file
               URL) and/or ``external-link``.
  - publication / oer : no source-file URL is exposed; the publication TEXT is
               already available in the search index as ``pdf_chunks``.

``download_element_source`` fetches the bytes, rewriting a GitHub ``blob`` URL to
``raw.githubusercontent.com``. Everything here is read-only / unauthenticated.

CLI:
    python -m agent_runtime.element_resolver <element_id> [<element_id> ...]
    python -m agent_runtime.element_resolver --selftest
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

DEFAULT_BACKEND = "https://backend.i-guide.io"
_BLOB_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)/blob/(.+)$")
_REPO_RE = re.compile(r"https?://github\.com/([^/]+)/([^/#?]+)")


def backend_url() -> str:
    return os.getenv("IGUIDE_BACKEND_URL", DEFAULT_BACKEND).rstrip("/")


def _cache_dir() -> Path:
    """Default download/cache location under the agent file storage root."""
    try:
        from agent_runtime.file_store import storage_root
        root = Path(storage_root())
    except Exception:
        root = Path(os.getenv("AGENT_FILE_STORAGE_ROOT", "./agent_chat_files")).expanduser()
    p = root / "element_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def github_blob_to_raw(url: str) -> str:
    """``github.com/{owner}/{repo}/blob/{ref}/{path}`` -> raw.githubusercontent URL.
    Returns ``url`` unchanged if it is not a GitHub blob link."""
    m = _BLOB_RE.match(url or "")
    if not m:
        return url
    owner, repo, rest = m.groups()
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{rest}"


def _filename_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    name = url.split("?")[0].rstrip("/").split("/")[-1]
    return name or None


def _ref_from_blob(blob: str) -> Optional[str]:
    m = _BLOB_RE.match(blob or "")
    if not m:
        return None
    rest = m.group(3)  # "<ref>/<path...>"
    return rest.split("/")[0] if rest else None


def _gh_headers() -> Dict[str, str]:
    h = {"Accept": "application/vnd.github+json"}
    tok = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _parse_github_owner_repo(url: str):
    m = _REPO_RE.match(url or "")
    if not m:
        return None, None
    owner, repo = m.group(1), m.group(2)
    return owner, (repo[:-4] if repo.endswith(".git") else repo)


def github_find_raw_url(repo_url: str, filename: str, *, timeout: int = 30) -> Optional[str]:
    """Locate ``filename`` inside a GitHub repo (default branch, any subdir) via the
    tree API and return its raw.githubusercontent URL. Falls back to repo-root."""
    owner, repo = _parse_github_owner_repo(repo_url)
    if not owner or not repo:
        return None
    branch = "main"
    try:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}",
                         headers=_gh_headers(), timeout=timeout)
        if r.ok:
            branch = r.json().get("default_branch") or branch
    except Exception:
        pass
    want = (filename or "").strip().lower()
    try:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1",
                         headers=_gh_headers(), timeout=timeout)
        if r.ok:
            tree = r.json().get("tree") or []
            cands = [t["path"] for t in tree if t.get("type") == "blob"
                     and t.get("path", "").split("/")[-1].lower() == want]
            if not cands:
                cands = [t["path"] for t in tree if t.get("type") == "blob"
                         and t.get("path", "").lower().endswith(want)]
            if cands:
                path = sorted(cands, key=lambda p: p.count("/"))[0]  # shallowest
                return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{quote(path)}"
    except Exception:
        pass
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{quote(filename)}"


def fetch_element_metadata(element_id: str, *, timeout: int = 30) -> Dict[str, Any]:
    """Raw element JSON from the public backend API (raises on HTTP error)."""
    r = requests.get(f"{backend_url()}/api/elements/{element_id}", timeout=timeout)
    r.raise_for_status()
    return r.json()


def _related(j: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for rel in (j.get("related-elements") or []):
        if isinstance(rel, dict):
            out.append({
                "element_id": str(rel.get("id") or rel.get("element_id") or ""),
                "title": str(rel.get("title") or ""),
                "resource_type": str(rel.get("resource-type") or rel.get("resource_type") or ""),
            })
    return out


def resolve_element(element_id: str) -> Dict[str, Any]:
    """Return normalized source pointers for ``element_id`` (public backend API).

    Keys: element_id, title, resource_type, authors, tags, source_url (canonical
    fetchable URL, raw for GitHub), source_kind ('github'|'direct'|None), filename,
    github_repo, github_ref, html_url, related, raw_meta_keys.
    """
    j = fetch_element_metadata(element_id)
    rtype = str(j.get("resource-type") or j.get("resource_type") or "").strip().lower()
    out: Dict[str, Any] = {
        "element_id": element_id,
        "title": j.get("title") or "",
        "resource_type": rtype,
        "authors": j.get("authors") or [],
        "tags": j.get("tags") or [],
        "abstract": j.get("contents") or "",
        "contributor": j.get("contributor") or {},
        "source_url": None,
        "source_kind": None,
        "filename": None,
        "github_repo": None,
        "github_ref": None,
        "html_url": j.get("html-notebook") or None,
        "related": _related(j),
        "raw_meta_keys": sorted(j.keys()),
    }
    if rtype == "notebook":
        blob = j.get("notebook-url") or ""
        out["github_repo"] = j.get("notebook-repo") or None
        out["filename"] = j.get("notebook-file") or _filename_from_url(blob) or f"{element_id}.ipynb"
        if blob:
            out["source_url"] = github_blob_to_raw(blob)
            out["source_kind"] = "github"
            out["github_ref"] = _ref_from_blob(blob)
        elif out["github_repo"] and j.get("notebook-file"):
            # uploaded notebook: source lives in notebook-repo; raw URL resolved at download.
            out["source_kind"] = "github_repo"
    elif rtype == "code":
        repo = j.get("notebook-repo") or j.get("code-repo") or j.get("external-link") or ""
        out["github_repo"] = repo or None
        out["source_url"] = repo or None
        out["source_kind"] = "github" if "github.com" in repo else ("direct" if repo else None)
    elif rtype == "dataset":
        link = j.get("direct-download-link") or j.get("external-link") or ""
        if link:
            is_blob = "github.com" in link and "/blob/" in link
            out["source_url"] = github_blob_to_raw(link) if is_blob else link
            out["source_kind"] = "github" if "github.com" in link else "direct"
            out["filename"] = _filename_from_url(out["source_url"])
    # publication / oer: no source file URL exposed; text lives in index 'pdf_chunks'.
    return out


def download_element_source(
    element_id: str,
    dest_dir: "str | Path | None" = None,
    *,
    force: bool = False,
    timeout: int = 60,
) -> Dict[str, Any]:
    """Resolve + download the element's source file.

    Saves as ``{element_id}{ext}`` (so callers that map filename-stem -> element_id,
    e.g. build_eval_kb.py, work directly). Returns the resolve_element dict plus
    ``path`` (None for publications with no file) and ``bytes``/``cached``/``note``.
    """
    info = resolve_element(element_id)
    dest = Path(dest_dir).expanduser() if dest_dir else _cache_dir()
    dest.mkdir(parents=True, exist_ok=True)
    info["path"] = None

    url = info.get("source_url")
    if not url and info.get("source_kind") == "github_repo":
        url = github_find_raw_url(info.get("github_repo") or "", info.get("filename") or "")
        info["source_url"] = url
    if not url:
        info["note"] = f"no downloadable source URL for resource-type={info['resource_type']!r}"
        return info

    fname = info.get("filename") or element_id
    ext = Path(fname).suffix or (".ipynb" if info["resource_type"] == "notebook" else "")
    target = dest / f"{element_id}{ext}"
    if target.exists() and not force:
        info["path"] = str(target)
        info["cached"] = True
        return info
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        target.write_bytes(r.content)
        info["path"] = str(target)
        info["bytes"] = len(r.content)
        info["cached"] = False
    except Exception as exc:
        info["note"] = f"download failed from {url}: {type(exc).__name__}: {exc}"
    return info


def _selftest() -> int:
    """Validate the resolver across notebook / dataset / publication."""
    import json
    import tempfile
    targets = {
        "notebook": "cca9b545-8416-45a3-9267-122ce6ce9991",
        "dataset": "1efb4820-548c-49b5-8a91-8474f201588b",
        "publication": "1c42c1a6-ceec-458c-827d-36fe36651020",
    }
    tmp = Path(tempfile.mkdtemp(prefix="iguide_resolver_test_"))
    rc = 0
    for kind, eid in targets.items():
        res = download_element_source(eid, tmp, force=True)
        print(f"\n[{kind}] {eid}")
        print(f"  source_kind={res.get('source_kind')} url={res.get('source_url')}")
        print(f"  path={res.get('path')} bytes={res.get('bytes')} note={res.get('note','')}")
        if kind == "notebook":
            p = res.get("path")
            if not p:
                print("  FAIL: no notebook downloaded"); rc = 1; continue
            try:
                import nbformat
                nb = nbformat.read(p, as_version=4)
                ncells = len(nb.get("cells", []))
                print(f"  OK: nbformat parsed, {ncells} cells")
                if ncells == 0:
                    print("  WARN: 0 cells");
            except Exception as exc:
                print(f"  FAIL: nbformat parse error: {type(exc).__name__}: {exc}"); rc = 1
        elif kind == "dataset":
            print("  OK" if res.get("path") else "  WARN: dataset had no direct file URL")
        else:
            print("  OK: publication has no file URL (expected; text via index pdf_chunks)")
    return rc


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] == "--selftest":
        return _selftest()
    import json
    dest = _cache_dir()
    for eid in argv:
        res = download_element_source(eid, dest, force=True)
        print(json.dumps({k: res.get(k) for k in
                          ("element_id", "title", "resource_type", "source_url", "path", "bytes", "note")},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # ensure repo root importable when run directly
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
