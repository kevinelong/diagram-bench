"""lineage.py - give existing pictures the prompt chain they were made from, once.

Every picture the app renders as an edit records `base_image`. The chain of prompts behind it was never stored, so a
fixed prompt reworded by Enhance, or edited step by step, scattered into several folders that no threshold can merge
(measured: a tag list and its prose rewrite score 0.07 cosine). The chain can be recovered from `base_image` - follow it
back to a picture with no base, and the prompts in that order are the lineage.

This pass writes `lineage` and `lineage_root` into sidecars, copying nothing else. It is safe to run twice: a sidecar
that already has a lineage is never touched, and pictures whose base is gone are marked `lineage_partial` instead of being
given a chain that pretends to start somewhere it did not.

    python lineage.py --gallery C:/Users/kevin/Pictures/zimage-ui             # dry run, prints what it would write
    python lineage.py --gallery C:/Users/kevin/Pictures/zimage-ui --apply     # writes the sidecars

Nothing here reads the image itself, so the pass costs one JSON parse per picture and leaves the pictures untouched.
"""

import argparse
import json
import sys
from pathlib import Path

MAX_STEPS = 64


def _normalise(text):
    return ' '.join((text or '').split())


def _sidecar(png_path):
    """The record beside a picture, or None when there is none (a picture can exist without one)."""
    side = Path(png_path).with_suffix('.json')
    if not side.is_file():
        return None
    try:
        return json.loads(side.read_text(encoding='utf-8'))
    except Exception:
        return None


def chain_for(png_path, seen=None):
    """The prompts that produced this picture, oldest first, plus whether the walk was cut short.

    Returns (steps, partial). `partial` is True when the base of some step is missing or its sidecar carries no prompt -
    the chain then starts at the oldest picture still on disk, which is honest but not the true root.
    """
    seen = set() if seen is None else seen
    png_path = Path(png_path)
    marker = str(png_path).lower()
    if marker in seen:
        return [], True
    seen.add(marker)
    meta = _sidecar(png_path) or {}
    prompt = _normalise(meta.get('prompt'))
    base = meta.get('base_image')
    if not base:
        return ([prompt] if prompt else []), False
    steps, partial = chain_for(Path(base), seen)
    if not steps:
        # the base is gone, or it never had a prompt: this picture starts a chain that is not the real one
        return ([prompt] if prompt else []), True
    if prompt:
        steps.append(prompt)
    return steps[-MAX_STEPS:], partial


def backfill(gallery, apply=False, verbose=True):
    """Fill in `lineage` for every sidecar that lacks it. Returns counts; never raises on a single bad sidecar."""
    gallery = Path(gallery)
    stats = {'pictures': 0, 'already': 0, 'written': 0, 'partial': 0, 'no_prompt': 0, 'failed': 0}
    for png in sorted(gallery.glob('*.png')):
        stats['pictures'] += 1
        meta = _sidecar(png)
        if meta is None:
            stats['failed'] += 1
            continue
        if not _normalise(meta.get('prompt')):
            stats['no_prompt'] += 1
            continue
        if meta.get('lineage'):
            stats['already'] += 1
            continue
        try:
            steps, partial = chain_for(png)
        except Exception:
            stats['failed'] += 1
            continue
        if not steps:
            stats['no_prompt'] += 1
            continue
        stats['written'] += 1
        if partial:
            stats['partial'] += 1
        if apply:
            meta['lineage'] = steps
            meta['lineage_root'] = steps[0]
            if partial:
                meta['lineage_partial'] = True
            png.with_suffix('.json').write_text(json.dumps(meta, indent=1), encoding='utf-8')
        elif verbose and stats['written'] <= 5:
            print('  %s -> %d step(s)%s' % (png.name, len(steps), ' (partial)' if partial else ''))
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description='Give existing pictures the prompt chain they came from.')
    ap.add_argument('--gallery', default=r'C:/Users/kevin/Pictures/zimage-ui')
    ap.add_argument('--apply', action='store_true', help='write the sidecars (default is a dry run)')
    args = ap.parse_args(argv)
    stats = backfill(args.gallery, apply=args.apply)
    print('%s: %d pictures, %d already had a chain, %d %s, %d partial (base gone), %d without a prompt, %d unreadable'
          % ('written' if args.apply else 'would write', stats['pictures'], stats['already'], stats['written'],
             'written' if args.apply else 'to write', stats['partial'], stats['no_prompt'], stats['failed']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
