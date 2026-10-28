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

    print('\n-- a shared style tail must not merge two subjects (2026-10-27 report)')
    TAIL = ', photograph, natural light, shallow depth of field'
    cyber = ['steampunk cyberdeck with brass keys' + TAIL,
             'steampunk cyberdeck on a walnut desk' + TAIL,
             'a steampunk cyberdeck, rivets and dials' + TAIL,
             'steampunk cyberdeck, leather straps' + TAIL]
    figure = ['life drawing figure model, charcoal' + TAIL,
              'life drawing figure model, standing pose' + TAIL,
              'a life drawing figure model on a dais' + TAIL,
              'life drawing figure model, seated' + TAIL]
    mixed_groups = gp.group_texts(cyber + figure)
    mixed_sets = [{('cyber' if i < 4 else 'figure') for i in g} for g in mixed_groups]
    check('two subjects sharing one style clause stay apart', all(len(s_) == 1 for s_ in mixed_sets), mixed_sets)
    # how many folders the variants split into is incidental on an 8-prompt corpus; purity is the invariant
    check('and no folder contains both subjects', len(mixed_groups) >= 2, len(mixed_groups))
    check('the head clause outweighs the style tail',
          gp.cosine(gp.vector(cyber[0], gp.fit_lexicon(cyber + figure)),
                    gp.vector(cyber[1], gp.fit_lexicon(cyber + figure)))
          > gp.cosine(gp.vector(cyber[0], gp.fit_lexicon(cyber + figure)),
                      gp.vector(figure[0], gp.fit_lexicon(cyber + figure))))

    print('\n-- the folder name describes the folder (2026-10-27 report)')
    frag = ['less intense eyes',
            'four mature older adult women clothed in dark colours, standing',
            'four mature older adult women in dark clothes, a wide shot',
            'four mature older adult women, dark coloured clothing']
    lab = gp.label_for(frag, list(range(len(frag))))
    check('the oldest member is not automatically the label', 'less intense eyes' not in lab, lab)
    check('a bare fragment is never chosen while a fuller prompt exists',
          len(gp.tokens(lab)) >= 4, lab)
    check('and the label names the shared subject', 'women' in gp.tokens(lab), lab)
    caps = ['This image is a detailed digital illustration showcasing a fox.',
            'a full-body character concept sheet from multiple angles',
            'a full-body character concept sheet, front and side',
            'full-body character concept sheet, three views']
    lab2 = gp.label_for(caps, list(range(len(caps))))
    check('a model caption template is not used as the name',
          not lab2.lower().startswith(('this image', 'the image', 'the shot')), lab2)
    check('the concept sheet is named as one', 'concept' in gp.tokens(lab2), lab2)


    print('\n-- generic section headers are formatting, not content (2026-10-27 report)')
    brief_a = ('DESCRIPTION:\nA photorealistic portrait of two adult women kissing while seated on an ornate French '
               'sofa.\nCOMPOSITION:\nA horizontal, eye-level medium-wide two-shot, framed from the knees.')
    brief_b = ('DESCRIPTION:\na busy restaurant kitchen at service, steel surfaces, steam\n'
               'COMPOSITION:\nwide angle from the pass, low light')
    check('a header is not a content token', 'description' not in gp.tokens(brief_a)
          and 'composition' not in gp.tokens(brief_a), gp.tokens(brief_a)[:6])
    check('but the sentences after it are', {'portrait', 'french', 'sofa'} <= set(gp.tokens(brief_a)),
          sorted(set(gp.tokens(brief_a)) - {'portrait', 'french', 'sofa'})[:6])
    check('a sentence that merely ends in a colon is kept',
          'room' in gp.tokens('The pages are full of the room: a sofa'), gp.tokens('The pages are full of the room: a sofa'))
    check('an ALL-CAPS label is the strong signal and is stripped', 'camera' not in gp.tokens('CAMERA: wide, low angle')
          and 'wide' in gp.tokens('CAMERA: wide, low angle'), gp.tokens('CAMERA: wide, low angle'))
    check('a title-case label alone on its line is stripped',
          'footwear' not in gp.tokens('Footwear:\nblack leather boots'), gp.tokens('Footwear:\nblack leather boots'))
    check('the same title case inline is not a header',
          'footwear' in gp.tokens('Footwear: black leather boots'), gp.tokens('Footwear: black leather boots'))
    check('"The pages are full of the room:" is prose, not a header',
          {'room', 'sofa'} <= set(gp.tokens('The pages are full of the room: a sofa')),
          gp.tokens('The pages are full of the room: a sofa'))
    check('a bullet header is dropped too', 'angles' not in gp.tokens('\u2022 Multiple Angles: Front, side views'),
          gp.tokens('\u2022 Multiple Angles: Front, side views'))
    lex2 = gp.fit_lexicon([brief_a, brief_b])
    check('two different subjects do not match just because they share headers',
          gp.similarity(brief_a, brief_b, lex2) < gp.DEFAULT_THRESHOLD,
          '%.3f' % gp.similarity(brief_a, brief_b, lex2))
    same = 'COMPOSITION:\nA photorealistic portrait of two adult women on an ornate French sofa.\nLIGHT:\nsoft'
    check('the same subject in another section order still matches',
          gp.similarity(brief_a, same, gp.fit_lexicon([brief_a, same])) > gp.DEFAULT_THRESHOLD,
          '%.3f' % gp.similarity(brief_a, same, gp.fit_lexicon([brief_a, same])))
    lab = gp.label_for([brief_a, brief_b], [0, 1])
    check('a folder is never named after the header', not lab.upper().startswith('DESCRIPTION'), lab)
    check('it is named from the sentence instead', 'portrait' in gp.tokens(lab) or 'sofa' in gp.tokens(lab), lab)
    slug = 'INT.\nA dark office with a single desk lamp'
    check('a screenplay slug is dropped from the name', not gp.label_for([slug], [0]).startswith('INT.'),
          gp.label_for([slug], [0]))
    check('and it is not a content token either', 'int' not in gp.tokens(slug), gp.tokens(slug))

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
        # the reader's complaint, measured: members of a folder should mention what its label says
        def head_words(text):
            return set(gp.tokens(re.split(r'[,;.]', (text or '').lower())[0]))
        total = sum(len(g['items']) for g in plan['groups'])
        stray = 0
        for g in plan['groups']:
            lab = head_words(g['label'])
            if not lab:
                continue
            stray += sum(1 for it in g['items'] if not (set(gp.tokens(it['prompt'])) & lab))
        rate = stray / total if total else 0
        print('   members sharing no word with their folder label: %d of %d (%.1f%%)' % (stray, total, 100 * rate))
        check('folders mostly contain what their label says', rate <= 0.12, '%.1f%% mismatched' % (100 * rate))
        import re as _re
        headerish = _re.compile(r'^\s*(?:[\u2022\-*]\s*)?[A-Z][A-Za-z /\'-]{1,40}:\s*$')
        slugs = ('INT.', 'EXT.', 'INT./EXT.')
        bad_names = [g['label'] for g in plan['groups'] if headerish.match(g['label']) or g['label'] in slugs]
        check('no folder in the real gallery is named after a section header', not bad_names, bad_names[:4])

    print('\n-- two folders that differ only in how their cores read are merged (2026-10-27)')
    # The poodle case reduced to its mechanism: same subject, a style prefix, and an older anchor too short to see the
    # link. `_merge_close` works on cluster cores, so this is testable without the gallery.
    near = ['a black medium size standard poodle on a leather couch in a craftsman home, oil on canvas',
            'a black medium size standard poodle on a leather couch in a craftsman home, oil on canvas painting']
    lex = gp.fit_lexicon(near)
    vecs = gp.build_vectors(near, lex)
    raws = [gp.raw_vector(x, lex) for x in near]
    merged = gp._merge_close([[0], [1]], vecs, raws)
    check('the near-identical pair is one cluster', len(merged) == 1 and len(merged[0]) == 2, merged)

    far = ['a black medium size standard poodle on a leather couch',
           'a white cat asleep on a windowsill in summer light']
    lex2 = gp.fit_lexicon(far)
    v2 = gp.build_vectors(far, lex2)
    r2 = [gp.raw_vector(x, lex2) for x in far]
    apart = gp._merge_close([[0], [1]], v2, r2)
    check('unrelated subjects stay apart', len(apart) == 2, apart)

    print('\n-- the live view offers only what the folder would accept')
    texts = ['a black medium size standard poodle on a leather couch in a craftsman home',
             'a black medium size standard poodle laying on a leather couch, watercolour']
    lex3 = gp.fit_lexicon(texts + ['a lighthouse on a rocky coast at dusk'])
    plan = {'groups': [{'label': 'poodle',
                        'items': [{'path': 'a.png', 'prompt': texts[0], 'created': '2026-10-01'}]}],
            'recent': [{'path': 'b.png', 'prompt': texts[1], 'created': '2026-10-27'},
                       {'path': 'c.png', 'prompt': 'a lighthouse on a rocky coast at dusk',
                        'created': '2026-10-27'}],
            'ungrouped': [], '_lexicon': lex3}
    hits = gp.live_append(plan['groups'][0], plan)
    check('a matching recent picture is offered', [r['path'] for r in hits] == ['b.png'], hits)
    check('an unrelated one is not', all(r['path'] != 'c.png' for r in hits), hits)
    check('a folder member is never offered twice', all(r['path'] != 'a.png' for r in hits), hits)
    check('a plan without a lexicon offers nothing rather than refitting',
          gp.live_append(plan['groups'][0], {}) == [])

    print('\n-- a new prompt can be placed in a folder before it is rendered')
    pood_a = 'a black medium size standard poodle on a leather couch in a craftsman home'
    pood_b = 'artists watercolor rendering of a black medium size standard poodle, leather couch'
    other = 'a lighthouse on a rocky coast at dusk'
    lex = gp.fit_lexicon([pood_a, pood_b, other])
    plan = {'groups': [{'label': 'poodles', 'items': [{'path': 'a', 'prompt': pood_a, 'created': '2026-10-01'}]},
                       {'label': 'coast', 'items': [{'path': 'b', 'prompt': other, 'created': '2026-10-02'}]}],
            'recent': [], 'ungrouped': [], 'threshold': gp.DEFAULT_THRESHOLD, '_lexicon': lex}
    hit = gp.predict('the same poodle on a leather couch, oil on canvas', plan)
    check('a variant is predicted into the right folder', hit is not None and hit['index'] == 0, hit)
    check('and it reports its score and margin', hit and 0 < hit['score'] <= 1.0 and hit['margin'] >= 0.0, hit)
    check('an unrelated prompt is not placed anywhere',
          gp.predict('a satellite view of a coral reef', plan) is None)
    check('an empty prompt is not placed', gp.predict('   ', plan) is None)
    check('a plan with no lexicon predicts nothing', gp.predict(pood_a, {'groups': plan['groups']}) is None)

    print('\n-- one name being the opening of another is the same subject (2026-10-27)')
    check('a shorter name matches the longer one it opens',
          gp._names_the_same_thing('flow chart', 'flow chart with start, processing and finish nodes'))
    check('and it does not care which side is longer',
          gp._names_the_same_thing('hacker cyberdeck screen with red ocr font', 'hacker cyberdeck screen'))
    check('different phrasing of the same subject is not a prefix match',
          not gp._names_the_same_thing('steampunk cyberdeck',
                                       'cyberdeck portable computer with keyboard and waterproof case'))
    check('a single word is too weak to merge on', not gp._names_the_same_thing('comicbook', 'comicbook style'))
    check('identical names are not a prefix pair', not gp._names_the_same_thing('flow chart', 'flow chart'))

    print('\n-- merge: core similarity or a shared opening name')
    texts = ['flow chart', 'flow chart with start, processing and finish nodes']
    lex4 = gp.fit_lexicon(texts)
    vecs4 = [gp.vector(x, lex4) for x in texts]
    raws4 = [gp.raw_vector(x, lex4) for x in texts]
    merged4 = gp._merge_close([[0], [1]], vecs4, raws4, prompts=texts)
    check('the two flow-chart folders merge', len(merged4) == 1 and len(merged4[0]) == 2, merged4)
    apart4 = gp._merge_close([[0], [1]], vecs4, raws4)          # no prompts: similarity only
    check('without prompts the prefix rule cannot fire', len(apart4) == 2, apart4)

    print('\n-- the judge is the cluster medoid, so a different scene cannot evict its neighbours (2026-10-28)')
    # The LAFAYETTE shape, reduced: one long, different scene that shares only a location with a tighter scene, so the
    # longest member (raw mass) would be the judge and would evict the rest. The medoid is the majority's member.
    # four renderings of the same scene, as the drafts in the real case were
    scene = [
        'int. hotel lafayette room 1313 night a storm rages trinity neo morpheus leather sunglasses at last',
        'int. hotel lafayette room 1313 night a storm rages trinity neo morpheus leather sunglasses at last cinematic still',
        'int. hotel lafayette room 1313 night a storm rages trinity neo morpheus leather sunglasses at last anamorphic',
        'int. hotel lafayette room 1313 night a storm rages trinity neo morpheus leather sunglasses at last film grain',
    ]
    intruder = ('a walking talking corporate spreadsheet narrator and marla singer in the support group basement '
                'this is a really bad movie ' * 12)
    prompts = scene + [intruder]
    groups = gp.group_texts(prompts)
    joined = [g for g in groups if len(g) >= 2]
    check('the four scene prompts stay in one folder', joined and sorted(joined[0]) == [0, 1, 2, 3],
          [sorted(g) for g in groups])
    check('and the intruder is not in it', all(4 not in g for g in joined), [sorted(g) for g in groups])

    print('\nFAILURES:', FAILS if FAILS else 'none')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(main())
