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

# ---------------------------------------------------------------- candidate-only clustering
cls = []
def fake_plan(items):
    """Groups by prompt, the way the real planner would for a handful of pictures."""
    cls.append(len(items))
    buckets = {}
    for r in items:
        buckets.setdefault(r['prompt'], []).append(r)
    return {'groups': [{'label': k, 'items': v} for k, v in buckets.items()]}

# the store above knows a.png/b.png (man) and c.png (sea). One new picture that belongs to 'man':
new_row = row('e.png', 'a man in a hat', 'a man in a hat', '2026-11-03T09:00:00')
plan3, reused3, clustered3 = G.merge(rows + [new_row], G.load(tmp), fp, fake_plan)
check('a new picture does not force the whole gallery', clustered3 < len(rows) + 1, clustered3)
check('...only the candidates plus one anchor per family are clustered', clustered3 == 1 + 2, clustered3)
check('the new picture joins the family it belongs to',
      any(sorted(r['path'] for r in g['items']) == sorted([str(Path('C:/g') / n) for n in ('a.png', 'b.png', 'e.png')])
          for g in plan3['groups']), [g['label'] for g in plan3['groups']])
check('the untouched family is still there', any(g['label'] == 'sea' for g in plan3['groups']))

# a picture of a genuinely new subject becomes its own folder
brand = row('f.png', 'a dragon over a lake', 'a dragon over a lake', '2026-11-03T10:00:00')
plan4, _, clustered4 = G.merge(rows + [brand, new_row], G.load(tmp), fp, fake_plan)
check('a new subject makes a new folder', any('dragon' in (g['label'] or '') for g in plan4['groups']),
      [g['label'] for g in plan4['groups']])
check('and the clustered set stays small on a big gallery', clustered4 <= 2 + 2, clustered4)

back5, reused5, clustered5 = G.merge(rows, G.load(tmp), fp, fake_plan)
check('nothing new means no clustering at all', reused5 is True and clustered5 == 0, (reused5, clustered5))

print('FAILURES: %d' % FAILS[0] if FAILS[0] else 'ALL PASS')
raise SystemExit(1 if FAILS[0] else 0)
