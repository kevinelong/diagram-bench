#!/usr/bin/env python
"""Tests for the thing the strip and the pane are made of: which files count as pictures, what a tile caches, and how
a picture with no prompt is found.

    python test_tiles.py

Four behaviours, each with the measurement that motivated it:

* every format Pillow can decode is listed - the tuple used to be png/webp/jpg/jpeg and a gif or an avif in the folder
  was invisible rather than unsupported-and-said-so;
* a tile's PhotoImage is cached on a size ladder - the pane's size is `width // cols`, so every pixel of window width
  used to mint a fresh Tk image per tile, and the cache had no bound (221 widths -> 221 images per tile);
* the flat list is the whole gallery - scoping it to the open folder hid the 83 unfiled pictures on the real gallery,
  seven pasted reference screenshots among them;
* a picture with no sidecar can be filtered to and is badged, because it can never join a family.

Self-contained: its own gallery, its own window. No server, no network.
"""

import json
import os
import queue
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

from PIL import Image

os.environ.setdefault('ZIMAGE_SETTINGS_DIR', tempfile.mkdtemp(prefix='tiles-set-'))
os.environ['ZIMAGE_NO_FOCUS'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parent))
import group_prompts as gpx  # noqa: E402
import zimage_ui as zui  # noqa: E402

zui.RECENT_POLICY = 0
FAILS = []


def check(label, ok, detail=''):
    print('%-66s %s %s' % (label, 'PASS' if ok else 'FAIL', detail))
    if not ok:
        FAILS.append(label)


class StubEngine:
    """Only what App touches: events, generate, cancel, shutdown."""

    def __init__(self):
        self.events = queue.Queue()
        self.events.put(('ready', None))     # the window starts busy and waits for this
        self.calls = []

    def generate(self, *a, **k):
        self.calls.append((a, k))
        return True

    def cancel(self, *a, **k):
        return None

    def shutdown(self, *a, **k):
        return None


def wait_plan(app, root, seconds=90):
    end = time.monotonic() + seconds
    while time.monotonic() < end and app._plan is None:
        app._poll()
        root.update()
        time.sleep(0.05)
    return app._plan


def make_gallery(root_dir, formats=('png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tif', 'tiff', 'avif'), n=0):
    """One picture per format, each with a sidecar, plus `n` pictures with no sidecar."""
    made = []
    for i, ext in enumerate(formats):
        p = root_dir / ('shot_%02d_1024x1024_seed%d.%s' % (i, i, ext))
        try:
            Image.new('RGB', (96, 72), (40 + i * 12, 30, 140)).save(p)
        except Exception:
            continue                       # a build without that codec: the format is simply not on offer
        (root_dir / ('shot_%02d_1024x1024_seed%d.json' % (i, i))).write_text(
            json.dumps({'prompt': 'a woman in a dark velvet dress sitting in a room, frame %d' % i,
                        'created': '2026-11-01T10:%02d:00' % i, 'seed': i}), encoding='utf-8')
        made.append(p)
    for i in range(n):
        p = root_dir / ('Screenshot 2026-11-01 1%05d.png' % i)     # a pasted reference: spaces, no sidecar
        Image.new('RGB', (64, 48), (10, 10, 10)).save(p)
        made.append(p)
    return made


def main():
    gal = Path(tempfile.mkdtemp(prefix='tiles-gal-'))
    os.environ['LOCALAPPDATA'] = str(Path(tempfile.mkdtemp(prefix='tiles-app-')) / 'appdata')
    made = make_gallery(gal, n=3)
    root = tk.Tk()
    args = type('A', (), dict(port=8031, size=512, steps=4, cfg=1.0, seed=1, out=str(gal), initial='',
                              initial_file='', auto=False, no_progress=True))()
    app = zui.App(root, StubEngine(), args)
    root.update()
    wait_plan(app, root)

    print('\n-- every format Pillow can decode is a picture')
    scanned = {p.suffix.lower() for p in app._all_files()}
    for ext in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff', '.avif'):
        want = [p for p in made if p.suffix.lower() == ext]
        if not want:
            continue
        check('a %s is listed' % ext, ext in scanned, '%d on disk' % len(want))
    check('the extension list IS the tuple, not a second opinion',
          set(scanned) <= set(zui.App.IMAGE_SUFFIXES), sorted(scanned))

    print('\n-- a picture with no prompt is findable')
    bare = [p for p in made if not p.with_suffix('.json').exists()]
    check('the fixture has pictures with no sidecar', len(bare) == 3, len(bare))
    app.noprompt_only.set(True)
    root.update()
    app.refresh_gallery()
    root.update()
    only = {p.name for p in app._visible_files()}
    check('the no-prompt filter shows exactly those', only == {p.name for p in bare}, sorted(only)[:4])
    app.noprompt_only.set(False)
    root.update()
    app.refresh_gallery()
    root.update()
    check('and turning it off shows everything again', len(app._visible_files()) == len(app._all_files()))

    print('\n-- the strip is the whole gallery, with a folder open')
    app.toggle_groups(True)
    wait_plan(app, root)
    if app._plan:
        app.select_folder(0)
        root.update()
        check('opening a folder does not scope the strip',
              len(app._visible_files()) == len(app._all_files()),
              '%d of %d, folder %r' % (len(app._visible_files()), len(app._all_files()), app._active_folder))
        check('an unfiled picture stays reachable in it',
              all(p.name in {f.name for f in app._visible_files()} for p in bare))
        app.select_folder(None)
        root.update()

    print('\n-- tiles snap to a ladder, and the photo cache is bounded')
    rungs = sorted({zui.tile_bucket(s) for s in range(200, 421)})
    check('a 221-pixel width sweep lands on at most 12 sizes', len(rungs) <= 12, rungs)
    check('no rung exceeds the thumbnail size', max(rungs) <= zui.THUMB_PX, max(rungs))
    check('the ladder is monotone', rungs == sorted(rungs) and rungs[0] >= zui.TILE_STEP)
    paths = [p for p in app._all_files()][:6]
    for p in paths:
        app._thumb_of(p, build=True)
    first = [app._photo_of(app._thumb_of(p), 300, key=p) for p in paths]
    t = time.monotonic()
    again = [app._photo_of(app._thumb_of(p), 300, key=p) for p in paths]
    warm = time.monotonic() - t
    check('a repaint reuses the Tk image objects', all(a is b for a, b in zip(first, again)) and warm < 0.05,
          '%.1f ms for %d tiles' % (warm * 1000, len(paths)))
    app._photo_cache.clear()                    # the repaints above filled it at the strip's own size
    for size in range(200, 421):
        for p in paths:
            app._photo_of(app._thumb_of(p), size, key=p)
    entries = len(app._photo_cache)
    check('a sweep fills one entry per rung, not one per pixel',
          entries <= len(paths) * len(rungs), '%d entries for %d tiles x %d rungs' % (entries, len(paths), len(rungs)))
    for i in range(app._photo_cache_cap + 120):        # more keys than the cap allows
        app._photo_of(app._thumb_of(paths[i % len(paths)]), 240, key='synthetic-%d' % i)
    check('the cache is bounded', len(app._photo_cache) <= app._photo_cache_cap, len(app._photo_cache))

    print('\n-- read_meta is memoised, and a rewritten sidecar is seen')
    side = bare[0].with_suffix('.json')
    check('a picture with no sidecar reads as None', zui.read_meta(bare[0]) is None)
    side.write_text(json.dumps({'prompt': 'a caption that did not exist a moment ago'}), encoding='utf-8')
    check('a new sidecar is read, not a cached None', (zui.read_meta(bare[0]) or {}).get('prompt', '').startswith('a caption'))
    one = zui.read_meta(bare[0])
    two = zui.read_meta(bare[0])
    check('and the same call twice is the same object', one is two)
    side.unlink()
    check('deleting the sidecar is seen too', zui.read_meta(bare[0]) is None)

    print('\n-- the split escalates for long prompts')
    # A family whose prompts are long enough that 0.30 merges them: the real case was 844 pictures whose median
    # prompt was 359 words, median intra-cosine 0.509, and a re-cluster at 0.30 that left 81 % in one piece.
    filler = ('softly diffused amber light from the left of the camera falls across the faces, warm midtones, '
              'cool emerald shadows behind, fine natural skin texture, individual freckles, velvet pile visible, ')
    scenes = ['two women in velvet dresses kissing in a green armchair',
              'a single woman standing by a rainy window in a grey coat',
              'a man reading a book under a brass lamp in a dark library',
              'three children running through tall grass at dusk',
              'an old fishing boat pulled up on a pebble beach at dawn',
              'a bowl of oranges on a scrubbed wooden table in a kitchen']
    rows = []
    for i, scene in enumerate(scenes):
        for k in range(30):
            rows.append({'path': 'p/%02d-%02d.png' % (i, k), 'created': '2026-11-01T10:%02d:%02d' % (i, k % 60),
                         'prompt': scene + ', ' + filler * 6 + 'variation %d' % k, 'root': None, 'lineage': None,
                         'topic': '', 'from_caption': False})
    lexicon = gpx.fit_lexicon([r['prompt'] for r in rows])
    # The size gate is `2 x mean group size`, so the family has to be passed in a population the way `plan` does it:
    # on its own it is the mean and can never be too big.
    big = {'label': 'long prompts', 'items': rows, 'oldest': rows[0]['created'], 'newest': rows[-1]['created'],
           'series': 1}
    decoys = [{'label': 'small %d' % i, 'items': rows[:3], 'oldest': rows[0]['created'],
               'newest': rows[0]['created'], 'series': 1} for i in range(10)]
    groups, leftovers = gpx.split_loose_groups([big] + decoys, lexicon, min_group=gpx.MIN_GROUP)
    groups = [g for g in groups if g['label'] != 'long prompts' or len(g['items']) != len(rows)]
    groups = [g for g in groups if not g['label'].startswith('small ')]
    sizes = sorted((len(g['items']) for g in groups), reverse=True)
    check('a 180-picture family of long prompts becomes several folders', len(groups) >= 2, sizes)
    check('and no single folder holds most of it',
          not sizes or sizes[0] < gpx.SPLIT_SHRINK * len(rows), sizes)
    check('nothing is lost in the split', sum(sizes) + len(leftovers) == len(rows),
          '%d + %d of %d' % (sum(sizes), len(leftovers), len(rows)))

    root.destroy()
    print('\n%s' % ('ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == '__main__':
    sys.exit(main())
