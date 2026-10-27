"""test_groups.py - the prompt-similarity grouping, against example data with known truth.

    python test_groups.py            # run the suite
    python test_groups.py --real     # ... and also read the real gallery (read-only) for a scale check

No model, no ComfyUI, no network. The synthetic corpus is the brief's own example: three women on a mountain top, a
multi-ethnic group under the milky way, two women kissing on a sofa - plus three unrelated scenes as controls, each in
several phrasings including the ones this app's rephrase button produces.
"""
import argparse
import itertools
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import group_prompts as gp
import rephrase as rp

FAILS = []


def check(name, cond, detail=''):
    print('%-62s %s%s' % (name, 'PASS' if cond else 'FAIL', ('  ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


# --- the brief's three scenes, and three controls ---------------------------------------------------------------
SCENES = {
    'mountain': 'three women standing on a mountain top at sunrise, wide landscape, wind in their coats',
    'milkyway': 'a multi ethnic group of friends lying under the milky way, night sky, long exposure',
    'sofa': 'two women kissing on a leather sofa in a warm living room, lamp light, close crop',
    'pool': 'an olympic swimming pool seen from above, lane ropes, blue tiles, empty',
    'forest': 'a misty pine forest with a narrow path, cold light, ferns in the foreground',
    'kitchen': 'a busy restaurant kitchen at service, steel surfaces, steam, cooks in whites',
}


def variants(base, rephrase_turns=(1, 2, 3)):
    out = [base]
    for turn in rephrase_turns:
        text, changed, _detail = rp.rephrase_text(base, turn)
        if changed:
            out.append(text)
    out += [base.replace('three women', '3 women'), base.replace('multi ethnic', 'multiethnic'),
            base.upper(), '  ' + base + '  ', base + ', highly detailed, 8k']
    return out


def corpus():
    texts, truth = [], []
    for name, base in SCENES.items():
        for v in variants(base):
            texts.append(v)
            truth.append(name)
    return texts, truth


def pairwise(groups_labels, truth):
    tp = fp = fn = 0
    for i, j in itertools.combinations(range(len(truth)), 2):
        same = groups_labels[i] == groups_labels[j]
        want = truth[i] == truth[j]
        if same and want:
            tp += 1
        elif same and not want:
            fp += 1
        elif want and not same:
            fn += 1
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return prec, rec


def labels_for(texts):
    groups = gp.group_texts(texts)
    labels = [0] * len(texts)
    for gi, members in enumerate(groups):
        for m in members:
            labels[m] = gi
    return groups, labels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--real', action='store_true', help='also group the real gallery, read-only, and time it')
    args = ap.parse_args()

    texts, truth = corpus()
    print('-- corpus')
    check('the example corpus is the brief\'s three scenes plus three controls', len(SCENES) == 6)
    check('every scene has several phrasings including rephrase output', all(len(variants(b)) >= 6 for b in SCENES.values()))

    groups, labels = labels_for(texts)
    prec, rec = pairwise(labels, truth)
    check('precision is 1.0: no folder mixes two scenes', prec == 1.0, 'precision %.2f' % prec)
    check('recall is 1.0: every phrasing lands with its scene', rec == 1.0, 'recall %.2f' % rec)
    check('exactly one folder per scene', len(groups) == len(SCENES), '%d folders' % len(groups))
    check('every folder is pure', all(len({truth[i] for i in g}) == 1 for g in groups))

    print('\n-- the three scenes the brief names are distinct folders')
    fam_of = {i: truth[i] for i in range(len(truth))}
    folder_of = {fam: {labels[i] for i in range(len(truth)) if fam_of[i] == fam} for fam in SCENES}
    check('mountain is one folder', len(folder_of['mountain']) == 1)
    check('milky way is another', len(folder_of['milkyway']) == 1)
    check('sofa is a third', len(folder_of['sofa']) == 1)
    check('and the three are different folders',
          len({next(iter(folder_of[k])) for k in ('mountain', 'milkyway', 'sofa')}) == 3)

    print('\n-- rephrase stability: the same scene re-worded stays put')
    lexicon = gp.fit_lexicon(texts)          # idf from the corpus, never from the pair
    unstable = []
    for name, base in SCENES.items():
        for turn in (1, 2, 3, 4):
            text, changed, _ = rp.rephrase_text(base, turn)
            if not changed:
                continue
            if gp.similarity(base, text, lexicon) < gp.DEFAULT_THRESHOLD:
                unstable.append((name, turn, round(gp.similarity(base, text, lexicon), 3)))
    check('four rephrase turns keep every scene together', not unstable, unstable)

    print('\n-- plan(): recent first, folders oldest first, singletons not dressed up as folders')
    items = []
    for i, (text, fam) in enumerate(zip(texts, truth)):
        items.append({'path': 'p%03d.png' % i, 'prompt': text,
                      'created': '2026-10-%02dT10:00' % (10 + (i % 10))})
    plan = gp.plan(items, recent=6)
    check('the newest six are left alone, in date order',
          len(plan['recent']) == 6 and [r['created'] for r in plan['recent']] ==
          sorted((r['created'] for r in plan['recent']), reverse=True), [r['created'] for r in plan['recent']])
    check('and none of them is in a folder',
          not ({r['path'] for r in plan['recent']} & {r['path'] for g in plan['groups'] for r in g['items']}))
    check('folders are ordered oldest first',
          [g['oldest'] for g in plan['groups']] == sorted(g['oldest'] for g in plan['groups']),
          [g['oldest'] for g in plan['groups']])
    check('a folder has at least two pictures', all(len(g['items']) >= 2 for g in plan['groups']))
    check('every folder is named from a real prompt, not invented',
          all(g['label'] and any(g['label'].rstrip('\u2026') in (t or '') for t in texts) for g in plan['groups']),
          [g['label'] for g in plan['groups']][:2])
    check('every picture appears exactly once',
          len({r['path'] for r in plan['recent']} | {r['path'] for g in plan['groups'] for r in g['items']}
              | {r['path'] for r in plan['ungrouped']}) == len(items))

    print('\n-- pictures with no prompt are never put in a folder')
    items2 = items + [{'path': 'blank.png', 'prompt': '', 'created': '2026-10-01T09:00'}]
    plan2 = gp.plan(items2, recent=3)
    check('an empty prompt stays ungrouped', plan2['ungrouped'] and
          any(r['path'] == 'blank.png' for r in plan2['ungrouped']))
    check('and it is not counted into a folder',
          not any(r['path'] == 'blank.png' for g in plan2['groups'] for r in g['items']))

    print('\n-- thresholds and shapes')
    check('a higher threshold never yields fewer folders',
          all(len(gp.group_texts(texts, threshold=a)) <= len(gp.group_texts(texts, threshold=b))
              for a, b in ((0.10, 0.30), (0.30, 0.60))))
    single = gp.plan(items[:1])
    check('a single picture makes no folder', single['groups'] == [] and len(single['recent']) == 1, single['groups'])
    check('an empty list is handled', gp.plan([]) == {'recent': [], 'groups': [], 'ungrouped': [],
                                                      'threshold': gp.DEFAULT_THRESHOLD,
                                                      'recent_n': gp.DEFAULT_RECENT})
    check('identical prompts form one folder', len(gp.group_texts(['a red bicycle'] * 5)) == 1)
    unicode_text = 'three women on a mountain top — sunrise ☀\n\nnew line'
    lex = gp.fit_lexicon(texts)
    check('unicode and newlines do not break it',
          gp.similarity(unicode_text, SCENES['mountain'], lex) >= gp.DEFAULT_THRESHOLD,
          round(gp.similarity(unicode_text, SCENES['mountain'], lex), 3))
    long_prompt = (SCENES['mountain'] + ' ') * 400
    t0 = time.monotonic()
    gp.group_texts([long_prompt, SCENES['mountain'], SCENES['sofa']])
    check('an 18k-character prompt does not hang', time.monotonic() - t0 < 5, '%.2fs' % (time.monotonic() - t0))
    check('grouping is deterministic', gp.group_texts(texts) == gp.group_texts(texts))

    if args.real:
        print('\n-- the real gallery, read-only')
        import json
        gal = Path(r'C:\Users\kevin\Pictures\zimage-ui')
        files = sorted(gal.glob('*.json'))
        rows = []
        for f in files:
            try:
                d = json.loads(f.read_text(encoding='utf-8'))
            except Exception:
                continue
            png = f.with_suffix('.png')
            if not png.exists():
                continue
            rows.append({'path': str(png), 'prompt': (d.get('prompt') or ''), 'created': d.get('created') or ''})
        t0 = time.monotonic()
        plan = gp.plan(rows)
        secs = time.monotonic() - t0
        folded = sum(len(g['items']) for g in plan['groups'])
        print('   %d pictures, %d folders, %d in folders, %d recent, %d ungrouped, %.1fs'
              % (len(rows), len(plan['groups']), folded, len(plan['recent']), len(plan['ungrouped']), secs))
        check('the real gallery groups in under 20 s', secs < 20, '%.1fs' % secs)
        check('it produces more than one folder', len(plan['groups']) > 1, len(plan['groups']))
        check('no folder holds more than half the gallery',
              all(len(g['items']) <= len(rows) / 2 for g in plan['groups']),
              max((len(g['items']) for g in plan['groups']), default=0))
        print('   largest folders:', [len(g['items']) for g in plan['groups'][:8]])

    print('\nFAILURES:', FAILS if FAILS else 'none')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(main())
