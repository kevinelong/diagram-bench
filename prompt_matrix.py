#!/usr/bin/env python3
"""The pool-table prompt ladder on the command line.

    python prompt_matrix.py --list                 # versions x date x checks x prompt sha
    python prompt_matrix.py --diff 4 5             # checks added/removed, prompt changed?
    python prompt_matrix.py --export 5 out.txt     # write the raw prompt text

The table is built from :func:`prompts.suite.manifest`; nothing here reads a
file that the suite module did not validate first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prompts import suite  # noqa: E402


def _relative(path: Path) -> str:
    """Repo-relative when the path is in the repo, absolute otherwise."""
    try:
        return path.resolve().relative_to(suite.REPO_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _table(headers, rows) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    line = "  ".join("-" * w for w in widths)
    out = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip(), line]
    for row in rows:
        out.append("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)).rstrip())
    return "\n".join(out)


def cmd_list(args) -> int:
    entries = suite.manifest()
    if not entries:
        print(f"no prompt suite files in {suite.REPO_ROOT}", file=sys.stderr)
        return 1
    rows = [
        (e["version"], e["declared"], e["n_checks"], e["prompt_sha256"], e["file"])
        for e in entries
    ]
    print(_table(("version", "date", "checks", "prompt sha256", "file"), rows))
    print()
    print(
        f"{len(entries)} versions, "
        f"{entries[-1]['n_checks']} checks in the newest; "
        "sha256 is of the prompt string exactly as stored"
    )
    return 0


def cmd_diff(args) -> int:
    result = suite.diff(args.diff[0], args.diff[1])
    prompt = "changed" if result["prompt_changed"] else "unchanged"
    print(f"diff v{result['from']} -> v{result['to']}")
    print(f"  file:   {result['from_file']} -> {result['to_file']}")
    print(f"  prompt: {prompt}")
    print(f"    sha256 {result['from_sha256']} -> {result['to_sha256']}")
    print(
        f"  checks: {result['from_n_checks']} -> {result['to_n_checks']} "
        f"({len(result['added'])} added, {len(result['removed'])} removed, "
        f"{len(result['unchanged_check_ids'])} unchanged)"
    )
    print(f"  added ({len(result['added'])}):")
    for item in result["added_checks"]:
        print(f"    + {item['id']}  {item['rule']}")
    print(f"  removed ({len(result['removed'])}):")
    for item in result["removed_checks"]:
        print(f"    - {item['id']}  {item['rule']}")
    return 0


def cmd_export(args) -> int:
    version, target = args.export
    loaded = suite.load(version)
    out = Path(target)
    if out.parent and not out.parent.is_dir():
        out.parent.mkdir(parents=True, exist_ok=True)
    payload = loaded.prompt.encode("utf-8")
    out.write_bytes(payload)
    print(
        f"wrote {len(payload)} bytes to {_relative(out)} "
        f"(v{loaded.version} prompt, sha256 {loaded.prompt_sha256})"
    )
    if not out.read_bytes():
        raise suite.PromptSuiteError(f"{_relative(out)} is empty after writing")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prompt_matrix.py",
        description="List, diff and export the pool-table prompt ladder.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="table of versions, checks and prompt hashes")
    group.add_argument("--diff", nargs=2, metavar=("A", "B"), help="what changed from version A to version B")
    group.add_argument("--export", nargs=2, metavar=("VERSION", "OUTFILE"), help="write the raw prompt text")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.list:
            return cmd_list(args)
        if args.diff:
            return cmd_diff(args)
        return cmd_export(args)
    except suite.PromptSuiteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
