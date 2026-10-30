#!/usr/bin/env python
"""Read-only analysis: which per-image metadata records in a gallery folder point at pictures that are not there.

    python tools/orphan_metadata.py --folder C:/Users/kevin/Pictures/zimage-ui --manifest <temp>/candidates.txt

Nothing is deleted or moved. The output is counts per class, samples, and (with --manifest) the list of deletion
candidates, so the decision can be reviewed before anything is touched.

Classes, per record:
  KEEP-OK        its `image` resolves to a file that exists
  KEEP-SIBLING   `image` is missing, but a file with the same stem is in the folder (a record whose name drifted)
  KEEP-UNCLEAR   no `image` field, unreadable JSON, or not a picture record - not ours to judge
  CANDIDATE      `image` is missing and the stem is nowhere in the folder: the picture was moved or deleted
      of which "parent still exists" (deleted, folder kept) vs "parent gone" (the folder was moved)
  SHEET          a contact-sheet record: judged by its `tiles`

A picture with no metadata at all is counted separately: that is the opposite problem, and deleting it is not on the table.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

IMAGE_EXT = ('.png', '.webp', '.jpg', '.jpeg')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--folder', required=True)
    ap.add_argument('--manifest', default='')
    ap.add_argument('--samples', type=int, default=6)
    a = ap.parse_args()

    folder = Path(a.folder)
    records = sorted(p for p in folder.glob('*.json'))
    pictures = [p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXT and p.is_file()]
    stems = {p.stem for p in pictures}

    counts = Counter()
    samples = {}
    candidates = []

    for rec_path in records:
        try:
            meta = json.loads(rec_path.read_text(encoding='utf-8', errors='replace'))
        except Exception:
            counts['KEEP-UNCLEAR unreadable'] += 1
            samples.setdefault('KEEP-UNCLEAR unreadable', []).append(rec_path.name)
            continue
        if not isinstance(meta, dict):
            counts['KEEP-UNCLEAR not a record'] += 1
            continue
        if meta.get('tiles'):
            # a sheet is judged by its OWN picture, not by its tiles: tiles may be cleaned up on purpose while the sheet
            # stays, and the record is what carries the sheet's prompt.
            own = Path(meta['image']) if meta.get('image') else None
            key = 'SHEET ok (picture here)' if (own and own.exists()) else 'SHEET orphan (picture gone)'
            counts[key] += 1
            samples.setdefault(key, []).append(rec_path.name)
            if not (own and own.exists()):
                candidates.append((rec_path, str(own) if own else '(no image field)'))
            continue
        if not meta.get('image') and (meta.get('mp4') or meta.get('clip') or meta.get('still')):
            # a clip record: its pictures are mp4/still, not `image`
            have = [k for k in ('mp4', 'still', 'clip') if meta.get(k) and Path(str(meta[k])).exists()]
            key = 'CLIP ok' if have else 'CLIP orphan'
            counts[key] += 1
            samples.setdefault(key, []).append(rec_path.name)
            if not have:
                candidates.append((rec_path, str(meta.get('mp4') or meta.get('clip') or '(no media field)')))
            continue
        image = meta.get('image')
        if not image:
            counts['KEEP-UNCLEAR no image field'] += 1
            samples.setdefault('KEEP-UNCLEAR no image field', []).append(rec_path.name)
            continue
        target = Path(image)
        if target.exists():
            counts['KEEP-OK'] += 1
            continue
        if target.stem in stems:
            counts['KEEP-SIBLING'] += 1
            samples.setdefault('KEEP-SIBLING', []).append('%s -> %s' % (rec_path.name, target.name))
            continue
        parent = target.parent
        key = 'CANDIDATE parent exists' if parent.exists() else 'CANDIDATE parent gone'
        counts[key] += 1
        samples.setdefault(key, []).append('%s -> %s' % (rec_path.name, image))
        candidates.append((rec_path, image))

    meta_stems = {p.stem for p in records}
    naked = [p for p in pictures if p.stem not in meta_stems]

    print('folder: %s' % folder)
    print('records: %d   pictures: %d   pictures without a record: %d' % (len(records), len(pictures), len(naked)))
    print()
    width = max(len(k) for k in counts) if counts else 10
    for key in sorted(counts):
        print('  %-*s %6d' % (width, key, counts[key]))
    print()
    for key in sorted(samples):
        print('%s — %d, e.g.:' % (key, counts.get(key, len(samples[key]))))
        for line in samples[key][:a.samples]:
            print('   ', line[:150])
        print()

    if candidates:
        names = [Path(str(img)).name for _r, img in candidates]
        by_day = Counter(n[:8] for n in names if n[:8].isdigit())
        print('candidate records by day (from the file name):')
        for day in sorted(by_day):
            print('   %s  %5d' % (day, by_day[day]))
        print()

    if a.manifest:
        out = Path(a.manifest)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('\n'.join(str(r) for r, _img in candidates) + '\n', encoding='utf-8')
        print('manifest: %d records -> %s' % (len(candidates), out))
    print('nothing was deleted or moved')


if __name__ == '__main__':
    main()
