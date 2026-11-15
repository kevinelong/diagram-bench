"""test_pairs.py - the A/B review queue, against example pictures with known visual truth.

    python test_pairs.py            # the suite
    python test_pairs.py --real     # ... and pair the real gallery's biggest prompt folder, read-only

The synthetic set is built so "most similar first" has an answer: two pictures that differ by a single pixel, two that
differ by a lot, and one unrelated, so the correct first pair is knowable.
"""
import argparse
import json
import os
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
    # five pictures are below EXHAUSTIVE_MAX, so the plan is every pair - the decision/reload checks below do not care
    # how many, only that a decision sticks and the pair is not offered again
    check('a small set plans every pair', len(state['pairs']) == 10 and state.get('exhaustive') is True,
          '%d pairs, exhaustive=%s' % (len(state['pairs']), state.get('exhaustive')))
    a, b, _d = state['pairs'][0]
    to_delete = pr.decide(state, a, b, 'delete_left')
    check('delete_left returns the left path', to_delete == a, to_delete)
    check('and records which side was left', state['decisions'][pr._pair_id(a, b)]['choice'] == 'delete_left')
    pr.save_state(state, state_dir)
    reloaded = pr.load_state(key, state_dir)
    check('the decision survives a save and reload', len(reloaded['decisions']) == 1, reloaded['decisions'])
    check('the decided pair is not offered again',
          pr._pair_id(a, b) not in [pr._pair_id(x, y) for x, y, _ in pr.remaining(reloaded)])
    check('the other pairs are still offered', len(pr.remaining(reloaded)) == 9,
          str(len(pr.remaining(reloaded))))

    print('\n-- choices and accounting')
    s2 = pr.load_state('other', state_dir)
    pr.build(files, s2, hash_store=tmp / 'hashes.json')
    x, y, _ = s2['pairs'][0]
    check('keep_both deletes nothing', pr.decide(s2, x, y, 'keep_both') is None)
    p, q, _ = s2['pairs'][1]
    check('delete_right returns the right path', pr.decide(s2, p, q, 'delete_right') == q)
    decided, total, deletes = pr.progress(s2)
    check('progress counts decisions and deletes', (decided, total, deletes) == (2, 10, 1),
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

    # ranking: wins, then fewer losses, then the name
    for _ in range(3):
        rank.record(st, c, a, 'left_wins')          # c: 3W 1T, a: 1W 4L
    rank.record(st, b, a, 'left_wins')              # b: 1W 1T 1L
    order = [Path(p_).name for p_ in rank.sort_by_wins([a, b, c])]
    check('most wins come first', order[0] == 'c.png', order)
    check('the tail is ordered by record', order[1] == 'b.png' and order[2] == 'a.png', order)
    check('a badge says what a picture has done, and how many bouts that is',
          rank.badge(c) == '%dW (%d)' % (rank.record_of(c)['wins'], rank.record_of(c)['bouts']),
          (rank.badge(c), rank.record_of(c)))
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
    # no pair can be made from one picture; exhaustive leaves nothing over because there is nothing to pair
    check('a single picture makes no pair', one['pairs'] == [], str(one['pairs']))
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

    # a set small enough to compare exhaustively gets every pair: 15 questions is a couple of minutes, and the complete
    # graph is better data than a schedule
    def crew(n, name):
        folder = tmp / name
        folder.mkdir(parents=True, exist_ok=True)
        out = []
        for i in range(n):
            p = folder / ('s%d.png' % i)
            Image.new('RGB', (48, 48), (25 * i, 60, 140)).save(p)
            out.append(p)
        return out

    small = crew(6, 'small')
    small_state = pr.build(small, {}, hash_store=tmp / 'small-hashes.json')
    ids = {pr._pair_id(a, b) for a, b, _d in small_state['pairs']}
    check('six pictures get every one of their 15 pairs', len(small_state['pairs']) == 15 and len(ids) == 15,
          '%d pairs, %d unique' % (len(small_state['pairs']), len(ids)))
    check('and no picture is left out', len({n for a, b, _d in small_state['pairs'] for n in (a, b)}) == 6, 'missing')
    check('the plan is marked exhaustive', small_state.get('exhaustive') is True, str(small_state.get('exhaustive')))
    check('and needs no further rounds', pr.next_round(small_state) == [], 'asked for more')
    edge = crew(7, 'edge')
    edge_state = pr.build(edge, {}, hash_store=tmp / 'edge-hashes.json')
    check('seven pictures switch to rounds instead',
          not edge_state.get('exhaustive') and len(edge_state['pairs']) == 3,
          '%d pairs, exhaustive=%s' % (len(edge_state['pairs']), edge_state.get('exhaustive')))

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

    print('\n-- one order: the fit decides, thin records follow, and the input order never shows')
    # 2. is not here: "the pane and the tile agree" needs the window, and lives in test_gallery_ui.py.
    rank_dir = Path(tempfile.mkdtemp(prefix='order-'))

    def standing(name, wins, losses, ties=0, vs=None, when=None):
        """A picture with a record and the per-opponent tallies the fit reads. `when` stamps both files, for ties."""
        p = rank_dir / (name + '.png')
        p.write_bytes(b'x')
        p.with_suffix('.json').write_text(_json.dumps({'image': str(p), 'wins': wins, 'losses': losses,
                                                       'ties': ties, 'bouts': wins + losses + ties,
                                                       'vs': vs or {}}), encoding='utf-8')
        if when is not None:
            os.utime(p, (when, when))
            os.utime(p.with_suffix('.json'), (when, when))
        return str(p)

    def names(paths):
        return [Path(p).name for p in paths]

    # 1. the pair the raw win count was cheapest on: a 3W-0L whose three wins are all against the picture that always
    #    loses, beside a 12W-1L that beat that same picture eleven times and the folder's best once. The perfect record
    #    must not present itself above the earned one.
    weak = standing('weak', 0, 15, vs={'farm3.png': [0, 3, 0], 'earn12.png': [0, 11, 0]})
    farm3 = standing('farm3', 3, 0, vs={'weak.png': [3, 0, 0]})
    best = standing('best', 6, 4, vs={'earn12.png': [3, 1, 0]})
    earn12 = standing('earn12', 12, 1, vs={'weak.png': [11, 0, 0], 'best.png': [1, 1, 0]})
    crew = [weak, farm3, best, earn12]
    ranked = names(pair_rank.order(crew))
    check('a 3W-0L does not outrank a 12W-1L', ranked.index('earn12.png') < ranked.index('farm3.png'), ranked)
    strong = pair_rank.bt_strength(crew)
    check('and the fit is why: beating only the picture that always loses earns no strength',
          strong[farm3] < strong[earn12],
          'farm3 %.4f, earn12 %.4f' % (strong[farm3], strong[earn12]))

    # 3. determinism: three equal records written inside one second, so nothing but the rule can separate them
    tied = [standing(n, 1, 1, when=1_700_000_000) for n in ('tie_c', 'tie_a', 'tie_b')]
    once = names(pair_rank.order(tied))
    check('the same set comes back in the same order twice', names(pair_rank.order(tied)) == once, once)
    check('and shuffling the input changes nothing',
          names(pair_rank.order([tied[2], tied[0], tied[1]])) == once,
          (once, names(pair_rank.order([tied[2], tied[0], tied[1]]))))

    # 4. the point of the whole change: a beats b, b beats c, a beats c; d wins five bouts against a picture that
    #    never wins. Raw wins put d first and cannot say what those wins were worth - the fit can.
    nobody = standing('nobody', 0, 5, vs={'d.png': [0, 5, 0]})
    a_ = standing('a', 3, 0, vs={'b.png': [2, 0, 0], 'c.png': [1, 0, 0]})
    b_ = standing('b', 1, 2, vs={'a.png': [0, 2, 0], 'c.png': [1, 0, 0]})
    c_ = standing('c', 0, 2, vs={'a.png': [0, 1, 0], 'b.png': [0, 1, 0]})
    d_ = standing('d', 5, 0, vs={'nobody.png': [5, 0, 0]})
    seen = [nobody, a_, b_, c_, d_]
    by_count = names(pair_rank.sort_by_wins(seen))
    by_rank = names(pair_rank.order(seen))
    check('raw wins put the farmed picture first - which is what the counts cannot tell apart',
          by_count[0] == 'd.png', by_count)
    check('strength beats count: five wins over a picture that never wins do not outrank winning the head-to-heads',
          by_rank[0] == 'a.png' and by_rank.index('a.png') < by_rank.index('d.png'), by_rank)

    # the thin-record rule: two decided bouts is not a ranking, so such a picture follows the ones that have a fit
    thin2 = standing('thin2', 2, 0, vs={'sure.png': [2, 0, 0]})
    sure = standing('sure', 4, 1, vs={'thin2.png': [0, 2, 0], 'other.png': [4, 0, 0]})
    other = standing('other', 0, 4, vs={'sure.png': [0, 4, 0]})
    check('a picture with fewer than %d decided bouts is ordered after the ones that have a fit'
          % pair_rank.MIN_DECIDED_BOUTS,
          names(pair_rank.order([thin2, sure, other])).index('thin2.png') == 2,
          names(pair_rank.order([thin2, sure, other])))

    # 5. the badge carries the evidence: wins, losses, and the bouts behind them
    one_win = standing('onewin', 1, 0, ties=1)
    check('a badge with no loss reads as wins and the bout count', pair_rank.badge(one_win) == '1W (2)',
          pair_rank.badge(one_win))
    check('a full record reads wins, losses, and the bouts behind them',
          pair_rank.badge(earn12) == '12W-1L (13)', pair_rank.badge(earn12))
    check('and no bout at all means no badge', pair_rank.badge(standing('fresh', 0, 0)) == '',
          pair_rank.badge(standing('fresh', 0, 0)))

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

    print('\n-- an A/B deletion is attributable by name and time, and only then')
    import pair_deletions as pdel
    from datetime import datetime as _dt, timedelta as _td
    when = _dt(2026, 10, 29, 18, 19, 30)
    rows = [
        {'path': r'C:\pics\a.png', 'deleted': (when + _td(seconds=9)).isoformat()},
        {'path': r'C:\pics\b.png', 'deleted': (when + _td(hours=3)).isoformat()},
        {'path': r'C:\pics\c.png', 'deleted': when.isoformat()},
    ]
    decisions = [('a.png', when, 'delete_left', 's.json'), ('b.png', when, 'delete_right', 's.json')]
    hits = pdel.match(rows, decisions)
    check('the picture the decision deleted is matched', [r['path'].split('\\')[-1] for r, _d in hits] == ['a.png'],
          str([r['path'] for r, _d in hits]))
    check('a name without a decision is not matched', not any('c.png' in r['path'] for r, _d in hits), 'matched c')
    check('a decision hours away from the deletion is not matched',
          not any('b.png' in r['path'] for r, _d in hits), 'matched b three hours off')
    check('the choice travels with the match', hits and hits[0][1][2] == 'delete_left', str(hits and hits[0][1]))
    state_file = {'decisions': {'x|y': {'choice': 'delete_right', 'left': 'x.png', 'right': 'y.png',
                                        'at': when.isoformat()}}}
    sd = Path(tempfile.mkdtemp(prefix='decisions-'))
    (sd / 'pile.json').write_text(json.dumps(state_file), encoding='utf-8')
    got = pdel.review_deletions(sd)
    check('a review state yields its delete decisions', got == [('y.png', when, 'delete_right', 'pile.json')],
          str(got))
    win_file = {'decisions': {'x|y': {'choice': 'left_wins', 'left': 'x.png', 'right': 'y.png',
                                      'at': when.isoformat()}}}
    (sd / 'wins.json').write_text(json.dumps(win_file), encoding='utf-8')
    check('a win decision is not a deletion', len(pdel.review_deletions(sd)) == 1,
          str(pdel.review_deletions(sd)))

    print('\n-- a deliberate deletion is never restored by a rescue')
    rows2 = [
        {'path': r'C:\g\ab.png', 'deleted': (when + _td(seconds=5)).isoformat()},
        {'path': r'C:\g\manual.png', 'deleted': (when + _td(seconds=30)).isoformat()},
        {'path': r'C:\g\other.png', 'deleted': when.isoformat()},
    ]
    deliberate = {'manual.png': [when + _td(seconds=28)]}
    decisions2 = [('ab.png', when, 'delete_left', 's.json')]      # a decision naming *these* pictures
    tagged = pdel.classify(rows2, decisions2, deliberate)
    kinds = {Path(r['path']).name: k for r, k, _d in tagged}
    check('an A/B deletion is recognised as one', kinds.get('ab.png') == 'ab', str(kinds))
    check('a button deletion is recognised as deliberate', kinds.get('manual.png') == 'manual', str(kinds))
    check('anything else is called unrecorded, not accidental', kinds.get('other.png') == 'unrecorded', str(kinds))
    check('only the A/B deletion is offered for restore',
          [Path(r['path']).name for r, k, _d in tagged if k == 'ab'] == ['ab.png'], str(kinds))

    sd2 = Path(tempfile.mkdtemp(prefix='delib-'))
    (sd2 / 'review').mkdir()
    (sd2 / 'deletions.jsonl').write_text(
        '\n'.join([json.dumps({'at': when.isoformat(), 'name': 'x.png', 'source': 'button'}),
                    json.dumps({'at': when.isoformat(), 'name': 'y.png', 'source': 'button'}),
                    'not json']), encoding='utf-8')
    got2 = pdel.deliberate_deletions(sd2 / 'review')
    check('the deliberate-deletion log is read', sorted(got2) == ['x.png', 'y.png'], str(sorted(got2)))
    check('a broken line does not lose the others', len(got2) == 2, str(len(got2)))

    print('\n-- at three seconds a decision, a plan has a price')
    check('15 questions is under a minute', round(pr.estimate_minutes([0] * 15), 2) == 0.75,
          str(pr.estimate_minutes([0] * 15)))
    check('144 questions is about seven minutes', round(pr.estimate_minutes([0] * 144), 1) == 7.2,
          str(pr.estimate_minutes([0] * 144)))
    check('every pair of 48 would be 56 minutes', round(pr.estimate_minutes([0] * 1128) ) == 56,
          str(pr.estimate_minutes([0] * 1128)))

    # a round that cannot move anybody is not asked: three seconds a question is three minutes of someone's evening
    settle = {'paths': ['/s/%d.png' % i for i in range(8)],
              'pairs': [['/s/%d.png' % i, '/s/%d.png' % (i + 1), 1] for i in range(0, 8, 2)],
              'decisions': {}, 'round': 1, 'target_rounds': 4}
    for i in range(0, 8, 2):
        settle['decisions'][pr._pair_id('/s/%d.png' % i, '/s/%d.png' % (i + 1))] = {
            'choice': 'left_wins', 'left': '%d.png' % i, 'right': '%d.png' % (i + 1)}
    check('a round is offered while the order can move', len(pr.next_round(settle)) == 4, 'no round offered')
    check('but not when nothing has changed since', pr.next_round(settle) == [], 'asked anyway')
    check('and it says the order settled', settle.get('settled') is True, str(settle.get('settled')))

    print('\nFAILURES:', FAILS if FAILS else 'none')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(main())
