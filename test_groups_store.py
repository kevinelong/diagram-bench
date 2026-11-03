"""test_groups_store.py - the rules that make an incremental plan safe. Run: python test_groups_store.py"""
import tempfile
from pathlib import Path

import groups_store as G

FAILS = [0]


def check(name, ok, detail=''):
    if not ok:
        print('FAIL: %s %s' % (name, detail))
        FAILS[0] += 1


def row(name, prompt, root='', created='2026-11-01T10:00:00'):
    r = {'path': str(Path('C:/g') / name), 'prompt': prompt, 'created': created}
    if root:
        r['root'] = root
    return r


rows = [row('a.png', 'a man in a hat', 'a man in a hat', '2026-11-01T10:00:00'),
        row('b.png', 'a man in a hat', 'a man in a hat', '2026-11-01T11:00:00'),
        row('c.png', 'a woman by the sea', 'a woman by the sea', '2026-11-02T09:00:00')]
plan = {'groups': [{'label': 'man', 'items': rows[:2], 'oldest': rows[0]['created'], 'newest': rows[1]['created']},
                   {'label': 'sea', 'items': rows[2:], 'oldest': rows[2]['created'], 'newest': rows[2]['created']}]}
tmp = Path(tempfile.mkdtemp(prefix='gs-'))
fp = G.fingerprint_for(0.5, 24, 1)

check('a missing store is not an error', G.load(tmp) == {})
check('nothing is written for an empty plan', G.save(tmp, {'groups': []}, fp) is None)
G.save(tmp, plan, fp)
check('the store round-trips', bool(G.load(tmp).get('groups')))

back = G.reuse(rows, G.load(tmp), fp)
check('an unchanged gallery reuses the stored plan', back is not None and len(back['groups']) == 2,
      back and [g['label'] for g in back['groups']])
check('membership is the same picture by picture',
      sorted(r['path'] for g in back['groups'] for r in g['items']) == sorted(r['path'] for r in rows))
check('labels are presentation and survive', {g['label'] for g in back['groups']} == {'man', 'sea'})

new_rows = rows + [row('d.png', 'a dragon over a lake', 'a dragon over a lake')]
check('one picture that fits nowhere forces the full pass', G.reuse(new_rows, G.load(tmp), fp) is None,
      'reuse returned a plan for an unknown picture')

check('a changed rule is not reused', G.reuse(rows, G.load(tmp), G.fingerprint_for(0.6, 24, 1)) is None)
check('and an empty store is not reused', G.reuse(rows, {}, fp) is None)

only_sea = [rows[2]]
back2 = G.reuse(only_sea, G.load(tmp), fp)
check('folders whose pictures are all gone are dropped', back2 is not None and len(back2['groups']) == 1,
      back2 and [g['label'] for g in back2['groups']])

print('FAILURES: %d' % FAILS[0] if FAILS[0] else 'ALL PASS')
raise SystemExit(1 if FAILS[0] else 0)
