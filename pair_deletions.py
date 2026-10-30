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
    print('bin entries in the report: %d' % len(rows))
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
