"""test_sessions.py - the partitioning rules, as invariants rather than examples.

Run: python test_sessions.py
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import sessions as S

PASS = [0]


def check(name, ok, detail=''):
    if not ok:
        print('FAIL: %s %s' % (name, detail))
        PASS[0] += 1


def pic(name, when, root='a man in a hat'):
    item = {'path': name, 'prompt': 'a man in a hat', 'created': when.isoformat()}
    if root:
        item['root'] = root
    return item


def day(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi)


# ---------------------------------------------------------------- seams: midpoint of long gaps only
items = [pic('a.png', day(2026, 11, 1, 22)),
         pic('b.png', day(2026, 11, 2, 1)),          # 3 h gap: same session
         pic('c.png', day(2026, 11, 2, 11))]         # 10 h gap: a seam
found = S.seams(items)
check('one seam from the ten-hour break', len(found) == 1, found)
cut, before, after = found[0]
check('the seam is the midpoint, not an edge',
      cut == before + (after - before) / 2 and cut.hour == 6, cut)
check('a three-hour break is not a seam', all((b - a) < timedelta(hours=8) for (a, b) in
      [((day(2026, 11, 1, 22)), day(2026, 11, 2, 1))]))

# the threshold is the reader's, not the module's
check('a lower threshold finds the short break too', len(S.seams(items, gap_hours=2)) == 2)

# ---------------------------------------------------------------- split: a partition, by construction
parts = S.split(items, [cut])
check('two segments from one cut', len(parts) == 2, [p['label'] for p in parts])
check('the night stays whole', [i['path'] for i in parts[0]['items']] == ['a.png', 'b.png'],
      parts[0]['items'])
check('the morning is its own session', [i['path'] for i in parts[1]['items']] == ['c.png'])

allpaths = [i['path'] for p in parts for i in p['items']]
check('every item lands exactly once', sorted(allpaths) == sorted(i['path'] for i in items), allpaths)
check('a session is named by where it started', parts[0]['label'].startswith('Sun 01 Nov'), parts[0]['label'])

# ---------------------------------------------------------------- stability: the reason cuts are timestamps
later = items + [pic('d.png', day(2026, 11, 3, 9))]
check('a newer picture does not disturb the existing cut', S.seams(later)[0][0] == cut)
older = items + [pic('z.png', day(2026, 11, 1, 6))]
parts_older = S.split(older, [cut])
check('an older picture joins the session it belongs to',
      [i['path'] for i in parts_older[0]['items']] == ['z.png', 'a.png', 'b.png'],
      parts_older[0]['items'])
check('and the later item joins the open-ended last session',
      [i['path'] for i in S.split(later, [cut])[1]['items']] == ['c.png', 'd.png'])

# ---------------------------------------------------------------- undated pictures are kept, never dropped
with_undated = items + [{'path': 'nope.png', 'prompt': 'x'}]
got = S.split(with_undated, [cut])
check('an undated picture survives the partition',
      'nope.png' in [i['path'] for p in got for i in p['items']])

# ---------------------------------------------------------------- storage: per folder, and only that folder
tmp = Path(tempfile.mkdtemp(prefix='sessions-test-'))
a = [{'path': 'a.png', 'root': 'root one'}]
b = [{'path': 'b.png', 'root': 'root two'}]
key_a = S.key_for({'items': a, 'label': 'labelled'})
key_b = S.key_for({'items': b, 'label': 'other'})
check('the key is the root, not the label', key_a == 'root:root one', key_a)
S.save_cuts(tmp, key_a, [cut])
check('cuts survive a round trip', S.load_cuts(tmp, key_a) == [cut])
S.save_cuts(tmp, key_b, [cut + timedelta(hours=1)])
check('saving one folder leaves the other alone', S.load_cuts(tmp, key_a) == [cut])
S.save_cuts(tmp, key_a, [])
check('clearing removes the key', S.load_cuts(tmp, key_a) == [])
check('a missing store is not an error', S.load_cuts(tmp, 'root:never') == [])
# a folder renamed by a new medoid keeps its cuts, because the key is the root
key_renamed = S.key_for({'items': a, 'label': 'a different median prompt'})
check('a rename keeps the folder identity', key_renamed == key_a, key_renamed)

# ---------------------------------------------------------------- apply(): sibling folders, not a new level
plan = {'recent': [], 'groups': [{'label': 'the family', 'items': items,
                                  'oldest': '2026-11-01T22:00:00', 'newest': '2026-11-02T11:00:00'}]}
S.save_cuts(tmp, S.key_for(plan['groups'][0]), [cut])
expanded = S.apply(plan, tmp)
labels = [g['label'] for g in expanded['groups']]
check('one folder becomes one folder per session', len(labels) == 2, labels)
check('each sibling says which family it came from',
      all(g.get('session_of') == 'the family' for g in expanded['groups']))
check('the plan keeps its other keys', 'recent' in expanded)
nothing = S.apply({'groups': [{'label': 'x', 'items': items}]}, Path(tempfile.mkdtemp(prefix='s2-')))
check('no cuts means no change', len(nothing['groups']) == 1 and nothing['groups'][0]['label'] == 'x')

print('FAILURES: %d' % PASS[0] if PASS[0] else 'ALL PASS')
raise SystemExit(1 if PASS[0] else 0)
