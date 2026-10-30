#!/usr/bin/env python
"""Everything the existing gallery can still tell us, computed from files on disk only.

    python retrospect.py --out RETROSPECT-2026-10-28.md [--gallery C:/Users/kevin/Pictures/zimage-ui]

Nothing is written to the gallery and no new field is assumed: the point is to exhaust what was *already* recorded -
prompts, parameters, seeds, `base_image` links, and the pixels themselves - before deciding what to record differently.

Sections:
  A integrity   what is on disk, and what does not agree with itself
  B history     what was made, when, with which model and steps, and where compute was spent for nothing
  C prompts     the corpus: vocabulary, length, near-duplicates, how many distinct subjects there really are
  D lineage     base renders, chain depth, and (measured in pixels) what a denoise strength actually does
  E categories  the folders, scored retrospectively: sizes, coherence, label representativeness, parent/child agreement
  F blind spots what the existing data cannot answer at all
"""

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

import group_prompts as gp

# The module's documented ceilings: steps above these buy nothing on a distilled model.
CEILING = {'turbo': 9, 'klein': 4}


def canonical_model(name, unet=''):
    """Collapse the recorded spellings to the profiles the app actually had. Reported, not hidden: the mapping is in
    the output, because a silent rename would make the counts unfalsifiable."""
    text = ('%s %s' % (name or '', unet or '')).lower()
    if 'klein' in text:
        return 'flux2 klein 9b'
    if 'turbo' in text:
        return 'z-image turbo'
    if 'schnell' in text:
        return 'flux1 schnell (legacy)'
    if 'flux1 dev' in text or 'flux-dev' in text:
        return 'flux1 dev (legacy)'
    return (name or 'unknown').strip() or 'unknown'


def load(gallery):
    metas, pngs = {}, {}
    for path in gallery.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() == '.png':
            pngs[path.stem] = path
        elif path.suffix.lower() == '.json' and not path.stem.endswith('-hashes'):
            try:
                metas[path.stem] = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                metas[path.stem] = {'_unreadable': True}
    return metas, pngs


def section_a(metas, pngs):
    out = []
    out.append('### A. Integrity\n')
    out.append('| what | count |\n|---|---|')
    out.append('| pictures on disk | %d |' % len(pngs))
    out.append('| sidecars | %d |' % len(metas))
    lonely = sorted(set(pngs) - set(metas))
    out.append('| pictures with no sidecar | %d |' % len(lonely))
    if lonely:
        out.append('|   oldest such picture | %s |' % lonely[0][:33])
    orphans = sorted(set(metas) - set(pngs))
    clips = [s for s in orphans if s.startswith('wan-clip')]
    out.append('| sidecars with no picture (%d), of which clip sidecars | %d |' % (len(orphans), len(clips)))
    rest = [s for s in orphans if s not in clips]
    if rest:
        out.append('|   other orphans | %s |' % ', '.join(rest[:4]))
    unreadable = [k for k, v in metas.items() if v.get('_unreadable')]
    out.append('| unreadable sidecars | %d |' % len(unreadable))

    # dimensions: the recorded size against the decoded one
    mismatch, sheets, early, unreadable_png, sizes = [], [], [], [], Counter()
    for stem, path in pngs.items():
        try:
            with Image.open(path) as im:
                w, h = im.size
        except Exception:
            unreadable_png.append(stem)
            continue
        sizes[(w, h)] += 1
        meta = metas.get(stem) or {}
        if meta.get('width') and (int(meta['width']), int(meta['height'])) != (w, h):
            # a 9-up contact sheet records the *tile* size it was built from, so its three-across assembly is expected
            if meta.get('tiles') or '_sheet' in stem:
                sheets.append((stem, (meta.get('width'), meta.get('height')), (w, h)))
            elif (meta.get('created') or '').startswith('2026-10-22'):
                # the 2026-10-22 grid experiments recorded the *tile* size while saving either the assembled row
                # (3 x tile + 2 x 4 px gap: 776 = 3*256+8, 1544 = 3*512+8) or a single tile
                early.append((stem, (meta.get('width'), meta.get('height')), (w, h)))
            else:
                base = meta.get('base_image')
                base_stem = Path(base).stem if base else None
                if base_stem and base_stem in pngs:
                    try:
                        with Image.open(pngs[base_stem]) as bim:
                            if bim.size == (w, h):
                                # the size box said one thing, the base image's own size won
                                early.append((stem, (meta.get('width'), meta.get('height')), (w, h)))
                                continue
                    except Exception:
                        pass
                mismatch.append((stem, (meta.get('width'), meta.get('height')), (w, h)))
    out.append('| pictures that will not decode | %d |' % len(unreadable_png))
    out.append('| recorded size is a contact sheet\'s tile size, not the file\'s | %d |' % len(sheets))
    out.append('| size box ignored: the file is the base image\'s size (and the 2026-10-22 grid tiles) | %d |' % len(early))
    out.append('| recorded size disagrees with the pixels after that | %d |' % len(mismatch))
    for stem, rec, act in mismatch[:4]:
        out.append('|   e.g. %s: recorded %s, actual %s | |' % (stem[:40], rec, act))
    out.append('')
    out.append('Sizes: ' + ', '.join('%dx%d x%d' % (w, h, n) for (w, h), n in sizes.most_common(8)))
    out.append('')

    # duplicate content: exact, by size then hash, so a 2 GB scan stays linear
    by_size = defaultdict(list)
    for stem, path in pngs.items():
        try:
            by_size[path.stat().st_size].append((stem, path))
        except OSError:
            pass
    groups = 0
    dupes = 0
    examples = []
    for size, items in by_size.items():
        if len(items) < 2:
            continue
        seen = defaultdict(list)
        for stem, path in items:
            h = hashlib.sha256(path.read_bytes()).hexdigest()
            seen[h].append(stem)
        for h, stems in seen.items():
            if len(stems) > 1:
                groups += 1
                dupes += len(stems) - 1
                if len(examples) < 5:
                    examples.append((len(stems), stems[:3], size))
    out.append('| byte-identical pictures | %d in %d groups |' % (dupes, groups))
    for n, stems, size in examples:
        out.append('')
        out.append('- %d copies of one file (%d bytes): %s' % (n, size, ', '.join(stems[:3])))
    out.append('')
    return out


def section_b(metas):
    out = []
    out.append('### B. Generation history\n')
    days = Counter()
    models = Counter()
    steps = Counter()
    combos = Counter()
    for meta in metas.values():
        created = meta.get('created') or ''
        days[created[:10]] += 1
        model = canonical_model(meta.get('model'), meta.get('unet'))
        models[model] += 1
        steps[str(meta.get('steps'))] += 1
        combos[(model, meta.get('steps'))] += 1
    if days:
        out.append('| day | renders |\n|---|---|')
        for day, n in sorted(days.items()):
            out.append('| %s | %d |' % (day or '(none)', n))
        out.append('')
    out.append('Models, canonicalised (spellings folded, and the fold is in `canonical_model`):')
    out.append('')
    for model, n in models.most_common():
        out.append('- %s: %d' % (model, n))
    out.append('')
    out.append('Steps: ' + ', '.join('%s x%d' % (s, n) for s, n in steps.most_common(10)))
    out.append('')

    wasted = Counter()
    for meta in metas.values():
        model = canonical_model(meta.get('model'), meta.get('unet'))
        ceiling = CEILING.get('turbo' if 'turbo' in model else ('klein' if 'klein' in model else ''), None)
        try:
            used = int(meta.get('steps') or 0)
        except (TypeError, ValueError):
            continue
        if ceiling and used > ceiling:
            wasted[(model, used, ceiling)] += 1
    total = sum(wasted.values())
    out.append('**Steps above the model\'s distilled ceiling: %d renders**' % total)
    for (model, used, ceiling), n in wasted.most_common():
        out.append('- %s at %d steps (ceiling %d): %d' % (model, used, ceiling, n))
    out.append('')

    # cfg and denoise shapes
    cfgs = Counter(str(m.get('cfg')) for m in metas.values())
    dens = Counter(round(float(m.get('denoise') or 1.0), 2) for m in metas.values() if m.get('base_image'))
    out.append('cfg: ' + ', '.join('%s x%d' % (c, n) for c, n in cfgs.most_common(5)))
    if dens:
        out.append('')
        out.append('Denoise on base renders: ' + ', '.join('%s x%d' % (d, n) for d, n in sorted(dens.items())))
    out.append('')

    # seed reuse: the same seed across different prompts is not a bug, but it is worth knowing
    seeds = Counter(str(m.get('seed')) for m in metas.values())
    top = [s for s, n in seeds.most_common(3) if n > 1]
    out.append('Distinct seeds: %d; most repeated: %s' % (
        len(seeds), ', '.join('%s x%d' % (s, seeds[s]) for s in top) or 'none'))
    out.append('')
    return out


def section_c(metas):
    out = []
    out.append('### C. The prompt corpus\n')
    prompts = [(stem, (m.get('prompt') or '').strip()) for stem, m in metas.items()]
    prompts = [(s, p) for s, p in prompts if p]
    words = [len(p.split()) for _s, p in prompts]
    out.append('| measure | value |\n|---|---|')
    out.append('| prompts | %d |' % len(prompts))
    out.append('| distinct prompt strings | %d |' % len({p for _s, p in prompts}))
    out.append('| words per prompt: mean / median / max | %.1f / %d / %d |' % (
        statistics.mean(words), statistics.median(words), max(words)))
    out.append('| prompts under 6 words | %d |' % sum(1 for w in words if w < 6))
    out.append('')

    # near-duplicates: the property grouping actually uses
    lex = gp.fit_lexicon([p for _s, p in prompts])
    vecs = [gp.vector(p, lex) for _s, p in prompts]
    # `vector` may return terms the fitted lexicon does not carry, so the index is the union of what it produced
    keys = sorted({k for vec in vecs for k in vec} | set(lex))
    index = {k: i for i, k in enumerate(keys)}
    matrix = np.zeros((len(prompts), len(keys)), dtype=np.float32)
    for row, vec in enumerate(vecs):
        for word, weight in vec.items():
            matrix[row, index[word]] = weight
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0] = 1.0
    matrix /= norms[:, None]
    sims = matrix @ matrix.T
    np.fill_diagonal(sims, -1.0)
    near = sims >= 0.90
    pairs = int(near.sum() // 2)
    # connected components at 0.90: how many genuinely distinct prompts the 1,900 pictures came from
    parent = list(range(len(prompts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    rows, cols = np.where(np.triu(near, 1))
    for a, b in zip(rows.tolist(), cols.tolist()):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    clusters = len({find(i) for i in range(len(prompts))})
    out.append('| prompts within 0.90 cosine of another | %d pairs |' % pairs)
    out.append('| distinct prompts once 0.90 near-duplicates are merged | %d |' % clusters)
    vocab = len(lex.get('idf') or {}) if isinstance(lex, dict) else len(lex)
    out.append('| vocabulary (after the module\'s stop-and-style stripping) | %d terms |' % vocab)
    out.append('')

    # the strongest repeated phrasings, which is what the folders are built from
    common = Counter()
    for _s, p in prompts:
        for n in (3, 4):
            toks = p.lower().split()
            for i in range(len(toks) - n + 1):
                common[' '.join(toks[i:i + n])] += 1
    out.append('Most repeated 4-grams (a phrase in 3+ prompts means the corpus is built on it):')
    out.append('')
    for phrase, n in [(p, c) for p, c in common.most_common(400) if len(p.split()) == 4 and c >= 3][:8]:
        out.append('- "%s" x%d' % (phrase, n))
    out.append('')
    return out


def section_d(metas, pngs):
    out = []
    out.append('### D. Lineage, measured in pixels\n')
    children = {stem: m for stem, m in metas.items() if m.get('base_image')}
    out.append('| measure | value |\n|---|---|')
    out.append('| pictures with a base | %d |' % len(children))

    def chain(stem, limit=60):
        out_ = []
        seen = set()
        while stem and stem not in seen and len(out_) < limit:
            seen.add(stem)
            out_.append(stem)
            base = (metas.get(stem) or {}).get('base_image')
            stem = Path(base).stem if base else None
        return out_

    depths = Counter(len(chain(s)) for s in children)
    out.append('| chain depth: 2 / 3-5 / 6-10 / deeper | %d / %d / %d / %d |' % (
        depths[2], sum(v for k, v in depths.items() if 3 <= k <= 5),
        sum(v for k, v in depths.items() if 6 <= k <= 10),
        sum(v for k, v in depths.items() if k > 10)))
    out.append('| deepest chain | %d |' % max(depths))
    out.append('')

    def grey(path, side=64):
        with Image.open(path) as im:
            return np.asarray(im.convert('L').resize((side, side), Image.LANCZOS), dtype=np.float32) / 255.0

    by_denoise = defaultdict(list)
    measured = 0
    for stem, meta in children.items():
        parent = Path(meta['base_image']).stem
        if stem not in pngs or parent not in pngs:
            continue
        try:
            a, b = grey(pngs[stem]), grey(pngs[parent])
        except Exception:
            continue
        measured += 1
        d = float(np.abs(a - b).mean())
        # a coarse structural term: block means, so a re-seed that moves detail but keeps shapes scores low
        block = float(np.abs(a.reshape(8, 8, 8, 8).mean(axis=(1, 3)) - b.reshape(8, 8, 8, 8).mean(axis=(1, 3))).mean())
        by_denoise[round(float(meta.get('denoise') or 1.0), 2)].append((d, block))
    out.append('Pairs decoded and compared at 64x64 grey: **%d**' % measured)
    out.append('')
    out.append('| denoise | pairs | mean pixel difference | mean block difference |')
    out.append('|---|---|---|---|')
    for den, vals in sorted(by_denoise.items()):
        out.append('| %.2f | %d | %.4f | %.4f |' % (
            den, len(vals), statistics.mean(v[0] for v in vals), statistics.mean(v[1] for v in vals)))
    out.append('')

    # control: pairs of unrelated pictures, to say what "different" looks like without a lineage
    stems = [s for s in pngs if s not in children][:200]
    control = []
    for i in range(0, min(len(stems), 120), 2):
        try:
            control.append(float(np.abs(grey(pngs[stems[i]]) - grey(pngs[stems[i + 1]])).mean()))
        except Exception:
            pass
    if control:
        base = statistics.mean(control)
        out.append('Control - unrelated pairs: mean pixel difference **%.4f** over %d pairs.' % (base, len(control)))
        crossing = sorted(d for d, vals in by_denoise.items() if statistics.mean(v[0] for v in vals) >= base)
        out.append('')
        if crossing:
            out.append('A variation becomes as different as two unrelated pictures from **denoise %.2f** upward; below '
                       'that it is a near-copy of its parent (%d %% of the base renders sit under %.2f).' % (
                           crossing[0], 100 * sum(len(v) for d, v in by_denoise.items() if d < crossing[0]) // max(measured, 1),
                           crossing[0]))
        out.append('')

    # what the prompt text says about the lineage
    same = appended = rewritten = missing = fragment = 0
    for stem, meta in children.items():
        parent = (metas.get(Path(meta['base_image']).stem) or {})
        if not parent:
            missing += 1
            continue
        a = ' '.join((meta.get('prompt') or '').split()).lower()
        b = ' '.join((parent.get('prompt') or '').split()).lower()
        if not a or not b:
            missing += 1
        elif a == b:
            same += 1
        elif b in a:
            appended += 1
        elif a in b:
            fragment += 1
        else:
            rewritten += 1
    out.append('| the child\'s prompt vs its parent\'s | count |')
    out.append('|---|---|')
    out.append('| identical | %d |' % same)
    out.append('| parent contained in child (appended) | %d |' % appended)
    out.append('| child contained in parent (shortened) | %d |' % fragment)
    out.append('| rewritten | %d |' % rewritten)
    out.append('| parent sidecar missing or empty | %d |' % missing)
    out.append('')
    return out


def section_e(metas, pngs):
    out = []
    out.append('### E. The folders, scored retrospectively\n')
    rows = []
    for stem, meta in metas.items():
        prompt = (meta.get('prompt') or '').strip()
        if not prompt:
            continue
        rows.append({'path': stem, 'prompt': prompt, 'created': meta.get('created') or ''})
    plan = gp.plan(rows)
    groups = plan['groups']
    lex = plan.get('_lexicon') or gp.fit_lexicon([r['prompt'] for r in rows])
    sizes = [len(g['items']) for g in groups]
    out.append('| measure | value |\n|---|---|')
    out.append('| folders | %d |' % len(groups))
    out.append('| pictures in folders | %d |' % sum(sizes))
    out.append('| kept ungrouped: newest 18 / no prompt | %d / %d |' % (len(plan['recent']), len(plan['ungrouped'])))
    out.append('| folder size: min / median / max | %d / %d / %d |' % (min(sizes), int(statistics.median(sizes)), max(sizes)))
    out.append('| folders of 2-3 pictures | %d |' % sum(1 for s in sizes if s <= 3))
    out.append('')

    # coherence: how similar the members of a folder are to each other, and to its label
    coher, label_fit = [], []
    for g in groups:
        vecs = [gp.vector(r['prompt'], lex) for r in g['items']]
        label = gp.vector(g['title'], lex)
        if len(vecs) > 1:
            pair_sims = []
            for i in range(len(vecs)):
                for j in range(i + 1, len(vecs)):
                    pair_sims.append(gp.cosine(vecs[i], vecs[j]))
            coher.append(statistics.mean(pair_sims))
        label_fit.append(statistics.mean(gp.cosine(label, v) for v in vecs))
    out.append('| mean intra-folder similarity | %.3f |' % statistics.mean(coher))
    out.append('| folders below 0.5 intra-similarity | %d |' % sum(1 for c in coher if c < 0.5))
    out.append('| mean similarity of the label to its members | %.3f |' % statistics.mean(label_fit))
    out.append('| folders whose label fits below 0.7 | %d |' % sum(1 for f in label_fit if f < 0.7))
    out.append('')

    # does a varied picture land with its parent, and does the folder hold its own label's subject
    folder_of = {}
    for i, g in enumerate(groups):
        for r in g['items']:
            folder_of[r['path']] = i
    same = diff = outside = 0
    for stem, meta in metas.items():
        base = meta.get('base_image')
        if not base:
            continue
        child, parent = folder_of.get(stem), folder_of.get(Path(base).stem)
        if child is None or parent is None:
            outside += 1
        elif child == parent:
            same += 1
        else:
            diff += 1
    out.append('| varied pictures: same folder as parent / different / not in a folder | %d / %d / %d |' % (
        same, diff, outside))
    out.append('')
    return out


def section_f(metas):
    out = []
    out.append('### F. What the existing data cannot answer\n')
    have = Counter()
    for meta in metas.values():
        for key in meta:
            have[key] += 1
    total = len(metas)
    gaps = [
        ('sampler', 'which sampler drew each picture - not recorded at all, so a sampler question is unanswerable'),
        ('wins / rank', 'no picture carries a win, so no preference model can be fit'),
        ('base_prompt', 'the parent\'s prompt is only reachable through the parent file'),
        ('wall time per render', 'only the %d clips record `wall_s`; image renders have no duration' % have.get('wall_s', 0)),
        ('VRAM peak per render', 'recorded for %d clips only' % have.get('vram_peak_mib', 0)),
        ('rejects', 'deleting a picture leaves no trace, so the discarded half of the search is invisible'),
        ('edits', 'a prompt that was edited and re-run is indistinguishable from a fresh one'),
    ]
    out.append('| missing | consequence |\n|---|---|')
    for key, why in gaps:
        out.append('| %s | %s |' % (key, why))
    out.append('')
    needed = ('prompt', 'model', 'steps', 'cfg', 'seed')
    whole = sum(1 for m in metas.values() if all(m.get(k) not in (None, '') for k in needed))
    out.append('Runnable again from their own sidecar alone (prompt, model, steps, cfg, seed present): '
               '**%d of %d (%.0f %%)**; the rest predate the schema or were pasted from the clipboard.' % (
                   whole, total, 100 * whole / max(total, 1)))
    out.append('')
    weak = [(k, n) for k, n in have.items() if n < total * 0.7]
    out.append('Fields present in fewer than 70 % of sidecars: ' + ', '.join(
        '%s (%d)' % (k, n) for k, n in sorted(weak, key=lambda kv: -kv[1])[:12]))
    out.append('')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gallery', default=r'C:/Users/kevin/Pictures/zimage-ui')
    ap.add_argument('--out', default='RETROSPECT-2026-10-28.md')
    a = ap.parse_args()
    gallery = Path(a.gallery)
    metas, pngs = load(gallery)

    lines = []
    lines.append('# Retrospective on the existing gallery, %s\n' % datetime.now().strftime('%Y-%m-%d'))
    lines.append('Gallery: `%s` - %d pictures, %d sidecars. Every number below is computed from those files by '
                 '`retrospect.py`; nothing is estimated and no future field is assumed.\n' % (gallery, len(pngs), len(metas)))
    lines.extend(section_a(metas, pngs))
    lines.extend(section_b(metas))
    lines.extend(section_c(metas))
    lines.extend(section_d(metas, pngs))
    lines.extend(section_e(metas, pngs))
    lines.extend(section_f(metas))

    text = '\n'.join(lines) + '\n'
    Path(a.out).write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
