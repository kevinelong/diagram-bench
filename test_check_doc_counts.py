#!/usr/bin/env python
"""test_check_doc_counts.py - the counts gate read against transcripts built here, so no suite is run.

    python test_check_doc_counts.py

`check_doc_counts.py` sums one summary line per suite. Since 0.32.1 a suite may append a fourth part,
`209 checks: 205 passed, 0 failed, 4 skipped`, for the checks it asked and this box could not answer - and a skipped
check measured nothing, so it is not a pass, while nothing was found wrong, so it is not a failure either.

So the gate has to read both shapes (a transcript saved before the fourth part still has to parse), has to keep the
skip out of `passed`, and has to keep failing a run whose suite was red. Each check below hands `main()` a transcript
written here, with the document scan replaced by the fixtures given, so the answer does not depend on what the real
README says today.
"""

import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_doc_counts as gate  # noqa: E402

FAILS = []
CHECKS = [0]     # every check() that ran, so this suite can print its own size


def check(label, ok, detail=''):
    print('%-64s %s %s' % (label, 'PASS' if ok else 'FAIL', detail))
    CHECKS[0] += 1
    if not ok:
        FAILS.append(label)


# The three suites' own totals. GALLERY and TILES are in the shape they print today; SIZING is either shape, because
# `test_sizing.py` prints the fourth part only from 0.32.1 and a saved transcript from before it has three.
GALLERY = '1074 checks: 1074 passed, 0 failed'
SIZING_OLD = '209 checks: 208 passed, 1 failed'
SIZING_NEW = '209 checks: 205 passed, 0 failed, 4 skipped'
TILES = '28 checks: 28 passed, 0 failed'


def gate_run(lines, docs=(), code=0):
    """`main()` over `lines` as the suites' transcript, with the suites and the document scan replaced.

    `code` is what a suite exited with; `docs` is `[(full path, label)]` in the shape `documents()` returns.
    """
    real_docs, real_run = gate.documents, gate.run_suites
    gate.documents = lambda: list(docs)
    gate.run_suites = lambda: (list(lines), code)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = gate.main([])
    finally:
        gate.documents, gate.run_suites = real_docs, real_run
    return rc, out.getvalue()


def doc_with(tmp: str, text: str) -> list[tuple[str, str]]:
    path = Path(tmp) / 'STATE.md'
    path.write_text(text, encoding='utf-8')
    return [(str(path), 'docs/STATE.md')]


def main():
    # ---- the parser: both shapes, and skips as their own quantity -------------------------------------------------
    totals = gate.read_totals([GALLERY, SIZING_NEW, TILES])
    check('the four-part total is read, and its skips are counted apart',
          (totals['checks'], totals['passed'], totals['failed'], totals['skipped'], totals['suites'])
          == (1311, 1307, 0, 4, 3), totals)

    totals = gate.read_totals([GALLERY, SIZING_OLD, TILES])
    check('and a transcript in the older three-part shape still parses, claiming no skips',
          (totals['checks'], totals['passed'], totals['failed'], totals['skipped'], totals['suites'])
          == (1311, 1310, 1, 0, 3), totals)

    totals = gate.read_totals([GALLERY, SIZING_NEW + '  ', '  ' + TILES])
    check('and whitespace around a summary line does not make a suite disappear',
          (totals['skipped'], totals['suites']) == (4, 3), totals)

    # ---- a run with skips: the fourth part is printed beside the totals, and nothing is compared -----------------
    rc, out = gate_run([GALLERY, SIZING_NEW, TILES])
    check('a run whose checks were skipped exits 0 and reports the skip count',
          rc == 0 and 'NOTICE: no document states a count' in out
          and '1311 checks: 1307 passed, 0 failed, 4 skipped (from 3 suites)' in out, out.strip().splitlines()[-1:])

    # ---- and a skip is neither a pass nor a failure, which is what the claims are held to ------------------------
    with tempfile.TemporaryDirectory(prefix='doc-counts-') as tmp:
        docs = doc_with(tmp, 'the three window suites are 1311 checks, 1307 passing\n')
        rc, out = gate_run([GALLERY, SIZING_NEW, TILES], docs=docs)
        check('a document claiming the passing count agrees when four checks were skipped',
              rc == 0 and 'all 2 count claims agree' in out and '4 skipped' in out, out.strip().splitlines()[-1:])

        docs = doc_with(tmp, 'the three window suites are 1311 passing\n')
        rc, out = gate_run([GALLERY, SIZING_NEW, TILES], docs=docs)
        check('and a document that counts the skipped checks as passing is stale',
              rc == 1 and 'MEASURED 1307' in out, [ln for ln in out.splitlines() if 'MEASURED' in ln])

    # ---- a red suite fails the run whatever the numbers say, and an unparsed suite is not a silent pass ---------
    rc, out = gate_run([GALLERY, SIZING_OLD, TILES], code=1)
    check('a suite that exited non-zero still fails the run', rc == 1, rc)

    rc, out = gate_run([GALLERY, TILES])
    check('and a suite that printed no total fails it too, naming how many were summed',
          rc == 1 and '2 of 3 suites printed a total' in out, [ln for ln in out.splitlines() if 'suites printed' in ln])

    print('%d checks: %d passed, %d failed' % (CHECKS[0], CHECKS[0] - len(FAILS), len(FAILS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
