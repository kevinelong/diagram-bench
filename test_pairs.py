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

    print('\nFAILURES:', FAILS if FAILS else 'none')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(main())
