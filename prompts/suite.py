"""Loader, manifest and diff for the frozen pool-table prompt ladder.

One *version* is one frozen task file:

    v1  hard-task-pooltable.json          (predates the suffix; kept under its original name)
    vN  hard-task-pooltable-v<N>.json     (N >= 2)

Every version file is a JSON object with at least ``declared``, ``prompt`` and
``checks`` (a list of ``{"id", "rule"}`` objects).  The prompt hash is the
SHA-256 of the prompt string exactly as stored, encoded UTF-8 - no
normalisation, no trailing-newline games - so that editing an old prompt is
detectable (``prompts/selftest.py`` pins v1..v4 to their committed hashes).

Nothing here draws, scores or executes an SVG; this module only answers
"which prompts exist, what do they say, and what changed between two of them".
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

__all__ = [
    "PromptSuiteError",
    "Check",
    "Version",
    "REPO_ROOT",
    "LEGACY_FILE",
    "LEGACY_VERSION",
    "prompt_sha256",
    "candidate_paths",
    "path_for",
    "versions_present",
    "load",
    "manifest",
    "diff",
]

REPO_ROOT = Path(__file__).resolve().parent.parent
LEGACY_FILE = "hard-task-pooltable.json"
LEGACY_VERSION = 1
VERSION_GLOB = "hard-task-pooltable-v*.json"
_VERSION_RE = re.compile(r"^hard-task-pooltable-v(\d+)\.json$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PromptSuiteError(Exception):
    """A prompt-suite file is missing, malformed or self-inconsistent."""


@dataclass(frozen=True)
class Check:
    id: str
    rule: str


@dataclass(frozen=True)
class Version:
    version: int
    file: str
    declared: str
    prompt: str
    prompt_sha256: str
    checks: tuple
    frozen: bool
    data: dict

    @property
    def n_checks(self) -> int:
        return len(self.checks)

    @property
    def check_ids(self) -> tuple:
        return tuple(c.id for c in self.checks)

    def rules(self) -> dict:
        return {c.id: c.rule for c in self.checks}

    def entry(self) -> dict:
        """The manifest record for this version."""
        return {
            "version": self.version,
            "file": self.file,
            "declared": self.declared,
            "prompt_sha256": self.prompt_sha256,
            "n_checks": self.n_checks,
            "check_ids": sorted(self.check_ids),
        }


def prompt_sha256(prompt: str) -> str:
    """SHA-256 of the prompt text as stored (UTF-8, byte-exact)."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _root(root=None) -> Path:
    return REPO_ROOT if root is None else Path(root)


def _coerce_version(version) -> int:
    if isinstance(version, bool):
        raise PromptSuiteError(f"version must be an integer, not {version!r}")
    if isinstance(version, int):
        n = version
    else:
        try:
            n = int(str(version).strip())
        except ValueError:
            raise PromptSuiteError(
                f"version must be an integer, not {version!r}"
            ) from None
    if n < 1:
        raise PromptSuiteError(f"version must be >= 1, not {n}")
    return n


def candidate_paths(version, root=None) -> tuple:
    """Every file name that may hold *version*, in priority order."""
    root = _root(root)
    n = _coerce_version(version)
    paths = [root / f"hard-task-pooltable-v{n}.json"]
    if n == LEGACY_VERSION:
        paths.append(root / LEGACY_FILE)
    return tuple(paths)


def path_for(version, root=None) -> Path:
    """The existing file holding *version*; raises if there is none."""
    root = _root(root)
    paths = candidate_paths(version, root)
    for path in paths:
        if path.is_file():
            return path
    tried = ", ".join(str(p.relative_to(root)) for p in paths)
    raise PromptSuiteError(
        f"missing prompt suite file for v{_coerce_version(version)} in {root}: tried {tried}"
    )


def versions_present(root=None) -> list:
    """Version numbers that have a task file, ascending (v1 includes the legacy name)."""
    root = _root(root)
    if not root.is_dir():
        raise PromptSuiteError(f"prompt suite root does not exist: {root}")
    found = set()
    for path in root.glob(VERSION_GLOB):
        match = _VERSION_RE.match(path.name)
        if match and path.is_file():
            found.add(int(match.group(1)))
    if LEGACY_VERSION not in found and (root / LEGACY_FILE).is_file():
        found.add(LEGACY_VERSION)
    return sorted(found)


def load(version, root=None) -> Version:
    """Read and validate one version.

    Raises :class:`PromptSuiteError` when the file is missing, is not JSON,
    has no usable ``declared`` date, has an empty prompt or has no / duplicate /
    malformed checks.
    """
    root = _root(root)
    n = _coerce_version(version)
    path = path_for(n, root)
    rel = path.relative_to(root).as_posix()

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptSuiteError(f"cannot read {rel}: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PromptSuiteError(f"{rel}: invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise PromptSuiteError(f"{rel}: expected a JSON object, got {type(data).__name__}")

    prompt = data.get("prompt")
    if not isinstance(prompt, str):
        raise PromptSuiteError(f"{rel}: 'prompt' must be a string")
    if not prompt.strip():
        raise PromptSuiteError(f"{rel}: prompt is empty")

    declared = data.get("declared")
    if not isinstance(declared, str) or not _DATE_RE.match(declared):
        raise PromptSuiteError(f"{rel}: 'declared' must be a YYYY-MM-DD date, got {declared!r}")
    try:
        date.fromisoformat(declared)
    except ValueError as exc:
        raise PromptSuiteError(f"{rel}: 'declared' is not a real date: {declared!r} ({exc})") from exc

    raw_checks = data.get("checks")
    if not isinstance(raw_checks, list):
        raise PromptSuiteError(f"{rel}: 'checks' must be a list")
    if not raw_checks:
        raise PromptSuiteError(f"{rel}: 'checks' is empty")

    checks = []
    seen = set()
    for index, item in enumerate(raw_checks):
        if not isinstance(item, dict):
            raise PromptSuiteError(f"{rel}: check #{index} is not an object")
        cid = item.get("id")
        # v1 predates the "rule" key and phrases the same thing under "q".
        rule = item.get("rule")
        if not isinstance(rule, str) or not rule.strip():
            rule = item.get("q")
        if not isinstance(cid, str) or not cid.strip():
            raise PromptSuiteError(f"{rel}: check #{index} has no string 'id'")
        if not isinstance(rule, str) or not rule.strip():
            raise PromptSuiteError(f"{rel}: check {cid!r} has no string 'rule' (or legacy 'q')")
        if cid in seen:
            raise PromptSuiteError(f"{rel}: duplicate check id {cid!r}")
        seen.add(cid)
        checks.append(Check(id=cid, rule=rule))

    return Version(
        version=n,
        file=rel,
        declared=declared,
        prompt=prompt,
        prompt_sha256=prompt_sha256(prompt),
        checks=tuple(checks),
        frozen=bool(data.get("frozen")),
        data=data,
    )


def manifest(root=None) -> list:
    """One record per version present, ascending.

    Each record carries: ``version``, ``file``, declared ``date`` (``declared``),
    ``prompt_sha256``, ``n_checks`` and the sorted set of ``check_ids``.
    """
    return [load(n, root).entry() for n in versions_present(root)]


def diff(a, b, root=None) -> dict:
    """What changed from version *a* to version *b*.

    ``added`` / ``removed`` are sorted check-id lists (with the matching rules
    under ``added_checks`` / ``removed_checks``); ``prompt_changed`` compares
    the byte-exact prompt hashes.
    """
    va = load(a, root)
    vb = load(b, root)
    ids_a, ids_b = set(va.check_ids), set(vb.check_ids)
    rules_a, rules_b = va.rules(), vb.rules()
    added = sorted(ids_b - ids_a)
    removed = sorted(ids_a - ids_b)
    return {
        "from": va.version,
        "to": vb.version,
        "from_file": va.file,
        "to_file": vb.file,
        "from_sha256": va.prompt_sha256,
        "to_sha256": vb.prompt_sha256,
        "prompt_changed": va.prompt_sha256 != vb.prompt_sha256,
        "from_n_checks": va.n_checks,
        "to_n_checks": vb.n_checks,
        "added": added,
        "removed": removed,
        "added_checks": [{"id": i, "rule": rules_b[i]} for i in added],
        "removed_checks": [{"id": i, "rule": rules_a[i]} for i in removed],
        "unchanged_check_ids": sorted(ids_a & ids_b),
    }
