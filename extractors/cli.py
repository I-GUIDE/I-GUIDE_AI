"""CLI entry point for GitHub-repo ingestion (code + notebook).

    python -m extractors.cli <github_url> [--targets opensearch,mcp,skill] [--ref REF] [--reingest]

Dataset/publication ingestion is delivered through the webhook, not this CLI.
"""

from __future__ import annotations

import argparse
import sys
from typing import List

from .base import VALID_TARGETS
from .ingest import ingest_from_github


def _parse_targets(raw: str) -> List[str]:
    targets = [t.strip() for t in (raw or "").split(",") if t.strip()]
    bad = [t for t in targets if t not in VALID_TARGETS]
    if bad:
        raise SystemExit(f"unknown --targets value(s): {bad}; valid: {list(VALID_TARGETS)}")
    return targets or list(VALID_TARGETS)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="extractors.cli",
        description="Ingest a GitHub repo: extract notebook + code assets to OpenSearch / MCP / SKILL.",
    )
    p.add_argument("url", help="GitHub repository URL")
    p.add_argument("--ref", default="", help="git ref/branch/tag/sha to check out (default: repo default)")
    p.add_argument("--targets", default=",".join(VALID_TARGETS),
                   help=f"comma-separated emit targets (default: all of {list(VALID_TARGETS)})")
    p.add_argument("--reingest", action="store_true", help="re-extract and upsert even if already ingested")
    return p


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    targets = _parse_targets(args.targets)
    manifest = ingest_from_github(args.url, ref=args.ref, targets=targets, reingest=args.reingest)
    print(manifest.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
