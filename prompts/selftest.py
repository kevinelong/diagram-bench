#!/usr/bin/env python3
"""Acceptance test for the prompt suite: known-good and known-bad fixtures.

Run from the repo root:

    python prompts/selftest.py          (also: python -m prompts.selftest)

What is asserted, and why:

1. **Hash lock.**  The prompts of v1..v4 are frozen.  Their SHA-256s are
   hard-coded below, taken from the files as committed.  If anyone edits an old
   prompt, this fails loudly instead of silently rewriting history.
2. **v5 is v4 plus one thing.**  The prompt equals v4's with exactly one clause
   inserted, the eleven old checks are unchanged and in order, and exactly one
   check (``cue_butt_side``) is new.
3. **The diff reports that.**  ``diff(4, 5)`` = one added check, nothing
   removed, prompt changed; ``diff(3, 4)`` = no check-id change, prompt changed.
4. **Synthetic fixtures.**  A known-good pair (v7/v8) and eight known-bad files
   (missing, empty, corrupt, duplicate, rule-less, bad date, bad version, absent
   root) prove the loader and diff work on data whose right answer is known by
   construction, including that they *reject* the bad cases.
5. **The CLI.**  ``--list``, ``--diff`` and ``--export`` are run as real
   subprocesses and their exit codes and text are checked.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prompts import suite  # noqa: E402

PY = sys.executable
CLI = ROOT / "prompt_matrix.py"

# SHA-256 of each frozen prompt string, exactly as stored in the committed file.
# v1 lives in hard-task-pooltable.json (it predates the -vN suffix).
FROZEN_PROMPTS = {
    1: "77b4e985f542ada18abb2797d546cc730a65d799f987209cd8a3450f56087d97",
    2: "5b04d86015f53d922dbea71c8f47d72eed49a62a75415a2942685ec01117096e",
    3: "ce9aa8be9efa6cc0c6025c5a19bb4f0bd334a796461c72ccf4434a02d9e3dbf4",
    4: "5ecdcb772a772211b44a5767c5c0166132a19831e37ad2a96e6ffaa66e4e41b0",
    5: "89cefa839b016f6dc53b734a99b6af88e00a97e1725d4754ebb591f580ab0615",    6: "2d94a80743b47052697c20d4f32bdfad798f004b91cbf04676183a1fa291db6b",
}

CUE_OLD = "its butt points toward the head short rail."
CUE_NEW = (
    "its butt points toward the head short rail, with the butt lying on the head side of the cue ball, "
    "so the butt's x coordinate is less than 25."
)

v4_check_ids = (
    "playfield_2to1",
    "corners",
    "sides",
    "diamonds_long",
    "spots",
    "rack_apex",
    "rack_equilateral",
    "back_row_parallel",
    "balls_inside",
    "cue_angle",
    "label_CUE",
)


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class Failed(AssertionError):
    pass


def check(condition, message):
    if not condition:
        raise Failed(message)


def check_equal(actual, expected, what):
    if actual != expected:
        raise Failed(f"{what}: expected {expected!r}, got {actual!r}")


def expect_error(fn, contains, what):
    try:
        fn()
    except suite.PromptSuiteError as exc:
        check(
            contains.lower() in str(exc).lower(),
            f"{what}: error message {str(exc)!r} does not contain {contains!r}",
        )
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - wrong exception type is a failure
        raise Failed(f"{what}: raised {type(exc).__name__} instead of PromptSuiteError: {exc}") from exc
    raise Failed(f"{what}: expected PromptSuiteError, nothing was raised")


# --------------------------------------------------------------------------- #
# fixture helpers
# --------------------------------------------------------------------------- #
def task(prompt="A table.", declared="2026-10-21", checks=None, **extra):
    payload = {
        "declared": declared,
        "frozen": True,
        "prompt": prompt,
        "checks": checks
        if checks is not None
        else [{"id": "one", "rule": "the first check"}, {"id": "two", "rule": "the second check"}],
    }
    payload.update(extra)
    return payload


def write_fixture(root, name, payload):
    path = Path(root) / name
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")
    return path


def run_cli(*args, cwd=ROOT):
    return subprocess.run(
        [PY, str(CLI), *map(str, args)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# 1. hash lock on the frozen prompts
# --------------------------------------------------------------------------- #
@test
def frozen_prompt_hashes_unchanged():
    for version, expected in FROZEN_PROMPTS.items():
        loaded = suite.load(version)
        check_equal(loaded.prompt_sha256, expected, f"v{version} prompt hash ({loaded.file})")
        check_equal(loaded.file, "hard-task-pooltable.json" if version == 1 else f"hard-task-pooltable-v{version}.json", f"v{version} source file")
        check(loaded.prompt.strip() != "", f"v{version} prompt is empty")
    print("    v1..v5 prompt sha256 match the committed files")


@test
def frozen_prompt_hashes_are_recomputed_not_trusted():
    # the pinned values must agree with an independent sha256 of the raw text
    import hashlib

    for version, expected in FROZEN_PROMPTS.items():
        raw = json.loads(suite.path_for(version).read_text(encoding="utf-8"))["prompt"]
        check_equal(hashlib.sha256(raw.encode("utf-8")).hexdigest(), expected, f"v{version} independent sha256")


# --------------------------------------------------------------------------- #
# 2. v5 = v4 plus exactly one constraint and one check
# --------------------------------------------------------------------------- #
@test
def v5_is_v4_plus_one_constraint():
    v4 = suite.load(4)
    v5 = suite.load(5)

    check_equal(list(v5.data.keys()), list(v4.data.keys()), "v5 top-level key order")
    check_equal(v5.data.get("supersedes"), "hard-task-pooltable-v4.json", "v5 supersedes")
    check_equal(v5.declared, v4.declared, "v5 declared date")
    check_equal(v5.declared, "2026-10-21", "declared date")

    # prompt = v4's prompt with exactly one clause added
    check(v5.prompt != v4.prompt, "v5 prompt is identical to v4's")
    check_equal(v4.prompt.count(CUE_OLD), 1, "v4 cue sentence count")
    check_equal(v5.prompt, v4.prompt.replace(CUE_OLD, CUE_NEW), "v5 prompt text")
    check_equal(
        len(v5.prompt) - len(v4.prompt),
        len(CUE_NEW) - len(CUE_OLD),
        "v5 prompt length delta (only the new clause)",
    )
    check("x coordinate is less than 25" in v5.prompt, "v5 prompt does not state the butt x < 25 constraint")
    check_equal(v5.prompt.count("less than 25"), 1, "constraint mentioned exactly once")

    # checks: the eleven v4 checks unchanged and in order, then exactly one new one
    check_equal(len(v5.checks), 12, "v5 check count")
    check_equal(v5.check_ids, v4_check_ids + ("cue_butt_side",), "v5 check ids")
    check_equal([c.rule for c in v5.checks[:11]], [c.rule for c in v4.checks], "v5 inherited check rules")
    check_equal(v4.check_ids, v4_check_ids, "v4 check ids")
    new_rule = v5.rules()["cue_butt_side"]
    check("x < 25" in new_rule, f"cue_butt_side does not assert x < 25: {new_rule!r}")
    check("head side" in new_rule, "cue_butt_side does not name the head side")

    # nothing else moved
    for key in v4.data:
        if key in {"declared", "supersedes", "why", "prompt", "checks"}:
            continue
        check_equal(v5.data[key], v4.data[key], f"v5 field {key!r} unchanged")
    print("    v5 prompt = v4 prompt + 1 clause; 11 checks inherited, cue_butt_side added")


# --------------------------------------------------------------------------- #
# 3. diffs between the real versions
# --------------------------------------------------------------------------- #
@test
def diff_4_5_is_one_added_check_and_a_changed_prompt():
    d = suite.diff(4, 5)
    check_equal(d["added"], ["cue_butt_side"], "diff(4,5) added")
    check_equal(d["removed"], [], "diff(4,5) removed")
    check_equal(d["prompt_changed"], True, "diff(4,5) prompt_changed")
    check_equal((d["from_n_checks"], d["to_n_checks"]), (11, 12), "diff(4,5) check counts")
    check_equal(d["added_checks"][0]["id"], "cue_butt_side", "diff(4,5) added check record")
    check("x < 25" in d["added_checks"][0]["rule"], "diff(4,5) added rule lost its text")
    check_equal(len(d["unchanged_check_ids"]), 11, "diff(4,5) unchanged ids")


@test
def diff_3_4_changes_only_the_prompt():
    d = suite.diff(3, 4)
    check_equal((d["added"], d["removed"]), ([], []), "diff(3,4) check-id change")
    check_equal(d["prompt_changed"], True, "diff(3,4) prompt_changed")
    check_equal((d["from_n_checks"], d["to_n_checks"]), (11, 11), "diff(3,4) check counts")


@test
def diff_1_2_is_a_full_rewrite():
    d = suite.diff(1, 2)
    check_equal(d["prompt_changed"], True, "diff(1,2) prompt_changed")
    check_equal(len(d["added"]), 7, "diff(1,2) added count")
    check_equal(len(d["removed"]), 6, "diff(1,2) removed count")
    check_equal(d["added"], sorted(d["added"]), "diff(1,2) added is sorted")
    check("table_2to1" in d["added"], "diff(1,2) added misses table_2to1")
    check("aspect" in d["removed"], "diff(1,2) removed misses aspect")


@test
def diff_is_directional():
    forward = suite.diff(4, 5)
    backward = suite.diff(5, 4)
    check_equal(backward["added"], forward["removed"], "diff(5,4) added")
    check_equal(backward["removed"], forward["added"], "diff(5,4) removed")
    check_equal(backward["prompt_changed"], True, "diff(5,4) prompt_changed")


# --------------------------------------------------------------------------- #
# 4. manifest
# --------------------------------------------------------------------------- #
@test
def manifest_covers_v1_to_v5():
    entries = suite.manifest()
    check_equal([e["version"] for e in entries], [1, 2, 3, 4, 5, 6], "manifest versions")
    expected_keys = {"version", "file", "declared", "prompt_sha256", "n_checks", "check_ids"}
    for entry in entries:
        check_equal(set(entry), expected_keys, f"manifest v{entry['version']} keys")
        check_equal(entry["declared"], "2026-10-21", f"manifest v{entry['version']} date")
        check_equal(entry["prompt_sha256"], FROZEN_PROMPTS[entry["version"]], f"manifest v{entry['version']} sha")
        check_equal(entry["n_checks"], len(entry["check_ids"]), f"manifest v{entry['version']} check count")
        check_equal(entry["check_ids"], sorted(entry["check_ids"]), f"manifest v{entry['version']} ids sorted")
        check(len(entry["check_ids"]) == len(set(entry["check_ids"])), f"manifest v{entry['version']} ids unique")
    by_version = {e["version"]: e for e in entries}
    check_equal(by_version[6]["n_checks"], 12, "manifest v6 check count")
    check_equal(by_version[5]["n_checks"], 12, "manifest v5 check count")
    check_equal(by_version[4]["n_checks"], 11, "manifest v4 check count")
    check_equal(by_version[1]["file"], "hard-task-pooltable.json", "manifest v1 file")
    check_equal(by_version[5]["file"], "hard-task-pooltable-v5.json", "manifest v5 file")
    print("    manifest: v1..v5, counts 6/7/11/11/12, hashes match")


# --------------------------------------------------------------------------- #
# 5. synthetic known-good fixtures
# --------------------------------------------------------------------------- #
@test
def synthetic_pair_loads_and_diffs_as_constructed():
    with tempfile.TemporaryDirectory() as tmp:
        write_fixture(tmp, "hard-task-pooltable.json", task("legacy prompt", checks=[{"id": "a", "rule": "check a"}]))
        write_fixture(
            tmp,
            "hard-task-pooltable-v7.json",
            task("middle prompt", checks=[{"id": "a", "rule": "check a"}, {"id": "b", "rule": "check b"}]),
        )
        write_fixture(
            tmp,
            "hard-task-pooltable-v8.json",
            task(
                "middle prompt",
                checks=[
                    {"id": "a", "rule": "check a"},
                    {"id": "b", "rule": "check b"},
                    {"id": "c", "rule": "check c"},
                ],
            ),
        )
        # an unrelated file in the same directory must be ignored
        write_fixture(tmp, "notes.json", {"prompt": "not a version", "checks": []})

        check_equal(suite.versions_present(tmp), [1, 7, 8], "fixture versions_present")
        check_equal([e["version"] for e in suite.manifest(tmp)], [1, 7, 8], "fixture manifest versions")
        check_equal(suite.load(7, tmp).file, "hard-task-pooltable-v7.json", "fixture load file")
        check_equal(suite.load(1, tmp).file, "hard-task-pooltable.json", "fixture legacy v1 file")

        same_prompt = suite.diff(7, 8, tmp)
        check_equal(same_prompt["added"], ["c"], "fixture diff(7,8) added")
        check_equal(same_prompt["removed"], [], "fixture diff(7,8) removed")
        check_equal(same_prompt["prompt_changed"], False, "fixture diff(7,8) prompt_changed")

        rewrite = suite.diff(1, 7, tmp)
        check_equal(rewrite["added"], ["b"], "fixture diff(1,7) added")
        check_equal(rewrite["removed"], [], "fixture diff(1,7) removed")
        check_equal(rewrite["prompt_changed"], True, "fixture diff(1,7) prompt_changed")
    print("    synthetic v1/v7/v8: diff added/removed/prompt-change all as constructed")


@test
def explicit_v1_file_wins_over_legacy():
    with tempfile.TemporaryDirectory() as tmp:
        write_fixture(tmp, "hard-task-pooltable.json", task("legacy text"))
        write_fixture(tmp, "hard-task-pooltable-v1.json", task("suffixed text"))
        check_equal(suite.load(1, tmp).prompt, "suffixed text", "explicit v1 precedence")
        check_equal(
            [str(p.relative_to(tmp)) for p in suite.candidate_paths(1, tmp)],
            ["hard-task-pooltable-v1.json", "hard-task-pooltable.json"],
            "candidate order for v1",
        )


# --------------------------------------------------------------------------- #
# 6. synthetic known-bad fixtures
# --------------------------------------------------------------------------- #
@test
def missing_version_file_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        write_fixture(tmp, "hard-task-pooltable-v2.json", task())
        expect_error(lambda: suite.load(3, tmp), "missing", "load of a missing version")
        expect_error(lambda: suite.diff(2, 3, tmp), "missing", "diff against a missing version")
        check_equal(suite.manifest(tmp)[0]["version"], 2, "manifest still lists what is present")


@test
def empty_prompt_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        write_fixture(tmp, "hard-task-pooltable-v2.json", task(prompt=""))
        expect_error(lambda: suite.load(2, tmp), "empty", "empty prompt")
        expect_error(lambda: suite.manifest(tmp), "empty", "manifest surfaces an empty prompt")
        write_fixture(tmp, "hard-task-pooltable-v2.json", task(prompt="   \n\t "))
        expect_error(lambda: suite.load(2, tmp), "empty", "whitespace-only prompt")
        write_fixture(tmp, "hard-task-pooltable-v2.json", task(prompt=["not", "text"]))
        expect_error(lambda: suite.load(2, tmp), "must be a string", "non-string prompt")


@test
def corrupt_json_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        write_fixture(tmp, "hard-task-pooltable-v2.json", '{"prompt": "x", "checks": [ }')
        expect_error(lambda: suite.load(2, tmp), "invalid json", "corrupt JSON")
        write_fixture(tmp, "hard-task-pooltable-v2.json", '["not", "an", "object"]')
        expect_error(lambda: suite.load(2, tmp), "expected a json object", "JSON array instead of object")


@test
def bad_checks_are_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "hard-task-pooltable-v2.json"
        cases = [
            ({"checks": []}, "checks' is empty"),
            ({"checks": "nope"}, "must be a list"),
            ({"checks": [{"id": "a", "rule": "r"}, {"id": "a", "rule": "r2"}]}, "duplicate"),
            ({"checks": [{"id": "a"}]}, "'rule'"),
            ({"checks": [{"id": "", "rule": "r"}]}, "no string 'id'"),
            ({"checks": ["a"]}, "not an object"),
        ]
        base = task()
        for overrides, contains in cases:
            payload = dict(base)
            payload.update(overrides)
            path.write_text(json.dumps(payload), encoding="utf-8")
            expect_error(lambda: suite.load(2, tmp), contains, f"checks fixture {contains}")


@test
def bad_declared_date_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        write_fixture(tmp, "hard-task-pooltable-v2.json", task(declared="2026-13-40"))
        expect_error(lambda: suite.load(2, tmp), "not a real date", "impossible date")
        write_fixture(tmp, "hard-task-pooltable-v2.json", task(declared="21/10/2026"))
        expect_error(lambda: suite.load(2, tmp), "'declared'", "wrong date format")
        payload = task()
        payload.pop("declared")
        write_fixture(tmp, "hard-task-pooltable-v2.json", payload)
        expect_error(lambda: suite.load(2, tmp), "'declared'", "missing date")


@test
def bad_version_arguments_are_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        write_fixture(tmp, "hard-task-pooltable-v2.json", task())
        expect_error(lambda: suite.load("abc", tmp), "integer", "non-numeric version")
        expect_error(lambda: suite.load(0, tmp), ">= 1", "zero version")
        expect_error(lambda: suite.load(True, tmp), "integer", "boolean version")
        expect_error(lambda: suite.versions_present(Path(tmp) / "nope"), "does not exist", "absent root")
        check_equal(suite.manifest(tmp)[0]["version"], 2, "string-loaded fixture still healthy")


@test
def empty_root_manifests_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        check_equal(suite.versions_present(tmp), [], "empty root versions")
        check_equal(suite.manifest(tmp), [], "empty root manifest")


# --------------------------------------------------------------------------- #
# 7. the CLI, run as a subprocess
# --------------------------------------------------------------------------- #
@test
def cli_list_renders_v1_to_v5():
    proc = run_cli("--list")
    check_equal(proc.returncode, 0, f"--list exit code (stderr: {proc.stderr.strip()})")
    out = proc.stdout
    check("version" in out and "checks" in out and "prompt sha256" in out, "--list header missing")
    table = out.split("\n\n")[0].splitlines()
    data_rows = table[2:]  # header + rule, then one row per version
    check_equal(len(data_rows), 6, f"--list rendered {len(data_rows)} version rows")
    for version in range(1, 7):
        fields = data_rows[version - 1].split()
        check_equal(fields[0], str(version), f"--list row order at v{version}")
        check_equal(fields[1], "2026-10-21", f"--list row v{version} date")
        check(str(len(suite.load(version).checks)) == fields[2], f"--list row v{version} check count")
        check(FROZEN_PROMPTS[version] in data_rows[version - 1], f"--list row v{version} misses its sha")
    check("6 versions" in out, "--list summary line missing")
    print("    --list: 5 rows, dates, check counts and prompt hashes")


@test
def cli_diff_shows_one_added_check_and_a_changed_prompt():
    proc = run_cli("--diff", 4, 5)
    check_equal(proc.returncode, 0, f"--diff 4 5 exit code (stderr: {proc.stderr.strip()})")
    out = proc.stdout
    check("diff v4 -> v5" in out, "--diff heading missing")
    check("prompt: changed" in out, "--diff does not report a changed prompt")
    check("checks: 11 -> 12 (1 added, 0 removed, 11 unchanged)" in out, "--diff check counts wrong")
    check("added (1):" in out, "--diff added count wrong")
    check("+ cue_butt_side" in out, "--diff does not name cue_butt_side")
    check("removed (0):" in out, "--diff removed count wrong")
    print("    --diff 4 5: 'prompt: changed', '1 added, 0 removed', + cue_butt_side")


@test
def cli_export_writes_the_raw_prompt():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "nested" / "p5.txt"  # parent does not exist yet
        proc = run_cli("--export", 5, target)
        check_equal(proc.returncode, 0, f"--export 5 exit code (stderr: {proc.stderr.strip()})")
        check(target.is_file(), "--export did not create the file")
        payload = target.read_bytes()
        check(len(payload) > 0, "--export wrote an empty file")
        check_equal(payload, suite.load(5).prompt.encode("utf-8"), "exported bytes")
        check(FROZEN_PROMPTS[5] in proc.stdout, "--export did not report the prompt sha")

        legacy = Path(tmp) / "p1.txt"
        proc = run_cli("--export", 1, legacy)
        check_equal(proc.returncode, 0, f"--export 1 exit code (stderr: {proc.stderr.strip()})")
        check_equal(legacy.read_bytes(), suite.load(1).prompt.encode("utf-8"), "exported v1 bytes")
    print("    --export 5: non-empty, byte-identical to the stored prompt, parents created")


@test
def cli_rejects_bad_arguments():
    proc = run_cli("--diff", 4, 99)
    check_equal(proc.returncode, 2, "--diff with a missing version should exit 2")
    check("missing" in proc.stderr.lower(), f"--diff stderr does not explain the miss: {proc.stderr!r}")

    proc = run_cli("--export", 99, "unused.txt")
    check_equal(proc.returncode, 2, "--export with a missing version should exit 2")

    proc = run_cli()
    check_equal(proc.returncode, 2, "no arguments should exit 2")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    failures = []
    for fn in TESTS:
        name = fn.__name__
        try:
            print(f"[ RUN  ] {name}")
            fn()
        except Failed as exc:
            failures.append((name, str(exc)))
            print(f"[ FAIL ] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"[ FAIL ] {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"[  OK  ] {name}")

    total = len(TESTS)
    print()
    if failures:
        print(f"FAILED {len(failures)}/{total} acceptance checks")
        for name, message in failures:
            print(f"  - {name}: {message}")
        return 1
    print(f"PASS {total}/{total} acceptance checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
