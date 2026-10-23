"""test_gallery_ui.py - drive the real window and assert what multiselect and delete actually do.

No ComfyUI is involved: the engine is stubbed, the gallery is a temp directory of generated PNGs with
sidecars, and clicks arrive as synthetic events with the same state bits Tk sends (1 = shift, 4 = ctrl).
Screenshots are taken of the live window so the result is visible, not just asserted.

    python test_gallery_ui.py                 # run the suite
    python test_gallery_ui.py --shots C:/tmp/shots   # and write a PNG per state
"""
from __future__ import annotations

import argparse
import json
import queue
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

import tkinter as tk
from PIL import Image, ImageDraw, ImageGrab
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zimage_ui as zui  # noqa: E402

FAILS = []
SHOTS = None


def check(name, cond, detail=''):
    print('%-52s %s%s' % (name, 'PASS' if cond else 'FAIL', ('  ' + detail) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


class StubEngine:
    """Only what App touches: events, generate, cancel, shutdown."""

    def __init__(self):
        self.events = queue.Queue()
        self.events.put(('ready', None))    # the window starts busy and waits for this
        self.calls = []
        self.owned = False

    def generate(self, *a, **k):
        self.calls.append((a, k))
        return True

    def cancel(self):
        return 'stub: nothing to cancel'

    def shutdown(self):
        return 'stub: nothing to stop'


def make_gallery(dirpath, n=14):
    """n PNGs with distinct colours and sidecars, newest last, so file mtimes order the strip."""
    dirpath.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        im = Image.new('RGB', (320, 240), (30 + 15 * i, 90, 200 - 12 * i))
        d = ImageDraw.Draw(im)
        d.text((12, 12), 'img %02d' % i, fill='white')
        p = dirpath / ('20261023-1200%02d_512x512_seed%d.png' % (i, 100 + i))
        im.save(p)
        p.with_suffix('.json').write_text(json.dumps({
            'prompt': 'prompt for image %d' % i, 'seed': 100 + i, 'steps': 4, 'cfg': 1.0,
            'size': 512, 'base_image': None, 'denoise': 0.6}), encoding='utf-8')
        out.append(p)
    # mtimes must be increasing so the newest is last on disk and first in the strip
    import os, time
    base = time.time() - 3600
    for i, p in enumerate(out):
        os.utime(p, (base + i * 10, base + i * 10))
    return out


def shot(root, name):
    if not SHOTS:
        return
    root.update_idletasks()
    root.update()
    root.lift()
    root.attributes('-topmost', True)
    root.update()
    img = ImageGrab.grab()
    scale = img.width / root.winfo_screenwidth()
    x, y = int(root.winfo_rootx() * scale), int(root.winfo_rooty() * scale)
    w, h = int(root.winfo_width() * scale), int(root.winfo_height() * scale)
    crop = img.crop((x, y, x + w, y + h))
    crop.save(SHOTS / (name + '.png'))
    root.attributes('-topmost', False)


def feed_image(app, path):
    """Deliver a finished generation the way the engine does, so the base-update path is exercised."""
    app.engine.events.put(('image', (Path(path).read_bytes(), str(path))))
    app._poll()
    app.root.update()


def click(app, path, ctrl=False, shift=False):
    state = (0x0004 if ctrl else 0) | (0x0001 if shift else 0)
    app._on_tile_click(NS(state=state), path)
    app.root.update()


def main():
    global SHOTS
    ap = argparse.ArgumentParser()
    ap.add_argument('--shots', default='')
    a = ap.parse_args()
    if a.shots:
        SHOTS = Path(a.shots)
        SHOTS.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix='gal-test-'))
    files = make_gallery(tmp, 26)   # more than the strip holds, so the cap is exercised
    root = tk.Tk()
    try:
        ttk.Style().theme_use('vista')
    except Exception:
        pass
    args = NS(port=8019, size=512, steps=4, cfg=1.0, seed=1234, out=str(tmp),
              initial='', initial_file='', auto=False, no_progress=True)
    app = zui.App(root, StubEngine(), args)
    root.update()
    shot(root, '01-start')

    print('\n-- gallery contents')
    check('gallery shows 18 of 26 files', len(app._tile_widgets) == 18, str(len(app._tile_widgets)))
    check('all 26 known to the app', len(app._files) == 26, str(len(app._files)))
    check('title reports the cap', app.gal_title['text'] == 'Recent (18 of 26)', app.gal_title['text'])
    check('strip scrolls when it overflows', app.gal_canvas.bbox('all')[3] > 0,
          str(app.gal_canvas.bbox('all')))
    newest = app._files[0]
    oldest = app._files[-1]
    check('newest file is first in the strip', newest in app._tile_widgets)
    check('nothing selected at start', app.selected == [])
    check('delete button disabled at start', str(app.del_btn['state']) == 'disabled')

    print('\n-- plain click loads and selects one')
    click(app, newest)
    check('one selected', len(app.selected) == 1, str(len(app.selected)))
    check('it is the clicked file', app.selected[0].name == newest.name)
    check('it became the base', app.base_path is not None and app.base_path.name == newest.name)
    check('prompt restored from sidecar', 'prompt for image 25' in app.text.get('1.0', 'end'))
    check('seed restored', app.seed.get() == '125', app.seed.get())
    check('display loaded', app._current is not None and app._current.name == newest.name)
    check('delete button enabled', str(app.del_btn['state']) == 'normal')
    check('label reports 1 selected', app.sel_label['text'] == '1 selected', app.sel_label['text'])
    shot(root, '02-one-selected')

    print('\n-- ctrl+click adds and removes without changing the base')
    second = app._files[1]
    click(app, second, ctrl=True)
    check('two selected', len(app.selected) == 2, str(len(app.selected)))
    check('base unchanged by ctrl+click', app.base_path.name == newest.name)
    click(app, second, ctrl=True)
    check('ctrl+click same tile removes it', len(app.selected) == 1)
    click(app, app._files[2], ctrl=True)
    click(app, app._files[3], ctrl=True)
    check('three selected after two ctrl+clicks', len(app.selected) == 3, str(len(app.selected)))
    shot(root, '03-three-selected')

    print('\n-- shift+click selects a range from the anchor')
    app.clear_selection()
    app._anchor = app._files[3]
    click(app, app._files[7], shift=True)
    names = [p.name for p in app.selected]
    expect = [app._files[i].name for i in range(3, 8)]
    check('range is contiguous 5', names == expect, '%d selected' % len(names))
    shot(root, '04-range-selected')

    print('\n-- select all toggles, Esc clears')
    app.select_all()
    check('select all takes the whole strip (18)', len(app.selected) == 18, str(len(app.selected)))
    shot(root, '05-select-all')
    app.select_all()
    check('pressing it again clears', app.selected == [])
    app.select_all()
    app._on_escape()
    check('Escape clears the selection', app.selected == [])

    print('\n-- keyboard is ignored while typing')
    app.text.focus_set()
    root.update()
    app.select_all()
    before = len(app.selected)
    rc = app._on_ctrl_a()
    check('Ctrl+A inside the prompt box does not select images', rc is None and len(app.selected) == before)
    app.text.focus_set()
    asked = []
    real_ask = zui.messagebox.askyesno
    zui.messagebox.askyesno = lambda *a, **k: asked.append(1) or True
    rc = app._on_delete_key()
    check('Del inside the prompt box does not delete', rc is None and not asked)

    print('\n-- delete with No leaves everything alone')
    zui.messagebox.askyesno = lambda *a, **k: False
    app.clear_selection()
    click(app, app._files[1], ctrl=True)
    click(app, app._files[2], ctrl=True)
    n_before = len(list(tmp.glob('*.png')))
    gone = app.delete_selected()
    check('declined delete removes nothing', gone == 0 and len(list(tmp.glob('*.png'))) == n_before)
    check('selection survives a declined delete', len(app.selected) == 2)

    print('\n-- delete with Yes removes png, sidecar and thumbnail')
    zui.messagebox.askyesno = lambda *a, **k: True
    targets = list(app.selected)
    for t in targets:            # make sure a cached thumbnail exists for each, so removal is observable
        zui.thumb_for(t)
    thumbs_before = len(list((tmp / '.thumbs').glob('*.jpg')))
    gone = app.delete_selected()
    check('reported two deletions', gone == 2, str(gone))
    check('pngs gone', not any(t.exists() for t in targets))
    check('sidecars gone', not any(t.with_suffix('.json').exists() for t in targets))
    # the strip backfills from the remaining files, so count the targets' own thumbs, not the directory
    check('thumbnails gone', not any((tmp / '.thumbs' / (t.stem + '.jpg')).exists() for t in targets))
    check('selection emptied', app.selected == [])
    check('gallery dropped to 24 files', len(app._files) == 24, str(len(app._files)))
    check('status reports the count', 'deleted 2' in app.status['text'], app.status['text'])
    shot(root, '06-after-delete')

    print('\n-- deleting the base image clears it')
    app.clear_selection()
    base_file = app.base_path
    click(app, base_file, ctrl=True)
    gone = app.delete_selected()
    check('base deleted', gone == 1)
    check('base cleared', app.base_path is None)
    check('base label says none', 'none' in app.base_label['text'], app.base_label['text'])
    check('display reset to placeholder', app._current is None)
    check('copy disabled after display reset', 'disabled' in str(app.copy_btn['state']))

    print('\n-- refresh prunes a selection whose files vanished')
    app.clear_selection()
    victim = app._files[0]
    click(app, victim, ctrl=True)
    victim.unlink()
    victim.with_suffix('.json').unlink(missing_ok=True)
    app.refresh_gallery()
    check('stale selection pruned', victim not in app.selected, str(app.selected))

    print('\n-- regression: controls still behave')
    s0 = int(app.seed.get())
    app.bump_seed(1)
    check('seed +1', int(app.seed.get()) == s0 + 1)
    app.bump_seed(-1)
    check('seed -1 back', int(app.seed.get()) == s0)
    app.random_seed()
    check('random seed is numeric', app.seed.get().isdigit())
    check('layouts offered', app.layout_var.get() in ('single', '4-up (2x2)', '9-up (3x3)', '16-up (4x4)'))
    app.text.delete('1.0', 'end')
    app.text.insert('1.0', 'a red bicycle')
    app.generate()
    root.update()
    check('generate reached the engine', len(app.engine.calls) == 1, str(len(app.engine.calls)))
    if app.engine.calls:
        call = app.engine.calls[-1][0]
        # call signature is (prompt, width, height, steps, cfg, seed, out, kwargs)
        check('generate passed prompt/size/steps/seed/out',
              call[0] == 'a red bicycle' and (call[1], call[2]) == (512, 512) and call[3] == 4
              and isinstance(call[5], int) and call[6] == str(tmp), str(call[:7]))
    check('button locked while busy', 'disabled' in app.gen_btn.state())
    app.busy = False

    print('\n-- a generation does not become the next base')
    check('reuse-last-image is off by default', app.chain_var.get() is False)
    app.clear_base()
    check('no base to begin with', app.base_path is None)
    fresh = app._files[0]
    feed_image(app, fresh)
    check('base stays empty after generating', app.base_path is None)
    check('status confirms the write', 'wrote' in app.status['text'], app.status['text'])
    check('hint says the base is unchanged', 'base unchanged' in app.hint['text'], app.hint['text'])

    print('\n-- the seed moves on after each generation')
    app.seed.set('4242')
    feed_image(app, fresh)
    check('seed changes after a generation', app.seed.get() != '4242', app.seed.get())
    check('hint reports the next seed', 'next seed' in app.hint['text'], app.hint['text'])
    s1 = app.seed.get()
    feed_image(app, fresh)
    check('and again after the next one', app.seed.get() != s1, '%s -> %s' % (s1, app.seed.get()))

    print('\n-- but a gallery pick keeps its own seed')
    gal_item = app._files[2]
    want = str(json.loads(gal_item.with_suffix('.json').read_text(encoding='utf-8'))['seed'])
    click(app, gal_item)
    check('clicking restores that image seed', app.seed.get() == want, '%s (want %s)' % (app.seed.get(), want))
    seed_before = app.seed.get()
    feed_image(app, fresh)
    check('a gallery base does not randomise the seed', app.seed.get() == seed_before,
          '%s -> %s' % (seed_before, app.seed.get()))
    check('hint says the seed was kept', 'kept from the gallery' in app.hint['text'], app.hint['text'])

    print('\n-- chaining still updates the base, and then the seed moves')
    app.chain_var.set(True)
    seed_before = app.seed.get()
    feed_image(app, fresh)
    check('with the box ticked the new image is the base',
          app.base_path is not None and app.base_path.name == fresh.name)
    check('a chained base randomises again', app.seed.get() != seed_before, app.seed.get())
    app.chain_var.set(False)
    app.clear_base()

    print('\n-- n-ups run one tile at a time by default')
    check('default is one at a time', app.seq_var.get() is True)
    app.busy = False
    app.layout_var.set('16-up (4x4)')
    app.size_var.set('512')
    app.engine.calls.clear()
    app.generate(); root.update()
    kw = app.engine.calls[-1][1]
    check('16-up asks for 16', kw.get('batch') == 16, str(kw.get('batch')))
    check('... and sequential=True', kw.get('sequential') is True, str(kw.get('sequential')))
    check('status says one at a time', 'one at a time' in app.status['text'], app.status['text'])
    check('no batch VRAM warning while sequential', '12 GB card' not in app.hint['text'], app.hint['text'])

    app.busy = False
    app.seq_var.set(False)
    app.engine.calls.clear()
    app.generate(); root.update()
    kw = app.engine.calls[-1][1]
    check('unticked asks for one batch', kw.get('sequential') is False)
    check('batch VRAM warning returns at 16-up/512', '12 GB card' in app.hint['text'], app.hint['text'])
    app.busy = False
    app.seq_var.set(True)
    app.layout_var.set('single')

    print('\n-- the browser window shares the selection')
    app.clear_selection()
    app.open_all()
    root.update()
    check('browser registered every remaining file', len(app._all_widgets) == len(app._files),
          '%d vs %d' % (len(app._all_widgets), len(app._files)))
    some = list(app._all_widgets)[1:4]
    for p in some:
        click(app, p, ctrl=True)
    check('selection spans the browser', len(app.selected) == 3, str(len(app.selected)))
    root.update()
    shot(root, '07-browser-selected')
    check('browser delete button enabled', str(app.br_del['state']) == 'normal')

    for w in root.winfo_children():
        if isinstance(w, tk.Toplevel):
            w.destroy()
    root.update()

    root.destroy()
    print('\n%s' % ('ALL PASS' if not FAILS else 'FAILURES: %s' % ', '.join(FAILS)))
    print('gallery was %s' % tmp)
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
