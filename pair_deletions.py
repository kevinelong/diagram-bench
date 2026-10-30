#!/usr/bin/env python
"""Which of the pictures in the Recycle Bin were deleted by an A/B elimination decision?

    python pair_deletions.py                       # report: how many deletions the reviews recorded, and which match
    python pair_deletions.py --list out.txt        # write the original paths of the matched ones
    powershell -File tools/recycle-restore.ps1 -Paths out.txt -Apply      # put exactly those back

The Recycle Bin knows a path and a time, not a reason. The *reason* is in the review: `pair_review` records every
decision as `{choice: delete_left|delete_right, left, right, at}` in its state file, so an A/B deletion is matched exactly
by the deleted picture's **name** and by the **time** of a delete decision on that name (near it, because the state is
saved a moment before the shell does its work).

Deletions from the pane's Delete button and from A/B elimination use the same file triple, so the shape of a deletion
cannot tell them apart - this join is the only way. A review that has recorded no delete decisions restores nothing, and
that is a real answer, not a failure.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pair_review  # noqa: E402

WINDOW = timedelta(minutes=15)


def review_deletions(state_dir=None):
    """[(name, when, choice, pile)] for every delete decision in every review state."""
    folder = Path(state_dir or pair_review.STATE_DIR)
    out = []
    if not folder.exists():
        return out
    for path in sorted(folder.glob('*.json')):
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        for _pair_id, decided in (state.get('decisions') or {}).items():
            choice = decided.get('choice')
            if choice not in ('delete_left', 'delete_right'):
                continue
            name = decided.get('left') if choice == 'delete_left' else decided.get('right')
            try:
                when = datetime.fromisoformat(str(decided.get('at')))
            except Exception:
                continue
            out.append((str(name), when, choice, path.name))
    return out


def deliberate_deletions(state_dir=None):
    """{name: when} for every deletion this app performed on purpose (the Delete button and the review's own delete rule).

    Written by the app as it deletes. A deliberate deletion must never be restored by a rescue: putting back a file
    somebody chose to remove is undoing their decision.
    """
    base = Path(state_dir or pair_review.STATE_DIR).parent
    out = {}
    log = base / 'deletions.jsonl'
    if not log.exists():
        return out
    for line in log.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            row = json.loads(line)
            out.setdefault(str(row.get('name')), []).append(datetime.fromisoformat(str(row.get('at'))))
        except Exception:
            continue
    return out


def classify(bin_rows, deletions, deliberate, window=WINDOW):
    """[(row, 'ab' | 'manual' | 'unrecorded', detail)] for each Bin entry.

    'ab' - a review delete decision matches by name and time (a rule removed it; the likely unintended kind)
    'manual' - the app recorded it as a deliberate delete
    'unrecorded' - neither: deleted outside this app, or before the log existed. Not "accidental" - just unknown.
    """
    hits = {id(row): decision for row, decision in match(bin_rows, deletions, window)}
    out = []
    for row in bin_rows:
        name = Path(str(row.get('path'))).name
        if id(row) in hits:
            out.append((row, 'ab', hits[id(row)]))
            continue
        try:
            when = datetime.fromisoformat(str(row.get('deleted')))
        except Exception:
            out.append((row, 'unrecorded', None))
            continue
        stamps = deliberate.get(name) or []
        if any(abs(when - at) <= window for at in stamps):
            out.append((row, 'manual', name))
        else:
            out.append((row, 'unrecorded', None))
    return out


def match(bin_rows, deletions, window=WINDOW):
    """[(bin_row, decision)] where a bin entry is the picture a delete decision removed."""
    hits = []
    for row in bin_rows:
        name = Path(str(row.get('path'))).name
        try:
            when = datetime.fromisoformat(str(row.get('deleted')))
        except Exception:
            continue
        for deleted_name, decided_at, choice, pile in deletions:
            if deleted_name != name:
                continue
            if abs(when - decided_at) <= window:
                hits.append((row, (deleted_name, decided_at, choice, pile)))
                break
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(description='Match Recycle Bin entries to A/B elimination decisions.')
    ap.add_argument('--bin', help='JSON from tools/recycle-report.ps1 -Json')
    ap.add_argument('--state-dir', default='', help='where the review states live (default: the app\'s own)')
    ap.add_argument('--window-minutes', type=int, default=int(WINDOW.total_seconds() // 60))
    ap.add_argument('--list', default='', help='write the matched original paths here, for recycle-restore.ps1 -Paths')
    a = ap.parse_args(argv)

    deletions = review_deletions(a.state_dir or None)
    states = sorted({d[3] for d in deletions})
    print('review states with delete decisions: %d  (%d deletions recorded)%s'
          % (len(states), len(deletions), (': ' + ', '.join(states[:4])) if states else ''))

    rows = []
    if a.bin:
        rows = json.loads(Path(a.bin).read_text(encoding='utf-8-sig'))
        if isinstance(rows, dict):
            rows = [rows]
    else:
        print('no --bin given: nothing to match against, reporting the reviews only')
        return 0

    window = timedelta(minutes=a.window_minutes)
    hits = match(rows, deletions, window=window)
    tagged = classify(rows, deletions, deliberate_deletions(a.state_dir or None), window)
    counts = {}
    for _row, kind, _d in tagged:
        counts[kind] = counts.get(kind, 0) + 1
    print('bin entries in the report: %d  (by A/B rule: %d, deliberate: %d, unrecorded: %d)'
          % (len(rows), counts.get('ab', 0), counts.get('manual', 0), counts.get('unrecorded', 0)))
    if counts.get('manual'):
        print('   %d are deletions somebody chose; they are never restored by a rescue' % counts['manual'])
    print('matched to an A/B deletion within %d min: %d' % (a.window_minutes, len(hits)))
    for row, (name, when, choice, pile) in hits[:10]:
        print('   %-46s %s  %s' % (name[:46], when.strftime('%m-%d %H:%M:%S'), choice))
    if deletions and not hits:
        print('   the reviews recorded deletions, but none of them is in this bin report (different window or folder?)')
    if not deletions:
        print('   no A/B elimination has ever been recorded here, so nothing can be attributed to it - every deletion in')
        print('   the bin came from the Delete button or from outside the app')
    if a.list:
        paths = [row['path'] for row, _d in hits]
        Path(a.list).write_text('\n'.join(paths) + ('\n' if paths else ''), encoding='utf-8')
        print('wrote %d path(s) -> %s' % (len(paths), a.list))
    if hits and not a.list:
        print('add --list <file> to write the paths for: recycle-restore.ps1 -Paths <file> -Apply')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
