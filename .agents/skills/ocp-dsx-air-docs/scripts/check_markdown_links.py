#!/usr/bin/env python3
"""Check that local Markdown link targets exist."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

LINK = re.compile(r"!?\[[^\]]*\]\((?P<target><[^>]+>|[^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")
EXTERNAL_SCHEMES = ("http://", "https://", "mailto:", "data:")


def markdown_files(arguments: list[str]) -> list[Path]:
    paths = [Path(argument) for argument in arguments] if arguments else [Path("README.md"), Path("docs")]
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.md")))
        elif path.suffix == ".md":
            files.append(path)
    return files


def missing_links(path: Path) -> list[str]:
    failures: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for match in LINK.finditer(line):
            target = match["target"].removeprefix("<").removesuffix(">")
            if not target or target.startswith("#") or target.startswith(EXTERNAL_SCHEMES):
                continue
            file_target = unquote(target.split("#", 1)[0])
            if file_target and not (path.parent / file_target).resolve().exists():
                failures.append(f"{path}:{line_number}: missing local target {file_target!r}")
    return failures


def main() -> int:
    files = markdown_files(sys.argv[1:])
    failures = [failure for path in files for failure in missing_links(path)]
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Checked local links in {len(files)} Markdown files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
