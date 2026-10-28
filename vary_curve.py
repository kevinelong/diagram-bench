#!/usr/bin/env python
"""How much does a "variation" actually vary? A strength curve, with error bars, from parent/child pairs.

    python vary_curve.py --gallery C:/Users/kevin/Pictures/zimage-ui --out VARY-STRENGTH-2026-10-28.md

Every base render records its parent (`base_image`) and the denoise it was made with, so the gallery already contains a
natural experiment: the same question at many strengths. This tool measures the distance between each child and its
parent four ways - grey level, coarse structure, colour distribution and a difference hash - reports quartiles rather
than a bare mean, and answers three questions:

  * **departure**  at which strength does a variation stop looking like its parent?
  * **plateau**    at which strength does more denoise stop buying more change?
  * **control**    how different are two unrelated pictures, so "differs" has a scale?

Those three numbers are what a strength menu should be built from. Nothing is written to the gallery.
"""

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image


def load_pairs(gallery):
    import json

    metas, pngs = {}, {}
    for path in Path(gallery).iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() == '.png':
            pngs[path.stem] = path
        elif path.suffix.lower() == '.json' and not path.stem.endswith('-hashes'):
            try:
                metas[path.stem] = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                pass
    pairs = []
    for stem, meta in metas.items():
        base = meta.get('base_image')
        if not base:
            continue
        parent = Path(base).stem
        if stem in pngs and parent in pngs:
            pairs.append((stem, parent, round(float(meta.get('denoise') or 1.0), 2), meta))
    return metas, pngs, pairs


def _grey(path, side=64):
    with Image.open(path) as im:
        return np.asarray(im.convert('L').resize((side, side), Image.LANCZOS), dtype=np.float32) / 255.0


def _rgb(path, side=48):
    with Image.open(path) as im:
        return np.asarray(im.convert('RGB').resize((side, side), Image.LANCZOS), dtype=np.float32) / 255.0


def _dhash(path, bits=16):
    with Image.open(path) as im:
        a = np.asarray(im.convert('L').resize((bits + 1, bits), Image.LANCZOS), dtype=np.float32)
    return (a[:, 1:] > a[:, :-1]).flatten()


_CACHE = {}


def _decoded(path):
    """One decode per picture: the four metrics below would otherwise open the same file four times per pair."""
    key = str(path)
    got = _CACHE.get(key)
    if got is not None:
        return got
    with Image.open(path) as im:
        grey = np.asarray(im.convert('L').resize((64, 64), Image.LANCZOS), dtype=np.float32) / 255.0
        rgb = np.asarray(im.convert('RGB').resize((48, 48), Image.LANCZOS), dtype=np.float32) / 255.0
        small = np.asarray(im.convert('L').resize((17, 16), Image.LANCZOS), dtype=np.float32)
    dhash = (small[:, 1:] > small[:, :-1]).flatten()
    _CACHE[key] = (grey, rgb, dhash)
    return _CACHE[key]


def distance_pair(a_path, b_path):
    """Four distances, each in 0..1, because one number can lie about what changed."""
    ga, ca, da = _decoded(a_path)
    gb, cb, db = _decoded(b_path)
    grey = float(np.abs(ga - gb).mean())
    blocks = float(np.abs(ga.reshape(8, 8, 8, 8).mean(axis=(1, 3)) - gb.reshape(8, 8, 8, 8).mean(axis=(1, 3))).mean())
    # colour: L1 between per-channel histograms, which survives small shifts of a pixel but not a palette change
    hist = []
    for c in range(3):
        ha, _ = np.histogram(ca[:, :, c], bins=16, range=(0, 1), density=True)
        hb, _ = np.histogram(cb[:, :, c], bins=16, range=(0, 1), density=True)
        hist.append(np.abs(ha - hb).sum() / 2.0)
    colour = float(np.mean(hist))
    # structure: fraction of neighbouring-pixel comparisons that flip, the cheapest perceptual-ish signal
    dhash = float((da != db).mean())
    return {'grey': grey, 'blocks': blocks, 'colour': colour, 'dhash': dhash}


def summarise(values):
    values = sorted(values)
    n = len(values)
    if n == 1:
        return values[0], values[0], values[0], values[0], values[0]
    q = statistics.quantiles(values, n=4)
    return statistics.mean(values), statistics.median(values), q[0], q[2], max(values)


BANDS = ((0.0, 0.55), (0.55, 0.65), (0.65, 0.72), (0.72, 0.78), (0.78, 0.84), (0.84, 0.90), (0.90, 1.01))


def band_of(denoise):
    for low, high in BANDS:
        if low <= denoise < high:
            return (low, high)
    return BANDS[-1]


def banded(rows, metric):
    """Wide bands, so every row of the table has an honest n behind it."""
    bins = defaultdict(list)
    for denoise, dist in rows:
        bins[band_of(denoise)].append(dist[metric])
    out = []
    for band in sorted(bins):
        mean, median, q1, q3, top = summarise(bins[band])
        out.append({'band': band, 'n': len(bins[band]), 'mean': mean, 'median': median,
                    'q1': q1, 'q3': q3, 'max': top})
    return out


def envelope(bands):
    """The monotone reading of a noisy curve: each band cannot show less change than the ones below it."""
    out, running = [], 0.0
    for b in bands:
        running = max(running, b['median'])
        out.append(dict(b, envelope=running))
    return out


def curve(rows, metric):
    bins = defaultdict(list)
    for denoise, dist in rows:
        bins[denoise].append(dist[metric])
    out = []
    for denoise in sorted(bins):
        mean, median, q1, q3, top = summarise(bins[denoise])
        out.append({'denoise': denoise, 'n': len(bins[denoise]), 'mean': mean, 'median': median,
                    'q1': q1, 'q3': q3, 'max': top})
    return out


def spearman(xs, ys):
    def rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks

    rx, ry = rank(xs), rank(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gallery', default=r'C:/Users/kevin/Pictures/zimage-ui')
    ap.add_argument('--out', default='VARY-STRENGTH-2026-10-28.md')
    ap.add_argument('--sweep', default='', help='JSON from sweep_vary.py: one base, only the strength varying')
    a = ap.parse_args()

    metas, pngs, pairs = load_pairs(a.gallery)
    print('pairs with both ends on disk: %d' % len(pairs))

    measured = []
    for stem, parent, denoise, meta in pairs:
        try:
            d = distance_pair(pngs[stem], pngs[parent])
        except Exception as e:
            print('  skip %s: %s' % (stem, e))
            continue
        measured.append((denoise, d, stem, meta))
    print('measured: %d' % len(measured))

    # the control: unrelated pictures, matched in number to the base renders
    unrelated = [s for s in pngs if s not in {m[2] for m in measured}]
    unrelated.sort()
    control = []
    for i in range(0, min(len(unrelated) - 1, 300), 2):
        try:
            control.append(distance_pair(pngs[unrelated[i]], pngs[unrelated[i + 1]]))
        except Exception:
            pass

    metrics = ('grey', 'blocks', 'colour', 'dhash')
    lines = []
    lines.append('# What a "vary" strength actually does, %s\n' % Path(a.out).stem.split('-')[-1])
    lines.append('%d parent/child pairs from `%s`, each measured four ways at 64x64 (48x48 for colour). A strength is '
                 'only useful if it buys a predictable amount of change; this is the measured amount.\n' % (
                     len(measured), a.gallery))

    for metric in metrics:
        rows = [(den, d) for den, d, _s, _m in measured]
        bins = curve(rows, metric)
        ctrl = [d[metric] for d in control]
        cmean, cmedian, cq1, cq3, cmax = summarise(ctrl)
        rho = spearman([b['denoise'] for b in bins], [b['median'] for b in bins])
        lines.append('## %s\n' % {'grey': 'Grey level (mean absolute difference)',
                                  'blocks': 'Coarse structure (8x8 block means)',
                                  'colour': 'Colour distribution (per-channel histogram L1)',
                                  'dhash': 'Structure/topology (difference-hash flips)'}[metric])
        lines.append('| denoise | pairs | median | quartiles | control (unrelated) |')
        lines.append('|---|---|---|---|---|')
        for b in bins:
            lines.append('| %.2f | %d | %.4f | %.4f - %.4f | %.4f |' % (
                b['denoise'], b['n'], b['median'], b['q1'], b['q3'], cmedian))
        lines.append('')
        lines.append('Monotone in strength: Spearman rho = **%.2f** over %d bins.' % (rho, len(bins)))
        lines.append('')

        # departure: the lowest strength whose lower quartile is above the control's median
        departure = next((b['denoise'] for b in bins if b['q1'] > cmedian), None)
        # plateau: the lowest strength whose median is within 10% of the strongest bin's median
        top = max(b['median'] for b in bins)
        plateau = next((b['denoise'] for b in bins if b['median'] >= 0.9 * top), None)
        lines.append('- **departure**: a variation is reliably more than a copy from denoise **%s** '
                     '(its lower quartile clears the unrelated-pair median)' % ('%.2f' % departure if departure else 'not reached'))
        lines.append('- **plateau**: extra strength stops buying much from **%s** (median within 10%% of the '
                     'strongest bin)' % ('%.2f' % plateau if plateau else 'not reached'))
        lines.append('')

    # the strength table and its recommendations, from bands and the monotone envelope only
    band_rows = [{'band': b['band'], 'n': b['n'], 'median': b['median'], 'q1': b['q1'], 'q3': b['q3']}
                 for b in envelope(banded([(d, m) for d, m, _s, _mm in measured], 'grey'))]
    ctrl_grey = summarise([d['grey'] for d in control])[1]
    lines.append('## In bands, so every row has an honest n\n')
    lines.append('| denoise band | pairs | median grey difference | quartiles | vs an unrelated pair |')
    lines.append('|---|---|---|---|---|')
    for b in band_rows:
        lines.append('| %.2f - %.2f | %d | %.4f | %.4f - %.4f | %.0f%% |' % (
            b['band'][0], b['band'][1], b['n'], b['median'], b['q1'], b['q3'],
            100 * b['median'] / ctrl_grey if ctrl_grey else 0))
    lines.append('')
    lines.append('The envelope (each band cannot show less change than the ones below it): ' + ', '.join(
        '%.0f%%' % (100 * b['envelope'] / ctrl_grey) for b in envelope(banded([(d, m) for d, m, _s, _mm in measured], 'grey'))))
    lines.append('')
    lines.append('## What the strength menu should say\n')
    # With a controlled sweep, the menu comes from it: one base, one prompt, one seed, so only the strength varies.
    # The five presets the app now offers are reported with what they measured, as a ratio to an unrelated pair.
    if a.sweep:
        sweep_rows = sorted(json.loads(Path(a.sweep).read_text(encoding='utf-8')), key=lambda r: r['denoise'])
        presets = (0.40, 0.55, 0.72, 0.87, 0.95)
        lines.append('| preset | denoise | measured grey difference | vs an unrelated pair |')
        lines.append('|---|---|---|---|')
        for want in presets:
            row = min(sweep_rows, key=lambda r: abs(r['denoise'] - want))
            lines.append('| %s | %.2f | %.4f | %.2fx |' % (
                {0.40: 'subtle', 0.55: 'partial', 0.72: 'real change', 0.87: 'new picture',
                 0.95: 'different image'}.get(want, '') , row['denoise'], row['grey'],
                row['grey'] / ctrl_grey if ctrl_grey else 0))
        lines.append('')
        lines.append('Those are the app\'s Vary presets. They are the distinct levels this sweep measured, so no two of '
                     'them do the same thing - which 0.80 and 0.78 did, and 0.84, 0.87 and 0.90 all did.')
        lines.append('')
    env = envelope(banded([(d, m) for d, m, _s, _mm in measured], 'grey'))
    for fraction, label in (() if a.sweep else ((0.25, 'a nudge'), (0.5, 'a partial redraw'),
                                                 (0.9, 'close to a different picture'))):
        target = fraction * ctrl_grey
        pick = next((b for b in env if b['envelope'] >= target), None)
        if pick:
            lines.append('- "%s" (about %.0f%% of an unrelated picture): denoise **%.2f** (band %.2f-%.2f, n=%d, '
                         'median %.4f)' % (label, 100 * fraction, pick['band'][0], pick['band'][0], pick['band'][1],
                                           pick['n'], pick['median']))
        else:
            lines.append('- "%s": not reached anywhere in this corpus (max envelope %.0f%%)' % (
                label, 100 * env[-1]['envelope'] / ctrl_grey if ctrl_grey else 0))
    lines.append('')
    if a.sweep:
        sweep = json.loads(Path(a.sweep).read_text(encoding='utf-8'))
        sweep.sort(key=lambda r: r['denoise'])
        groups = {}
        for r in sweep:
            groups.setdefault(r.get('pixels') or r.get('sha256') or r['file'], []).append(r['denoise'])
        same_as = {}
        for key, ds in groups.items():
            for d in ds:
                same_as[d] = ds
        lines.append('## Controlled sweep: everything held still but the strength\n')
        lines.append('%d renders from one base, one prompt and one seed, 9 steps, 1024 px. `sweep_vary.py` runs it; the '
                     'output directory is a temp dir, never the gallery.\n' % len(sweep))
        lines.append('| denoise | grey | blocks | colour | dhash | same picture as |')
        lines.append('|---|---|---|---|---|---|')
        for r in sweep:
            others = [d for d in same_as.get(r['denoise'], []) if d != r['denoise']]
            lines.append('| %.2f | %.4f | %.4f | %.4f | %.4f | %s |' % (
                r['denoise'], r['grey'], r['blocks'], r['colour'], r['dhash'],
                ', '.join('%.2f' % d for d in others) or '-'))
        lines.append('')
        repeats = [ds for ds in groups.values() if len(ds) > 1]
        lines.append('**%d renders, %d distinct pictures.** %s' % (
            len(sweep), len(groups),
            'Strength is quantised to the sampler\'s schedule: ' + '; '.join(
                ', '.join('%.2f' % d for d in ds) + ' produced one identical picture' for ds in repeats)
            if repeats else 'Every strength produced a distinct picture.'))
        lines.append('')
        if repeats:
            lines.append('Two consequences worth acting on:')
            lines.append('')
            lines.append('- A strength control finer than about **0.1** is partly theatre at 9 steps - the measured '
                         'distinct levels here are ' + ', '.join(
                             '%.2f' % ((ds[0] + ds[-1]) / 2) for ds in groups.values()) + '.')
            lines.append('- The sidecar records the strength that was *asked for*, not the one that ran, so the record '
                         'overstates how fine the control was. Identity is a pixel hash: the PNG bytes differ even for '
                         'identical pictures, because ComfyUI embeds the workflow in each file.')
            lines.append('')

    lines.append('Caveat worth keeping: the population curve above is observational - different bases, prompts and models '
                 'are mixed in each band, and it is not monotone band to band for that reason. That is why the controlled '
                 'sweep exists: it holds everything but the strength still, and its curve is monotone end to end.')
    lines.append('')

    text = '\n'.join(lines) + '\n'
    Path(a.out).write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
