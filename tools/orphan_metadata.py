#!/usr/bin/env python
"""Classify a gallery's per-image metadata, and (with --apply) archive-then-delete the orphaned records.

    # analysis only - the default, and it never touches a file
    python tools/orphan_metadata.py --folder C:/Users/kevin/Pictures/zimage-ui --manifest <temp>/candidates.txt

    # prune: archive the orphans into a zip, verify the zip, then delete the records it holds
    python tools/orphan_metadata.py --folder C:/Users/kevin/Pictures/zimage-ui --apply \\
        --archive C:/Users/kevin/Pictures/zimage-ui/orphan-sidecars-2026-10-29.zip

Classes, per record, judged by what it actually points at:
  KEEP-OK        `image` resolves to a file that exists
  KEEP-SIBLING   `image` is missing but a file with the same stem is in the folder
  KEEP-UNCLEAR   no media field, unreadable, or not a picture record - not ours to judge
  CANDIDATE      `image` is missing and the stem is nowhere in the folder
  SHEET ok / SHEET orphan     a contact sheet, judged by its **own** picture, not its tiles
  CLIP ok / CLIP orphan       a clip record: `mp4`/`still`, not `image`

The pruning order is deliberate: **archive, verify the archive against the files, and only then delete**, one file at a
time, each re-checked as an orphan at that moment. A deleted sidecar is the only record of that picture's prompt, seed,
style and settings, so a copy must exist before anything goes - and if the verify step disagrees, nothing is deleted.
"""

import argparse
import json
import zipfile
from collections import Counter
from pathlib import Path

IMAGE_EXT = ('.png', '.webp', '.jpg', '.jpeg')


def classify(folder):
    """(counts, samples, candidate_records). Reads only."""
    folder = Path(folder)
    records = sorted(folder.glob('*.json'))
    pictures = [p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXT and p.is_file()]
    stems = {p.stem for p in pictures}
    counts, samples, candidates = Counter(), {}, []

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
            own = Path(meta['image']) if meta.get('image') else None
            here = bool(own and own.exists())
            key = 'SHEET ok (picture here)' if here else 'SHEET orphan (picture gone)'
            counts[key] += 1
            samples.setdefault(key, []).append(rec_path.name)
            if not here:
                candidates.append((rec_path, str(own) if own else '(no image field)'))
            continue
        if not meta.get('image') and (meta.get('mp4') or meta.get('clip') or meta.get('still')):
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
        key = 'CANDIDATE parent exists' if target.parent.exists() else 'CANDIDATE parent gone'
        counts[key] += 1
        samples.setdefault(key, []).append('%s -> %s' % (rec_path.name, image))
        candidates.append((rec_path, image))

    meta_stems = {p.stem for p in records}
    naked = [p for p in pictures if p.stem not in meta_stems]
    return counts, samples, candidates, len(records), len(pictures), naked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--folder', required=True)
    ap.add_argument('--manifest', default='', help='write (or, with --apply, read) the candidate list')
    ap.add_argument('--samples', type=int, default=6)
    ap.add_argument('--apply', action='store_true', help='archive then delete the orphaned records')
    ap.add_argument('--archive', default='', help='zip that must receive a copy before anything is deleted')
    a = ap.parse_args()

    folder = Path(a.folder)
    counts, samples, candidates, n_records, n_pictures, naked = classify(folder)

    print('folder: %s' % folder)
    print('records: %d   pictures: %d   pictures without a record: %d' % (n_records, n_pictures, len(naked)))
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
        by_day = Counter(Path(str(img)).name[:8] for _r, img in candidates)
        print('candidates by day (from the file name):')
        for day in sorted(d for d in by_day if d.isdigit()):
            print('   %s  %5d' % (day, by_day[day]))
        print()

    if a.manifest and not a.apply:
        out = Path(a.manifest)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('\n'.join(str(r) for r, _img in candidates) + '\n', encoding='utf-8')
        print('manifest: %d records -> %s' % (len(candidates), out))

    if not a.apply:
        print('nothing was deleted or moved')
        return 0

    if not a.archive:
        print('refusing to delete without --archive: the sidecar is the only record of that picture', flush=True)
        return 2
    manifest_set = None
    if a.manifest and Path(a.manifest).is_file():
        manifest_set = {Path(line.strip()).name for line in Path(a.manifest).read_text(encoding='utf-8').splitlines()
                        if line.strip()}

    # 1. the records to remove, re-checked right now: still a .json in this folder, still an orphan, no symlink games
    doomed = []
    for rec_path, _target in candidates:
        if manifest_set is not None and rec_path.name not in manifest_set:
            continue
        if rec_path.parent.resolve() != folder.resolve() or rec_path.suffix.lower() != '.json' or not rec_path.is_file():
            continue
        doomed.append(rec_path)

    # 2. archive them
    archive = Path(a.archive)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for rec_path in doomed:
            z.write(rec_path, arcname=rec_path.name)

    # 3. verify the archive against the files before deleting anything
    with zipfile.ZipFile(archive) as z:
        broken = z.testzip()
        names = set(z.namelist())
        sizes_ok = all(z.getinfo(p.name).file_size == p.stat().st_size for p in doomed)
    if broken or not sizes_ok or names != {p.name for p in doomed}:
        print('archive verify FAILED (broken=%r, sizes_ok=%s, %d names for %d files) - nothing deleted'
              % (broken, sizes_ok, len(names), len(doomed)), flush=True)
        return 3

    # 4. delete, one at a time
    deleted, failed = 0, []
    for rec_path in doomed:
        try:
            rec_path.unlink()
            deleted += 1
        except Exception as e:
            failed.append('%s: %s' % (rec_path.name, e))

    size = archive.stat().st_size if archive.exists() else 0
    print('archived %d records -> %s (%.1f KB) and verified' % (len(doomed), archive, size / 1024.0))
    print('deleted %d records; %d failed' % (deleted, len(failed)))
    for line in failed[:5]:
        print('   ', line)

    after, _s, again, n_records2, n_pictures2, naked2 = classify(folder)
    print('after: records %d (was %d), pictures %d, pictures without a record %d, orphans left %d'
          % (n_records2, n_records, n_pictures2, len(naked2), len(again)))
    return 0 if not failed else 1


if __name__ == '__main__':
    raise SystemExit(main())
