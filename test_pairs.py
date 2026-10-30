"""test_pairs.py - the A/B review queue, against example pictures with known visual truth.

    python test_pairs.py            # the suite
    python test_pairs.py --real     # ... and pair the real gallery's biggest prompt folder, read-only

The synthetic set is built so "most similar first" has an answer: two pictures that differ by a single pixel, two that
differ by a lot, and one unrelated, so the correct first pair is knowable.
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pair_review as pr

FAILS = []


def check(name, cond, detail=''):
    print('%-58s %s%s' % (name, 'PASS' if cond else 'FAIL', ('  ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


def make_set(root):
    """A near-identical pair, a different pair, a lone picture and a blank: the pairing order is then knowable."""
    root.mkdir(parents=True, exist_ok=True)

    def one(name, draw):
        im = Image.new('RGB', (256, 256), (30, 30, 30))
        d = ImageDraw.Draw(im)
        draw(d)
        p = root / name
        im.save(p)
        return p

    def stripes(d, top=16):
        # horizontal bands: the structure dHash actually sees is the row-to-row brightness step
        for i in range(8):
            d.rectangle((0, i * 32, 256, i * 32 + top), fill=(210, 210, 210))

    a = one('a.png', lambda d: stripes(d, 16))
    b = one('b.png', lambda d: stripes(d, 18))          # one band 2 px taller: near-identical
    c = one('c.png', lambda d: [d.rectangle((i * 32, 0, i * 32 + 16, 256), fill=(210, 210, 210)) for i in range(8)])
    d1 = one('d.png', lambda d: d.ellipse((30, 30, 226, 226), outline=(240, 240, 240), width=18))
    e1 = one('e.png', lambda d: d.polygon([(128, 24), (232, 232), (24, 232)], fill=(210, 210, 210)))
    return [a, b, c, d1, e1]


def _raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--real', action='store_true')
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='pairs-'))
    gallery = tmp / 'gal'
    state_dir = tmp / 'state'
    files = make_set(gallery)

    print('-- hashing and pairing')
    hashes = pr.hashes(files, store=tmp / 'hashes.json')
    check('every picture gets a 64-bit hash', all(v and len(v) == 16 for v in hashes.values()), list(hashes.values())[:2])
    check('the hash is stable across calls', pr.hashes(files) == hashes)
    pairs, leftovers = pr.pair_up(files, hashes)
    check('five pictures make two pairs and one leftover', len(pairs) == 2 and len(leftovers) == 1,
          '%d pairs, %d left' % (len(pairs), len(leftovers)))
    check('no picture appears twice', len({p for a, b, _ in pairs for p in (a, b)}) == 2 * len(pairs))
    ab = pr.hamming(hashes[str(files[0])], hashes[str(files[1])])
    cd = pr.hamming(hashes[str(files[2])], hashes[str(files[3])])
    check('the near-identical pair is closer than the odd one', ab < cd, 'a-b %d, c-d %d' % (ab, cd))
    check('pairs are ordered most similar first', [d for _, _, d in pairs] == sorted(d for _, _, d in pairs),
          [d for _, _, d in pairs])
    check('the first pair is the near-identical one',
          {Path(pairs[0][0]).name, Path(pairs[0][1]).name} == {'a.png', 'b.png'},
          '%s/%s at distance %d (others %s)' % (Path(pairs[0][0]).name, Path(pairs[0][1]).name, pairs[0][2],
                                                [d for _, _, d in pairs]))
    check('pairing is deterministic', pr.pair_up(files, hashes)[0] == pairs)

    print('\n-- session state: decisions are recorded and survive a reload')
    key = pr.state_key(files)
    state = pr.load_state(key, state_dir)
    pr.build(files, state, hash_store=tmp / 'hashes.json')
    check('the plan holds both pairs', len(state['pairs']) == 2, state.get('pairs'))
    a, b, _d = state['pairs'][0]
    to_delete = pr.decide(state, a, b, 'delete_left')
    check('delete_left returns the left path', to_delete == a, to_delete)
    check('and records which side was left', state['decisions'][pr._pair_id(a, b)]['choice'] == 'delete_left')
    pr.save_state(state, state_dir)
    reloaded = pr.load_state(key, state_dir)
    check('the decision survives a save and reload', len(reloaded['decisions']) == 1, reloaded['decisions'])
    check('the decided pair is not offered again',
          pr._pair_id(a, b) not in [pr._pair_id(x, y) for x, y, _ in pr.remaining(reloaded)])
    check('the other pair is still offered', len(pr.remaining(reloaded)) == 1)

    print('\n-- choices and accounting')
    s2 = pr.load_state('other', state_dir)
    pr.build(files, s2, hash_store=tmp / 'hashes.json')
    x, y, _ = s2['pairs'][0]
    check('keep_both deletes nothing', pr.decide(s2, x, y, 'keep_both') is None)
    p, q, _ = s2['pairs'][1]
    check('delete_right returns the right path', pr.decide(s2, p, q, 'delete_right') == q)
    decided, total, deletes = pr.progress(s2)
    check('progress counts decisions and deletes', (decided, total, deletes) == (2, 2, 1),
          (decided, total, deletes))
    try:
        pr.decide(s2, p, q, 'delete_both')
        check('an unknown choice is refused', False)
    except ValueError:
        check('an unknown choice is refused', True)

    print('\n-- rank mode: a win is a fact about the picture, and it survives being moved')
    import pair_rank as rank
    tmp2 = Path(tempfile.mkdtemp(prefix='rank-'))
    def pic(name, prompt):
        png = tmp2 / (name + '.png')
        png.write_bytes(b'\x89PNG\r\n\x1a\n')
        (tmp2 / (name + '.json')).write_text(json.dumps({'image': str(png), 'prompt': prompt, 'seed': 7}),
                                             encoding='utf-8')
        return str(png)
    a = pic('a', 'a red bicycle')
    b = pic('b', 'a blue bicycle')
    c = pic('c', 'a green bicycle')
    st = {'key': 'run1', 'total': 3, 'pairs': [[a, b, 0], [b, c, 1], [a, c, 2]], 'decisions': {}}
    check('a fresh picture has no record', rank.record_of(a)['bouts'] == 0, rank.record_of(a))
    check('a win is recorded on the winner and the loser',
          rank.record(st, a, b, 'left_wins') is None
          and rank.record_of(a)['wins'] == 1 and rank.record_of(b)['losses'] == 1,
          (rank.record_of(a), rank.record_of(b)))
    check('it deletes nothing at all', (tmp2 / 'a.png').exists() and (tmp2 / 'b.png').exists())
    meta = json.loads((tmp2 / 'a.json').read_text(encoding='utf-8'))
    check('the sidecar keeps the prompt and gains the record',
          meta.get('prompt') == 'a red bicycle' and meta.get('wins') == 1 and meta.get('bouts') == 1, meta)
    check('a win at the other side is symmetric',
          rank.record(st, a, c, 'right_wins') is None
          and rank.record_of(c)['wins'] == 1 and rank.record_of(a)['losses'] == 1, rank.record_of(c))
    check('a tie counts for neither', rank.record(st, b, c, 'tie') is None
          and rank.record_of(b)['ties'] == 1 and rank.record_of(b)['wins'] == 0, rank.record_of(b))
    check('an unknown choice is refused', _raises(lambda: rank.record(st, a, b, 'both_win')))
    check('the comparison is remembered so it is not asked again',
          len(st['decisions']) == 3 and pr.remaining(st) == [], len(st['decisions']))

    # ranking: wins, then rate, then fewer losses
    for _ in range(3):
        rank.record(st, c, a, 'left_wins')          # c: 3W 1T, a: 1W 4L
    rank.record(st, b, a, 'left_wins')              # b: 1W 1T 1L
    order = [Path(p_).name for p_ in rank.sort_by_wins([a, b, c])]
    check('most wins come first', order[0] == 'c.png', order)
    check('the tail is ordered by record', order[1] == 'b.png' and order[2] == 'a.png', order)
    check('a badge says what a picture has done',
          rank.badge(c).startswith('%dW' % rank.record_of(c)['wins']), (rank.badge(c), rank.record_of(c)))
    check('an untouched picture has no badge', rank.badge(pic('d', 'uncompared')) == '', rank.badge(pic('d', 'uncompared')))

    # moved to another folder: the record goes with the file, so it ranks first in its new home
    moved = tmp2 / 'moved'
    moved.mkdir()
    import shutil
    shutil.move(str(tmp2 / 'c.png'), str(moved / 'c.png'))
    shutil.move(str(tmp2 / 'c.json'), str(moved / 'c.json'))
    fresh_others = [pic('e', 'another scene'), pic('f', 'another scene')]
    ranked = rank.sort_by_wins([str(moved / 'c.png')] + fresh_others)
    check('a winner sorts first in a folder it was moved into', Path(ranked[0]).name == 'c.png',
          [Path(x).name for x in ranked])

    # an explicit reset is the only thing that clears a record
    cleared = rank.reset_wins([str(moved / 'c.png'), a, b])
    check('reset clears the record', rank.record_of(str(moved / 'c.png'))['bouts'] == 0, rank.record_of(str(moved / 'c.png')))
    check('and leaves the prompt alone',
          json.loads((moved / 'c.json').read_text(encoding='utf-8')).get('prompt') == 'a green bicycle')
    check('reset reports what it touched', cleared == 3, cleared)

    print('\n-- edges')
    one = pr.load_state('one', state_dir)
    pr.build(files[:1], one, hash_store=tmp / 'hashes.json')
    check('a single picture makes no pair', one['pairs'] == [] and one['leftovers'] == [str(files[0])])
    check('a pair id is order-independent', pr._pair_id('x/a.png', 'x/b.png') == pr._pair_id('x/b.png', 'x/a.png'))
    broken = gallery / 'broken.png'
    broken.write_bytes(b'not a png')
    h2 = pr.hashes(files + [broken])
    check('an unreadable file gets no hash and does not crash', h2.get(str(broken)) is None)
    check('and it is left out of the pairing', all(str(broken) not in (a2, b2) for a2, b2, _ in pr.pair_up(files + [broken], h2)[0]))
    blank1 = gallery / 'blank1.png'
    blank2 = gallery / 'blank2.png'
    for bl in (blank1, blank2):
        Image.new('RGB', (128, 128), (255, 255, 255)).save(bl)
    hb = pr.hashes([blank1, blank2])
    check('two blank pictures are a legitimate pair',
          pr.pair_up([blank1, blank2], hb)[0] and pr.pair_up([blank1, blank2], hb)[0][0][2] == 0,
          pr.pair_up([blank1, blank2], hb))

    print('\n-- the hash store is reused, not recomputed')
    store = tmp / 'hashes.json'
    first = json.loads(store.read_text(encoding='utf-8'))
    t0 = time.monotonic()
    pr.hashes(files, store=store)
    quick = time.monotonic() - t0
    check('the second pass reads the store', len(first) >= len(files) and quick < 0.5, '%.2fs' % quick)

    if args.real:
        print('\n-- the real gallery, biggest prompt folder, read-only')
        import group_prompts as gp
        gal = Path(r'C:\Users\kevin\Pictures\zimage-ui')
        rows = []
        for f in sorted(gal.glob('*.json')):
            try:
                d = json.loads(f.read_text(encoding='utf-8'))
            except Exception:
                continue
            png = f.with_suffix('.png')
            if png.exists():
                rows.append({'path': str(png), 'prompt': d.get('prompt') or '', 'created': d.get('created') or ''})
        plan = gp.plan(rows)
        biggest = max(plan['groups'], key=lambda g: len(g['items']))
        paths = [r['path'] for r in biggest['items']]
        t0 = time.monotonic()
        hs = pr.hashes(paths, store=tmp / 'real-hashes.json')
        pairs, leftovers = pr.pair_up(paths, hs)
        secs = time.monotonic() - t0
        print('   folder of %d pictures -> %d pairs + %d leftover, hashed and paired in %.1fs'
              % (len(paths), len(pairs), len(leftovers), secs))
        check('a big folder pairs in reasonable time', secs < 30, '%.1fs' % secs)
        check('the first pairs are near-identical', bool(pairs) and pairs[0][2] <= 12, pairs[0][2] if pairs else None)
        print('   first distances:', [d for _a, _b, d in pairs[:8]])

    print('\n-- rounds: a ranking needs more than one bout per picture')
    check('rounds for 2 is 2', pr.rounds_for(2) == 2, str(pr.rounds_for(2)))
    check('rounds for 8 is 3 (log2)', pr.rounds_for(8) == 3, str(pr.rounds_for(8)))
    check('rounds for 48 is 6', pr.rounds_for(48) == 6, str(pr.rounds_for(48)))

    ring = [str(tmp / ('r%d.png' % i)) for i in range(8)]
    state = {'paths': ring, 'pairs': [], 'decisions': {}, 'round': 1, 'target_rounds': pr.rounds_for(8)}
    first = [(ring[i], ring[i + 1], 1) for i in range(0, 8, 2)]
    state['pairs'].extend([list(x) for x in first])
    for a, b, _d in first:
        state['decisions'][pr._pair_id(a, b)] = {'choice': 'left_wins', 'left': Path(a).name, 'right': Path(b).name}
    second = pr.next_round(state)
    covered = {Path(n).name for a, b, _d in second for n in (a, b)}
    check('the second round covers every picture once', len(second) == 4 and len(covered) == 8,
          '%d pairs, %d pictures' % (len(second), len(covered)))
    check('and asks no pair twice', not ({pr._pair_id(a, b) for a, b, _d in second} & set(state['decisions'])),
          'rematch asked')
    winners = {Path(a).name for a, b, _d in first}
    check('winners are paired with winners', all((Path(a).name in winners) == (Path(b).name in winners)
                                                  for a, b, _d in second),
          [(Path(a).name, Path(b).name) for a, b, _d in second])
    check('the round counter advanced', state['round'] == 2, str(state['round']))
    state['round'] = state['target_rounds']
    check('and the schedule stops at the target', pr.next_round(state) == [], 'kept going')

    print('\n-- strength: a win over a strong picture counts for more')
    import json as _json
    import pair_rank
    bt_dir = Path(tempfile.mkdtemp(prefix='bt-'))

    def picture(name, wins, losses, vs):
        p = bt_dir / (name + '.png'); p.write_bytes(b'x')
        p.with_suffix('.json').write_text(_json.dumps({'image': str(p), 'wins': wins, 'losses': losses, 'ties': 0,
                                                       'bouts': wins + losses, 'vs': vs}), encoding='utf-8')
        return p

    # a wins 3 of 3 against the best; b wins 5 but loses 4 of them to a
    a = picture('a', 3, 0, {'s1.png': [2, 0, 0], 's2.png': [1, 0, 0]})
    b = picture('b', 5, 4, {'w1.png': [3, 0, 0], 'a.png': [2, 4, 0]})
    s1 = picture('s1', 2, 2, {'a.png': [0, 2, 0], 's2.png': [2, 0, 0]})
    s2 = picture('s2', 1, 3, {'a.png': [0, 1, 0], 's1.png': [0, 2, 0]})
    w1 = picture('w1', 3, 4, {'b.png': [0, 3, 0], 'a.png': [3, 1, 0]})
    crew = [a, b, s1, s2, w1]
    strong = pair_rank.bt_strength(crew)
    check('every picture gets a strength', all(k in strong for k in map(str, crew)), str(len(strong)))
    check('a win over the best outranks three over the worst', strong[str(w1)] > strong[str(s2)],
          '%.3f vs %.3f' % (strong[str(w1)], strong[str(s2)]))
    order = [Path(p).name for p in pair_rank.sort_by_strength(crew)]
    check('and the order differs from raw wins', order != [Path(p).name for p in pair_rank.sort_by_wins(crew)],
          str(order))
    check('no bouts at all leaves everyone level', len(set(pair_rank.bt_strength([a]).values())) == 1,
          str(pair_rank.bt_strength([a])))

    print('\n-- undo: going back must retract exactly what it added')
    u = win_dir = Path(tempfile.mkdtemp(prefix='undo-'))
    p1, p2 = u / 'one.png', u / 'two.png'
    for q in (p1, p2):
        q.write_bytes(b'x')
        q.with_suffix('.json').write_text(_json.dumps({'image': str(q), 'wins': 0, 'losses': 0, 'ties': 0,
                                                       'bouts': 0}), encoding='utf-8')
    st = {}
    pair_rank.record(st, str(p1), str(p2), 'left_wins')
    before = pair_rank.record_of(str(p1))
    check('a decision is recorded', before['wins'] == 1 and before['bouts'] == 1, str(before))
    undone = pair_rank.unrecord(st, str(p1), str(p2))
    after = pair_rank.record_of(str(p1))
    check('the choice is undone', undone == 'left_wins', str(undone))
    check('the counters go back to zero', after['wins'] == 0 and after['bouts'] == 0, str(after))
    check('the decision is forgotten', not st.get('decisions'), str(st.get('decisions')))
    check('and the opponent tally went too', not (pair_rank.read_meta(p1).get('vs') or {}).get('two.png')
          or pair_rank.read_meta(p1)['vs']['two.png'][0] == 0, str(pair_rank.read_meta(p1).get('vs')))
    check('undoing a pair with no decision is a no-op', pair_rank.unrecord({}, str(p1), str(p2)) is None, 'returned a choice')

    print('\nFAILURES:', FAILS if FAILS else 'none')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(main())
