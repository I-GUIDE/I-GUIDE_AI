"""R1 prototype: IPython-aware cell transformer + construct classifier.

Proves out requirement R1 from NOTEBOOK_FEATURE_SURVEY.md: turn notebook cells
into Python that `ast.parse` accepts, and classify each non-Python construct so
downstream stages (dataflow DAG, env capture, tool wrapping) have structured
input instead of silently-discarded `!`/`%` lines.

IMPORTANT: this is a STATIC analyzer. It does source-to-source transformation
(IPython's InputTransformerManager) + `ast.parse` + regex classification. It
NEVER executes notebook code — the `get_ipython().system(...)` calls that the
transform emits are only parsed, never run.

Usage:
    python3 MCP_server/prototypes/r1_ipython_frontend.py <notebook.ipynb> [more.ipynb ...]
"""

from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import nbformat

try:
    from IPython.core.inputtransformer2 import TransformerManager
    _IPYTHON_TM: Optional[TransformerManager] = TransformerManager()
except Exception:  # pragma: no cover - fallback when IPython absent
    _IPYTHON_TM = None


# Shell commands that appear as IPython "auto-magics" (bare, no `!`). IPython's
# static transformer does NOT rewrite these, so we pre-promote them to `!cmd`.
SHELL_CMDS = {
    "ls", "cd", "pwd", "cat", "cp", "mv", "rm", "mkdir", "rmdir", "echo",
    "head", "tail", "chmod", "chown", "wget", "curl", "grep", "find", "touch",
    "unzip", "tar", "which", "conda", "mamba", "pip", "git", "make", "export",
    "source", "sed", "awk", "mpirun", "mpiexec", "srun", "sbatch",
}
# arg-0 wrappers: the real binary is *after* the wrapper's own flags/values.
WRAPPERS = {"mpirun", "mpiexec", "srun", "xargs", "time", "sudo", "nice", "env"}
# Per-wrapper flags that CONSUME the following token (so we must skip both).
WRAPPER_VALUE_FLAGS = {
    "mpirun": {"-np", "-n", "--np", "--n", "-c", "-host", "-hostfile",
               "-machinefile", "-x", "-wdir", "-path", "-bind-to", "-map-by", "-rank-by"},
    "mpiexec": {"-np", "-n", "--np", "--n", "-c", "-host", "-hostfile",
                "-machinefile", "-x", "-wdir", "-path", "-bind-to", "-map-by", "-rank-by"},
    "srun": {"-n", "--ntasks", "-N", "--nodes", "-c", "--cpus-per-task",
             "-p", "--partition", "--mem", "-t", "--time"},
    "sudo": {"-u", "-g", "-C", "--user", "--group"},
    "nice": {"-n"},
    "xargs": {"-n", "-P", "-I", "-d", "-E", "-s"},
    "time": set(),
    "env": set(),
}
# Interpolated/literal spellings of "the Python interpreter".
PY_EXES = {"{sys.executable}", "{executable}", "{python}", "{PYTHON}", "python", "python3"}

INSTALL_RE = re.compile(r"^(?:!|%)?\s*(?:uv\s+)?(pip3?|conda|mamba)\s+(install|create)\b")
ACQUIRE_RE = re.compile(r"\b(wget|curl|git\s+clone|git\s+lfs)\b")
CAPTURE_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*!(.+)$")
LINE_MAGIC_RE = re.compile(r"^\s*%([A-Za-z_]\w*)(.*)$")
CELL_MAGIC_RE = re.compile(r"^\s*%%([A-Za-z_]\w*)(.*)$")
BANG_RE = re.compile(r"^\s*!\s*(.+)$")
HELP_RE = re.compile(r"^\s*\??[A-Za-z_][\w\.]*\??\s*$")


@dataclass
class Construct:
    cell_index: int
    line_no: int
    text: str
    category: str
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CellReport:
    index: int
    cell_type: str
    constructs: List[Construct]
    r1_parse_ok: bool
    r1_error: Optional[str]
    current_parse_ok: bool          # what the existing builder's approach yields
    current_lost_lines: int         # real shell/magic lines the current builder discards


def _clean_cmd(cmd: str) -> str:
    """Drop shell redirections and trailing inline comments for analysis."""
    cmd = re.sub(r"\s+\d?>>?\s*\S+", "", cmd)   # 2>/dev/null, >out, >>log
    cmd = re.sub(r"\s+#\s.*$", "", cmd)          # trailing # comment
    return cmd.strip()


def _resolve_tool(cmd: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """Resolve the real executable of a shell command.

    Handles launcher/wrapper flag-arity (``mpirun -np {np} pitremove`` -> ``pitremove``)
    and interpolated interpreters (``{sys.executable} foo.py`` -> ``python`` + script).
    Returns (tool_or_None, meta) where meta may carry wrapper/script/target_hint.
    """
    toks = _clean_cmd(cmd).split()
    if not toks:
        return None, {}
    meta: Dict[str, Any] = {}
    a0 = toks[0].strip("\"'")
    start = 1

    if a0 in WRAPPERS:
        meta["wrapper"] = a0
        vflags = WRAPPER_VALUE_FLAGS.get(a0, set())
        i = 1
        a0 = None
        while i < len(toks):
            t = toks[i]
            if a0 is None and meta.get("wrapper") == "env" and "=" in t and not t.startswith("-"):
                i += 1
                continue
            if t in vflags:
                i += 2
                continue
            if t.startswith("-"):
                i += 1
                continue
            a0 = t.strip("\"'")
            start = i + 1
            break
        if a0 is None:
            return None, meta

    base = a0.strip("{}")
    if a0 in PY_EXES or base in {"sys.executable", "executable", "python", "python3"}:
        for t in toks[start:]:
            if not t.startswith("-"):
                meta["script"] = t.strip("\"'")
                break
        return "python", meta

    if "{" in a0 or "$" in a0:
        meta["interpolated"] = True
        for t in toks[start:]:
            if not t.startswith("-") and "{" not in t and "$" not in t:
                meta["target_hint"] = t.strip("\"'")
                break
        return None, meta

    return a0, meta


def _cmd_detail(cmd: str) -> Dict[str, Any]:
    tool, meta = _resolve_tool(cmd)
    det: Dict[str, Any] = {"command": _clean_cmd(cmd), "tool": tool}
    det.update(meta)
    if "{" in cmd or "$" in cmd:
        det["interpolated"] = True
    return det


def _packages(after_install: str) -> List[str]:
    pkgs: List[str] = []
    skip_next = False
    for tok in after_install.split():
        if skip_next:
            skip_next = False
            continue
        if tok in ("-n", "--name", "-c", "--channel", "-r", "--requirement"):
            skip_next = True
            continue
        if tok.startswith("-"):
            continue
        # strip version pins for the name, keep raw too
        pkgs.append(tok)
    return pkgs


def classify_line(line: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Classify a single logical source line. Returns None for plain Python."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    m = INSTALL_RE.match(line)
    if m:
        after = line.split(m.group(2), 1)[1]
        return "DEPENDENCY", {"manager": m.group(1), "op": m.group(2), "packages": _packages(after)}

    cap = CAPTURE_RE.match(line)
    if cap:
        det = _cmd_detail(cap.group(2))
        det["var"] = cap.group(1)
        return "SHELL_CAPTURE", det

    cm = CELL_MAGIC_RE.match(line)
    if cm:
        return "CELL_MAGIC", {"name": cm.group(1)}

    bang = BANG_RE.match(line)
    if bang:
        cmd = bang.group(1)
        det = _cmd_detail(cmd)
        return ("ACQUISITION" if ACQUIRE_RE.search(cmd) else "CLI_STEP"), det

    lm = LINE_MAGIC_RE.match(line)
    if lm:
        det = {"name": lm.group(1)}
        if "$" in lm.group(2):
            det["interpolated"] = True
        return "LINE_MAGIC", det

    # bare auto-magic shell command (e.g. `ls data/x`) — only if NOT valid Python
    first = stripped.split()[0] if stripped.split() else ""
    if first in SHELL_CMDS:
        try:
            ast.parse(stripped)
        except SyntaxError:
            return "AUTO_MAGIC", _cmd_detail(stripped)
    return None


def _promote_automagics(source: str) -> str:
    """Pre-pass: rewrite bare shell auto-magics to `!cmd` so IPython can transform them."""
    out = []
    for line in source.splitlines():
        stripped = line.strip()
        first = stripped.split()[0] if stripped.split() else ""
        if first in SHELL_CMDS and not stripped.startswith(("!", "%", "#")):
            try:
                ast.parse(stripped)
            except SyntaxError:
                indent = line[: len(line) - len(line.lstrip())]
                out.append(f"{indent}!{stripped}")
                continue
        out.append(line)
    return "\n".join(out)


def transform_cell(source: str) -> Tuple[str, bool, Optional[str]]:
    """IPython-aware transform → (transformed_source, parse_ok, error)."""
    pre = _promote_automagics(source)
    if _IPYTHON_TM is not None:
        try:
            transformed = _IPYTHON_TM.transform_cell(pre)
        except Exception as exc:  # transformer itself failed
            transformed = pre
            note = f"transformer_error: {exc}"
        else:
            note = None
    else:
        # minimal fallback: convert !/% lines to harmless calls
        transformed = _regex_fallback(pre)
        note = "ipython_unavailable_fallback"
    try:
        ast.parse(transformed)
        return transformed, True, note
    except SyntaxError as exc:
        return transformed, False, f"SyntaxError: {exc.msg} (line {exc.lineno})"


def _regex_fallback(source: str) -> str:
    out = []
    for line in source.splitlines():
        s = line.strip()
        cap = CAPTURE_RE.match(line)
        if cap:
            out.append(f"{cap.group(1)} = _shell({cap.group(2)!r})")
        elif s.startswith("!"):
            out.append(f"_shell({s[1:].strip()!r})")
        elif s.startswith("%%"):
            out.append(f"_cellmagic({s[2:]!r})")
        elif s.startswith("%"):
            out.append(f"_linemagic({s[1:]!r})")
        else:
            out.append(line)
    return "\n".join(out)


def _simulate_current_builder(source: str) -> Tuple[bool, int]:
    """Replicate notebook_workflow_builder._sanitize_line + ast.parse per cell."""
    lost = 0
    sani = []
    for line in source.splitlines():
        s = line.lstrip()
        if s.startswith(("%%", "%", "!", "?")):
            sani.append(f"# removed: {line}")
            lost += 1
        else:
            sani.append(line)
    try:
        ast.parse("\n".join(sani))
        return True, lost
    except SyntaxError:
        return False, lost


def _ast_extra(transformed: str, cell_index: int) -> List[Construct]:
    """Find imports and subprocess/os.system CLI calls in transformed Python."""
    found: List[Construct] = []
    try:
        tree = ast.parse(transformed)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.append(Construct(cell_index, getattr(node, "lineno", 0),
                                        f"import {a.name}", "IMPORT", {"module": a.name.split(".")[0]}))
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(Construct(cell_index, getattr(node, "lineno", 0),
                                    f"from {node.module} import ...", "IMPORT",
                                    {"module": node.module.split(".")[0]}))
        elif isinstance(node, ast.Call):
            fn = node.func
            name = None
            if isinstance(fn, ast.Attribute):
                owner = getattr(fn.value, "id", None)
                if owner == "subprocess" and fn.attr in {"run", "call", "check_call", "check_output", "Popen"}:
                    name = f"subprocess.{fn.attr}"
                elif owner == "os" and fn.attr == "system":
                    name = "os.system"
            if name and node.args:
                a0 = node.args[0]
                cmdstr = None
                if isinstance(a0, ast.List):
                    parts = [str(e.value) for e in a0.elts if isinstance(e, ast.Constant)]
                    cmdstr = " ".join(parts)
                elif isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                    cmdstr = a0.value
                det: Dict[str, Any] = {"via": name}
                if cmdstr:
                    tool, meta = _resolve_tool(cmdstr)
                    det["command"] = cmdstr
                    det["tool"] = tool
                    det.update(meta)
                found.append(Construct(cell_index, getattr(node, "lineno", 0),
                                       f"{name}(...)", "CLI_STEP", det))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "exec":
            found.append(Construct(cell_index, getattr(node, "lineno", 0), "exec(...)",
                                   "DYNAMIC_EXEC", {}))
    # exec() detection (Name-based) separately to be safe
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "exec":
            found.append(Construct(cell_index, getattr(node, "lineno", 0), "exec(...)",
                                   "DYNAMIC_EXEC", {}))
    return found


def analyze_notebook(path: Path) -> Dict[str, Any]:
    nb = nbformat.read(path, as_version=4)
    cell_reports: List[CellReport] = []
    all_constructs: List[Construct] = []

    for idx, cell in enumerate(nb.cells):
        if cell.cell_type == "raw":
            c = Construct(idx, 0, (cell.source or "")[:60], "RAW_NONPYTHON",
                          {"looks_like_yaml": "---" in (cell.source or "")[:5] or ":" in (cell.source or "")})
            cell_reports.append(CellReport(idx, "raw", [c], True, None, True, 0))
            all_constructs.append(c)
            continue
        if cell.cell_type != "code":
            continue
        source = str(cell.source or "")
        if not source.strip():
            continue

        constructs: List[Construct] = []
        for ln, line in enumerate(source.splitlines(), 1):
            res = classify_line(line)
            if res:
                constructs.append(Construct(idx, ln, line.strip(), res[0], res[1]))

        transformed, ok, err = transform_cell(source)
        constructs.extend(_ast_extra(transformed, idx))
        cur_ok, cur_lost = _simulate_current_builder(source)

        cell_reports.append(CellReport(idx, "code", constructs, ok, err, cur_ok, cur_lost))
        all_constructs.extend(constructs)

    # aggregate
    def collect(cat: str, key: str) -> List[str]:
        vals: List[str] = []
        for c in all_constructs:
            if c.category == cat:
                v = c.detail.get(key)
                if isinstance(v, list):
                    vals.extend(v)
                elif v:
                    vals.append(v)
        return sorted(set(vals))

    code_cells = [r for r in cell_reports if r.cell_type == "code"]
    _tool_cats = {"CLI_STEP", "AUTO_MAGIC", "SHELL_CAPTURE", "ACQUISITION"}
    tool_vals: List[str] = []
    for c in all_constructs:
        if c.category in _tool_cats:
            if c.detail.get("tool"):
                tool_vals.append(c.detail["tool"])
            if c.detail.get("wrapper"):
                tool_vals.append(c.detail["wrapper"])
    scripts = sorted(set(c.detail.get("script") for c in all_constructs if c.detail.get("script")))
    return {
        "notebook": path.name,
        "code_cells": len(code_cells),
        "r1_parse_ok_cells": sum(1 for r in code_cells if r.r1_parse_ok),
        "r1_failed_cells": [r.index for r in code_cells if not r.r1_parse_ok],
        "current_builder_crash_cells": [r.index for r in code_cells if not r.current_parse_ok],
        "current_builder_lost_lines": sum(r.current_lost_lines for r in code_cells),
        "pip_packages": collect("DEPENDENCY", "packages"),
        "system_tools": sorted(set(tool_vals)),
        "scripts_invoked": scripts,
        "acquisitions": [c.detail.get("command") for c in all_constructs if c.category == "ACQUISITION"],
        "magics": sorted(set(
            [c.detail.get("name") for c in all_constructs if c.category in {"LINE_MAGIC", "CELL_MAGIC"}]
        )),
        "python_imports": collect("IMPORT", "module"),
        "hazards": {
            "shell_interpolation_cells": sorted(set(
                c.cell_index for c in all_constructs if c.detail.get("interpolated"))),
            "dynamic_exec_cells": sorted(set(
                c.cell_index for c in all_constructs if c.category == "DYNAMIC_EXEC")),
            "raw_nonpython_cells": sorted(set(
                c.cell_index for c in all_constructs if c.category == "RAW_NONPYTHON")),
        },
        "category_counts": _counts(all_constructs),
    }


def _counts(constructs: List[Construct]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in constructs:
        out[c.category] = out.get(c.category, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _fmt(report: Dict[str, Any]) -> str:
    L = []
    nb = report["notebook"]
    cc = report["code_cells"]
    ok = report["r1_parse_ok_cells"]
    L.append(f"\n{'='*72}\n{nb}  ({cc} code cells)\n{'='*72}")
    L.append(f"  R1 parse OK:        {ok}/{cc} cells" + ("  ✅" if ok == cc else f"  ⚠️ failed: {report['r1_failed_cells']}"))
    crash = report["current_builder_crash_cells"]
    verdict = "WHOLE NOTEBOOK CRASHES" if crash else "parses (but lossy)"
    L.append(f"  Current builder:    {verdict}" + (f"  crash cells: {crash}" if crash else ""))
    L.append(f"  Work current builder silently discards: {report['current_builder_lost_lines']} shell/magic lines")
    L.append(f"  Construct counts:   {report['category_counts']}")
    if report["pip_packages"]:
        L.append(f"  → dependencies:     {report['pip_packages']}")
    if report["system_tools"]:
        L.append(f"  → system tools:     {report['system_tools']}")
    if report.get("scripts_invoked"):
        L.append(f"  → scripts invoked:  {report['scripts_invoked']}")
    if report["acquisitions"]:
        L.append(f"  → acquisitions:     {report['acquisitions']}")
    if report["magics"]:
        L.append(f"  → magics:           {report['magics']}")
    haz = report["hazards"]
    haz_nonempty = {k: v for k, v in haz.items() if v}
    if haz_nonempty:
        L.append(f"  → hazards flagged:  {haz_nonempty}")
    return "\n".join(L)


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    print(f"IPython transformer: {'available' if _IPYTHON_TM else 'NOT available (regex fallback)'}")
    grand = {"cells": 0, "r1_ok": 0, "current_crash_nb": 0, "lost": 0}
    for arg in argv[1:]:
        p = Path(arg).expanduser()
        if not p.exists():
            print(f"!! missing: {p}")
            continue
        rep = analyze_notebook(p)
        print(_fmt(rep))
        grand["cells"] += rep["code_cells"]
        grand["r1_ok"] += rep["r1_parse_ok_cells"]
        grand["current_crash_nb"] += 1 if rep["current_builder_crash_cells"] else 0
        grand["lost"] += rep["current_builder_lost_lines"]
    print(f"\n{'#'*72}\nTOTALS: R1 parsed {grand['r1_ok']}/{grand['cells']} code cells across notebooks; "
          f"current builder would crash on {grand['current_crash_nb']} of the notebooks and "
          f"discard {grand['lost']} shell/magic work-lines.\n{'#'*72}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
