"""check_doc_counts.py - do the documents agree with the suites they describe?

The three window suites each print their own total (`test_gallery_ui.py`, `test_sizing.py`,
`test_tiles.py` end with `<N> checks: <passed> passed, <failed> failed`). This runs them - once
each, no suite twice - adds those three totals, then reads every check count the documents state
and fails naming the file, the line, the number claimed and the number measured.

Usage (cmd.exe, per README's interpreter quirk):

  MSYS_NO_PATHCONV=1 cmd.exe /c "C:\\Users\\kevin\\ai_bench\\image-models-20261020\\.venv\\Scripts\\python.exe check_doc_counts.py"
  ... check_doc_counts.py --test-output logs\\suites-2026-11-14.log   # parse a run, make none
  ... check_doc_counts.py --quiet                                     # only what disagrees

Exit 0 = every claim agrees. Exit 1 = a claim disagrees, a suite failed or printed no total, or no
document could be read. Exit 0 with a NOTICE = **no document states a count yet** (this repository's
state today): there is nothing to compare, and the notice names the file that most needs the line.

WHY THIS IS A GATE AND NOT A HABIT. Three stale numbers reached a reader in three days in sibling
repositories: a README at 342 when the suite was 509, a course repo's review at 105 when it was 168,
and a third project quoting counts that had drifted the same way. Each was caught by running the
instrument, never by reading more carefully. The counts in this repository live in dated records
(`PIN-20261021.md`: "484 checks", `STATE-2026-11-02-4060.md`: "182 passes / 11 failures") and are
true of the day they were written - so the gate reads the documents that describe the tree you have
(`README.md`, `docs/*.md`, `CONTINUE-HERE.md` if it exists) and not the dated logs.

WHAT IS READ: `N checks`, `N tests`, `N passing`, `the suite is N`. A number inside quotes or
backticks is an explanation, not a claim, and is skipped; so is a line from a suite's own transcript
(a `PASS`/`FAIL` row, or `N checks: ...`). `docs/*.md` is read whole, minus any file listed in SKIP -
whose reason is printed - so the search never narrows silently.

When --test-output is given, the file holds the three suites' transcripts concatenated (what the
README tells you to save), and no suite is run at all.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winproc  # noqa: E402  - this repository runs every child process through winproc, and its suite says so

ROOT = os.path.dirname(os.path.abspath(__file__))

# The suites, in the order the README lists them, and what each one owns.
RUNNERS = (
    ('test_gallery_ui.py', 'the gallery window'),
    ('test_sizing.py', 'the four columns and their boundaries'),
    ('test_tiles.py', 'the tiles and the prompt grouping'),
)

# Each suite's own total, printed by the suite: "52 checks: 52 passed, 0 failed".
TOTAL = re.compile(r'^(?P<checks>\d+)\s+checks:\s+(?P<passed>\d+)\s+passed,\s+(?P<failed>\d+)\s+failed\s*$')

CLAIMS = (
    # `N checks` - but not "6 check categories", where `check` is an adjective
    ('checks', re.compile(r'(?<![\w/.-])(?P<n>\d{1,4})\s+checks?\b(?!\s+[a-z])')),
    ('tests', re.compile(r'(?<![\w/.-])(?P<n>\d{1,4})\s+tests?\b')),
    # `N passing` - not "3/3 passing", which counts runs of one test
    ('passing', re.compile(r'(?<![\w/.-])(?P<n>\d{1,4})\s+passing\b')),
    ('suite', re.compile(r'[Tt]he suite is\s+(?P<n>\d{1,4})\b')),
)

DOCS = ('README.md', 'CONTINUE-HERE.md')

# Documents under docs/ that are records of a day, not statements about this tree.
SKIP = {
    'docs/BUGS-20261030-tile-size.md': 'a dated bug report - its 883 checks is what it measured on 2026-10-30',
}


def quoted(line: str, index: int) -> bool:
    # Only a double quote and a backtick. An apostrophe is not a quotation mark here - this repository's prose
    # is full of them ("the documents' counts") and counting them made a claim on such a line invisible, which
    # is a silent miss: the gate would pass a stale number it never read.
    for mark in ('"', '`'):
        if line[:index].count(mark) % 2 == 1:
            return True
    return False


def transcript(line: str) -> bool:
    """A suite's own row is a measurement, not a claim in prose."""
    stripped = line.strip()
    if TOTAL.match(stripped):
        return True
    return stripped.endswith('PASS') or stripped.endswith('FAIL') or ' PASS ' in stripped or ' FAIL ' in stripped


def read_totals(lines: list[str]) -> dict:
    totals = {'checks': 0, 'passed': 0, 'failed': 0, 'suites': 0}
    for line in lines:
        match = TOTAL.match(line.strip())
        if not match:
            continue
        totals['checks'] += int(match.group('checks'))
        totals['passed'] += int(match.group('passed'))
        totals['failed'] += int(match.group('failed'))
        totals['suites'] += 1
    return totals


def run_suites() -> tuple[list[str], int]:
    lines: list[str] = []
    code = 0
    for script, what in RUNNERS:
        path = os.path.join(ROOT, script)
        print(f'running {script} ({what}) ...', flush=True)
        proc = winproc.run([sys.executable, path], cwd=ROOT, capture_output=True, text=True)
        lines.extend((proc.stdout or '').splitlines())
        lines.extend((proc.stderr or '').splitlines())
        if proc.returncode != 0:
            code = 1
            print(f'  {script} exited {proc.returncode}; last lines:')
            for line in (proc.stdout or '').splitlines()[-6:]:
                print(f'    {line}')
    return lines, code


def documents() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for name in DOCS:
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            found.append((path, name))
    for path in sorted(glob.glob(os.path.join(ROOT, 'docs', '*.md'))):
        label = 'docs/' + os.path.basename(path)
        if label in SKIP:
            continue
        found.append((path, label))
    return found


def claims_in(path: str, label: str) -> list[dict]:
    found: list[dict] = []
    with open(path, encoding='utf-8') as handle:
        for number, line in enumerate(handle, 1):
            if transcript(line):
                continue
            for kind, pattern in CLAIMS:
                for match in pattern.finditer(line):
                    if quoted(line, match.start()):
                        continue
                    found.append({
                        'file': label,
                        'line': number,
                        'kind': kind,
                        'claim': int(match.group('n')),
                        'text': line.strip(),
                    })
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--test-output', help='parse this saved transcript instead of running the suites')
    parser.add_argument('--save', help='write the transcript the totals were read from to this file')
    parser.add_argument('--quiet', action='store_true', help='print only what disagrees')
    args = parser.parse_args(argv)

    if args.test_output:
        if not os.path.exists(args.test_output):
            print(f'doc-counts: no such test output: {args.test_output}')
            return 1
        with open(args.test_output, encoding='utf-8', errors='replace') as handle:
            lines = handle.read().splitlines()
        code = 0
    else:
        lines, code = run_suites()

    if args.save:
        os.makedirs(os.path.dirname(os.path.abspath(args.save)) or '.', exist_ok=True)
        with open(args.save, 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(lines) + '\n')
        print('transcript: %s' % args.save)

    totals = read_totals(lines)
    if totals['suites'] != len(RUNNERS):
        print(f"doc-counts: {totals['suites']} of {len(RUNNERS)} suites printed a total, so the sum is not "
              f'the suite\'s size - a suite that crashed prints none')
        return 1
    if not args.quiet:
        print()
        print('measured: %d checks: %d passed, %d failed (from %d suites)' % (
            totals['checks'], totals['passed'], totals['failed'], totals['suites']))
        print()

    docs = documents()
    found: list[dict] = []
    for path, label in docs:
        found.extend(claims_in(path, label))

    if not found:
        print('NOTICE: no document states a count, so there is nothing to compare yet.')
        print('        Read %d: %s' % (len(docs), ', '.join(label for _, label in docs)))
        if SKIP:
            print('        Skipped, as records of a day: %s' % ', '.join(sorted(SKIP)))
        print('        The suite is %d checks (%d passed, %d failed) as measured above.'
              % (totals['checks'], totals['passed'], totals['failed']))
        print('        docs/STATE.md is the file that most needs the line: it is the durable record of')
        print('        what the tests enforce, it is edited rather than appended to, and it names the suites')
        print('        without ever saying how big they are. One sentence there - "the three window suites')
        print('        are %d checks" - makes this gate live on the next run.' % totals['checks'])
        return 1 if code else 0

    measured = {kind: totals['checks'] for kind in ('checks', 'tests', 'suite')}
    measured['passing'] = totals['passed']
    stale: list[dict] = []
    for claim in found:
        want = measured[claim['kind']]
        ok = claim['claim'] == want
        if not ok:
            stale.append({**claim, 'measured': want})
        if args.quiet and ok:
            continue
        where = f"{claim['file']}:{claim['line']}"
        if ok:
            print(f"{where:<30} claims {claim['claim']:>4} ({claim['kind']:<7}) ok")
        else:
            print(f"{where:<30} claims {claim['claim']:>4} ({claim['kind']:<7}) - MEASURED {want} "
                  f"({claim['claim'] - want:+d})")
            print(f"{'':<30}   line: {claim['text']}")

    print()
    if not stale and not code:
        print('all %d count claims agree with the suites: %d checks (%d passed, %d failed)' % (
            len(found), totals['checks'], totals['passed'], totals['failed']))
        return 0
    if stale:
        print('stale count in %d of %d claims. The measured numbers are: %d checks, %d passing' % (
            len(stale), len(found), totals['checks'], totals['passed']))
        for claim in stale:
            print('  %s:%d' % (claim['file'], claim['line']))
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
