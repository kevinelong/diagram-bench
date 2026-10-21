"""The pool-table prompt ladder: file-backed loader, manifest and diff.

    from prompts import suite
    suite.manifest()          # one record per version present
    suite.diff(4, 5)          # added/removed check ids + prompt change
"""

from .suite import (  # noqa: F401
    LEGACY_FILE,
    LEGACY_VERSION,
    REPO_ROOT,
    Check,
    PromptSuiteError,
    Version,
    candidate_paths,
    diff,
    load,
    manifest,
    path_for,
    prompt_sha256,
    versions_present,
)

__all__ = [
    "Check",
    "LEGACY_FILE",
    "LEGACY_VERSION",
    "PromptSuiteError",
    "REPO_ROOT",
    "Version",
    "candidate_paths",
    "diff",
    "load",
    "manifest",
    "path_for",
    "prompt_sha256",
    "versions_present",
]
