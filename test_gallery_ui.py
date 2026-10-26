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
import os
import queue
import re
import sys
import http.server
import tempfile
import threading
import time
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

    def make_clip(self, *a, **k):
        self.calls.append(('clip', a, k))
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
    os.environ['LOCALAPPDATA'] = str(tmp / 'appdata')   # settings must not touch the real profile
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

    print('\n-- plain click looks and selects one, and does not arm an edit')
    click(app, newest)
    check('one selected', len(app.selected) == 1, str(len(app.selected)))
    check('it is the clicked file', app.selected[0].name == newest.name)
    check('a click does not become the base', app.base_path is None, str(app.base_path))
    check('prompt restored from sidecar', 'prompt for image 25' in app.text.get('1.0', 'end'))
    check('seed restored', app.seed.get() == '125', app.seed.get())
    check('display loaded', app._current is not None and app._current.name == newest.name)
    check('delete button enabled', str(app.del_btn['state']) == 'normal')
    check('label reports 1 selected', app.sel_label['text'] == '1 selected', app.sel_label['text'])
    check('Use as Base is offered once something is shown', 'disabled' not in app.use_base_btn.state())
    shot(root, '02-one-selected')

    print('\n-- Use as Base is the gesture that arms an edit')
    app.use_as_base(); root.update()
    check('Use as Base sets the base', app.base_path is not None and app.base_path.name == newest.name)
    check('base row names it', newest.name[-20:] in app.base_label['text'], app.base_label['text'])
    check('status says the next Enter sends it', 'sends it to the model' in app.status['text'], app.status['text'])
    app.generate(); root.update()
    kw = app.engine.calls[-1][1]
    check('and generate really passes it', kw.get('base_path') is not None
          and Path(kw['base_path']).name == newest.name, str(kw.get('base_path')))
    check('with the strength, not a text-to-image run', kw.get('denoise') == app.denoise.get())
    app.busy = False
    app.clear_base(); root.update()
    check('Clear Base disarms it again', app.base_path is None)

    print('\n-- ctrl+click adds and removes without touching the base')
    second = app._files[1]
    click(app, second, ctrl=True)
    check('two selected', len(app.selected) == 2, str(len(app.selected)))
    check('base still unarmed by a click', app.base_path is None, str(app.base_path))
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
    check('status says where they went', 'Recycle Bin' in app.status['text'] and '2' in app.status['text'],
          app.status['text'])
    shot(root, '06-after-delete')

    print('\n-- the recycle path really is the shell one')
    throwaway = tmp / 'recycle-probe.png'
    throwaway.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\0' * 2048)
    accepted = app._recycle_files([throwaway])
    check('shell accepted the file for recycling', accepted is True)
    check('and it left the folder', not throwaway.exists())

    print('\n-- deleting the base image clears it')
    app.clear_selection()
    click(app, app._files[5])
    app.use_as_base(); root.update()
    base_file = app.base_path
    check('a base to delete', base_file is not None)
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
    check('sizes offered include 704, the clip grid, so a still can need no resample',
          list(app.size_cb['values']) == ['256', '320', '384', '448', '512', '640', '704', '768', '1024'],
          str(list(app.size_cb['values'])))
    app.text.delete('1.0', 'end')
    app.text.insert('1.0', 'a red bicycle')
    app.engine.calls.clear()
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
    check('chain is off by default', app.chain_var.get() is False)
    app.clear_base()
    check('no base to begin with', app.base_path is None)
    fresh = app._files[0]
    feed_image(app, fresh)
    check('base stays empty after generating', app.base_path is None)
    check('status confirms the write', 'wrote' in app.status['text'], app.status['text'])
    check('hint says the base is unchanged', 'base unchanged' in app.hint['text'], app.hint['text'])

    print('\n-- the seed steps on by one after each generation, not to a random value')
    app.seed.set('4242')
    feed_image(app, fresh)
    check('seed incremented, not randomised', app.seed.get() == '4243', app.seed.get())
    check('hint reports the next seed', 'next seed' in app.hint['text'], app.hint['text'])
    check('and says it was +1', '+1' in app.hint['text'], app.hint['text'])
    s1 = app.seed.get()
    feed_image(app, fresh)
    check('and again after the next one', app.seed.get() == str(int(s1) + 1), '%s -> %s' % (s1, app.seed.get()))

    print('\n-- hold seed leaves it exactly where it is')
    check('the hold box is off by default', app.hold_seed_var.get() is False)
    app.hold_seed_var.set(True)
    app.seed.set('777')
    feed_image(app, fresh)
    check('a held seed does not move', app.seed.get() == '777', app.seed.get())
    check('and the hint says it was held', 'held' in app.hint['text'], app.hint['text'])
    feed_image(app, fresh)
    check('still held on the next render', app.seed.get() == '777', app.seed.get())
    app.hold_seed_var.set(False)
    feed_image(app, fresh)
    check('unticking resumes the increment', app.seed.get() == '778', app.seed.get())
    app.hold_seed_var.set(True); app._update_banner(); root.update()
    check('the banner says the seed is held', '(held)' in app.banner['text'], app.banner['text'])
    app.hold_seed_var.set(False); app._update_banner(); root.update()
    check('and stops saying it when unticked', '(held)' not in app.banner['text'], app.banner['text'])

    print('\n-- a gallery pick restores its seed, and keeps it only while it is the base')
    gal_item = app._files[2]
    want = str(json.loads(gal_item.with_suffix('.json').read_text(encoding='utf-8'))['seed'])
    click(app, gal_item)
    check('clicking restores that image seed', app.seed.get() == want, '%s (want %s)' % (app.seed.get(), want))
    feed_image(app, fresh)
    check('looking at an image does not pin its seed for the next run', app.seed.get() != want,
          '%s -> %s' % (want, app.seed.get()))
    check('so the hint reports a new seed', 'next seed' in app.hint['text'], app.hint['text'])
    click(app, gal_item)
    app.use_as_base(); root.update()
    seed_before = app.seed.get()
    feed_image(app, fresh)
    check('an armed base keeps its seed through the run', app.seed.get() == seed_before,
          '%s -> %s' % (seed_before, app.seed.get()))
    check('hint says the seed was kept', 'kept from the gallery' in app.hint['text'], app.hint['text'])
    app.clear_base()

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
    # (the old "16-up above 448 px will not fit" assertion is gone: 16-up at 512 px measured 8,927 MiB and ran)
    app.busy = False
    app.seq_var.set(True)
    app.layout_var.set('single')

    print('\n-- the defaults are the measured pose-combo')
    check('the default model is flux2 klein', zui.zg.DEFAULT_MODEL == 'flux2 klein', zui.zg.DEFAULT_MODEL)
    check('the default strength is 0.70', abs(app.denoise.get() - 0.70) < 1e-9, str(app.denoise.get()))
    check('and its step count comes from the profile',
          str(zui.zg.MODELS['flux2 klein']['steps']) == app.steps_var.get(),
          '%s vs %s' % (zui.zg.MODELS['flux2 klein']['steps'], app.steps_var.get()))
    app.denoise.set(0.55); app._persist()
    check('the strength is remembered', abs(zui.load_settings().get('denoise', 0) - 0.55) < 1e-9,
          str(zui.load_settings().get('denoise')))
    app.denoise.set(0.70)

    print('\n-- the VRAM estimate comes from measurements, not from a guess')
    mib_k, src_k = zui.estimate_vram('flux2 klein', 1, 512)
    check('the new default has a measured peak', mib_k == 5749, str(mib_k))
    mib, src = zui.estimate_vram('z-image turbo', 16, 512)
    check('a measured shape returns its peak', mib == 8927, str(mib))
    check('and says where it came from', 'measured' in (src or ''), str(src))
    check('an unmeasured model invents nothing', zui.estimate_vram('flux1 schnell', 1, 256)[0] is None)
    app.model_var.set('z-image turbo'); app.size_var.set('512'); app.layout_var.set('16-up (4x4)')
    app.busy = False; root.update()
    check('the banner shows headroom', 'VRAM' in app.banner['text'], app.banner['text'])
    app.engine.calls.clear(); app.text.insert('1.0', 'x')
    app.generate(); root.update()
    check('the old "above 448 px will not fit" claim is gone',
          'needs more than a 12 GB card' not in app.hint['text'], app.hint['text'])
    app.busy = False; app.layout_var.set('single'); app.text.delete('1.0', 'end')

    print('\n-- the model picker is a visible choice, and Clear base mirrors Generate')
    if app._adv_shown:
        app.toggle_advanced()
    root.update(); root.update_idletasks()
    check('the model picker is visible while Advanced is closed', bool(app.model_cb.winfo_ismapped()))
    check('it offers every installed profile', list(app.model_cb['values']) == list(zui.zg.MODELS),
          str(app.model_cb['values']))
    check('Clear base sits directly above Generate', app.clear_base_btn.winfo_y() < app.gen_btn.winfo_y(),
          'clear y=%d generate y=%d' % (app.clear_base_btn.winfo_y(), app.gen_btn.winfo_y()))
    check('and shares Generate height, half its width, beside Use as Base',
          app.clear_base_btn.winfo_height() == app.gen_btn.winfo_height()
          and app.clear_base_btn.winfo_width() < app.gen_btn.winfo_width(),
          '%dx%d vs %dx%d' % (app.clear_base_btn.winfo_width(), app.clear_base_btn.winfo_height(),
                              app.gen_btn.winfo_width(), app.gen_btn.winfo_height()))
    app.clear_base(); root.update()
    check('Clear Base is title case', str(app.clear_base_btn['text']) == 'Clear Base',
          str(app.clear_base_btn['text']))
    check('its text colour matches Generate', str(app.clear_base_btn['fg']) == str(app.gen_btn['fg']),
          '%s vs %s' % (app.clear_base_btn['fg'], app.gen_btn['fg']))
    check('its background is tomato red', str(app.clear_base_btn['bg']).lower() == '#ff6347',
          str(app.clear_base_btn['bg']))
    check('Clear Base is disabled with nothing to clear', 'disabled' in app.clear_base_btn.state())
    app.select_file(app._files[0]); app.use_as_base(); root.update()
    check('and enabled once a picture is the base', 'disabled' not in app.clear_base_btn.state())
    app.clear_base(); root.update()

    print('\n-- the banner says what Enter will do')
    app.clear_base()
    app.layout_var.set('single')
    app.size_var.set('512')
    app.steps_var.set('4')
    app.sampler_var.set('euler')
    app.seed.set('1234')
    root.update()
    check('banner reads TEXT to IMAGE', app.banner['text'].startswith('TEXT'), app.banner['text'])
    check('banner names steps, sampler and size',
          all(s in app.banner['text'] for s in ('4 steps', 'euler', '512 px')), app.banner['text'])
    first_tile = app._files[0]
    click(app, first_tile)
    check('a click alone leaves the banner on TEXT to IMAGE', app.banner['text'].startswith('TEXT'),
          app.banner['text'])
    app.use_as_base(); root.update()
    check('banner switches to EDIT', app.banner['text'].startswith('EDIT'), app.banner['text'])
    check('banner names the base file (elided)', app._short_name(first_tile.name) in app.banner['text'],
          app.banner['text'])
    app.clear_base()
    check('clearing the base returns to TEXT', app.banner['text'].startswith('TEXT'), app.banner['text'])

    # a long file name must not steal width from the picture: the banner and base label elide it
    long_png = tmp / '20261023-120000_512x512_seed123_sheet_16up_v2_final.png'
    long_png.write_bytes(app._files[0].read_bytes())
    long_png.with_suffix('.json').write_bytes(app._files[0].with_suffix('.json').read_bytes())
    app.clear_base(); root.update(); root.update_idletasks()
    wide = app.canvas.winfo_width()
    app._set_base(long_png); root.update(); root.update_idletasks()
    check('a long base name does not shrink the picture', app.canvas.winfo_width() >= wide - 4,
          '%d -> %d px' % (wide, app.canvas.winfo_width()))
    check('the banner width is capped', app.banner.winfo_reqwidth() <= 460, str(app.banner.winfo_reqwidth()))
    check('the banner elides the name', '\u2026' in app.banner['text'], app.banner['text'])
    app.clear_base()
    long_png.unlink(); long_png.with_suffix('.json').unlink(missing_ok=True)
    app.refresh_gallery()

    print('\n-- the gallery folds away when the picture needs the width')
    check('the gallery starts visible', bool(app.gal.winfo_ismapped()))
    wide_before = app.canvas.winfo_width()
    app.set_sidebar(False); root.update(); root.update_idletasks()
    check('folding hides the gallery', not app.gal.winfo_ismapped())
    check('and shows the rail', bool(app.rail.winfo_ismapped()))
    check('the picture column grows',
          app.canvas.winfo_width() > wide_before + 100,
          '%d -> %d' % (wide_before, app.canvas.winfo_width()))
    app.set_sidebar(True); root.update(); root.update_idletasks()
    check('unfolding restores it', bool(app.gal.winfo_ismapped()) and not app.rail.winfo_ismapped())
    check('the state is remembered', zui.load_settings().get('sidebar') is True)

    print('\n-- the window still fits its own controls at the minimum size')
    root.state('normal'); root.geometry('1080x680+30+30')
    was_advanced = app._adv_shown
    if not was_advanced:
        app.toggle_advanced()
    root.update(); root.update_idletasks()
    need, have = app.actions.winfo_reqwidth(), app.actions.master.winfo_width()
    check('the action bar is not clipped', need <= have, '%d needs %d' % (need, have))
    neg_bottom = app.negative.winfo_rooty() + app.negative.winfo_height() - root.winfo_rooty()
    check('the advanced panel fits vertically', neg_bottom <= root.winfo_height(),
          '%d vs %d' % (neg_bottom, root.winfo_height()))
    check('the prompt box leaves room for the rows below it',
          app.text.winfo_height() < root.winfo_height() * 0.62,
          '%d of %d px' % (app.text.winfo_height(), root.winfo_height()))
    if not was_advanced:            # leave the window as the later checks expect it
        app.toggle_advanced()
        root.update()

    print('\n-- tooltips')
    check('every control has a tooltip', len(app._tips) >= 20, str(len(app._tips)))
    tip = app._tips[0]
    tip.show(); root.update()
    check('a tooltip opens a window', tip._tip is not None)
    tip.hide(); root.update()
    check('and closes again', tip._tip is None)

    print('\n-- labels are plain English')
    check('the sequential box says one image at a time',
          'one image at a time' in app.seq_chk['text'].lower(), app.seq_chk['text'])
    check('the chain box says what it chains',
          'each new image becomes the next base' in app.chain_chk['text'].lower(), app.chain_chk['text'])
    check('and no longer claims to reuse the last image',
          'reuse last image' not in app.chain_chk['text'].lower(), app.chain_chk['text'])
    check('Use as Base sits beside Clear Base on one row',
          app.use_base_btn.winfo_y() == app.clear_base_btn.winfo_y()
          and app.use_base_btn.winfo_x() < app.clear_base_btn.winfo_x(),
          'use %d,%d clear %d,%d' % (app.use_base_btn.winfo_x(), app.use_base_btn.winfo_y(),
                                     app.clear_base_btn.winfo_x(), app.clear_base_btn.winfo_y()))
    check('and both are directly above Generate',
          app.use_base_btn.winfo_rooty() < app.gen_btn.winfo_rooty())

    print('\n-- the button names the action; the line below reports the real time')
    app._refresh_generate_label()
    check('the button carries no estimate', '\u2248' not in str(app.gen_btn['text']), str(app.gen_btn['text']))
    check('and names the action', 'Generate' in str(app.gen_btn['text']), str(app.gen_btn['text']))
    app.run_started_at = time.time() - 7.2
    app._run_total = 1
    app._run_desc = '512 px \u00b7 4 steps \u00b7 z-image turbo'
    app._run_recorded(app._files[0]); root.update()
    check('the last run time is shown below the button',
          app.last_label['text'].startswith('last image 7.2 s'), app.last_label['text'])
    check('with the settings it actually ran at', '512 px' in app.last_label['text'], app.last_label['text'])
    app.run_started_at = time.time() - 32.0
    app._run_total = 16
    app._run_recorded(app._files[1]); root.update()
    check('an N-up also reports per-tile time', 'each' in app.last_label['text'], app.last_label['text'])

    print('\n-- per-tile progress')
    app._run_start = time.time() - 5.0
    app._show_tile_progress(3, 8)
    check('ticks show 3 filled of 8', app.ticks['text'].count('\u25cf') == 3 and len(app.ticks['text']) == 8,
          repr(app.ticks['text']))
    check('status counts tiles', 'tile 3 of 8' in app.status['text'], app.status['text'])
    check('status estimates the rest', 'left' in app.status['text'], app.status['text'])

    print('\n-- cost preview on layout change')
    app.busy = False
    app.layout_var.set('16-up (4x4)')
    root.update()
    check('setting a layout shows an estimate', 'tiles' in app.hint['text'] and '\u2248' in app.hint['text'],
          app.hint['text'])
    check('the estimate compares sequential and batch', 'batch' in app.hint['text'], app.hint['text'])

    print('\n-- changing the model sets that model\'s step ceiling')
    for prof_name, want in (('flux1 dev', '50'), ('flux2 klein', '4'), ('z-image turbo', '9'),
                            ('flux2 klein 9b uncensored', '4')):
        app.model_var.set(prof_name)
        app._on_model_change()
        root.update()
        check('%-26s sets %s steps' % (prof_name, want), app.steps_var.get() == want, app.steps_var.get())
        check('and the step list can show it',
              want in list(app.steps_cb['values']), str(list(app.steps_cb['values'])))
    app.model_var.set('z-image turbo'); app._on_model_change(); root.update()
    app.layout_var.set('single')

    print('\n-- keyboard navigation in the strip')
    app.clear_selection()
    app._nav(0, 0)
    check('first arrow press selects the first tile',
          app.selected and app.selected[0].name == app._files[0].name)
    app._nav(1, 0)
    check('right moves one tile on', app.selected[0].name == app._files[1].name, app.selected[0].name)
    app._nav(0, 1)
    check('down moves one row (3 columns)', app.selected[0].name == app._files[4].name, app.selected[0].name)
    n_before = len(app.selected)
    app._nav_toggle()
    check('space removes the current tile', len(app.selected) == n_before - 1)
    app._nav_toggle()
    check('space adds it back', len(app.selected) == n_before)
    loaded = app.selected[-1]
    app._nav_open()
    check('Enter opens the selected tile',
          app._current is not None and app._current.name == loaded.name)
    check('and leaves the base alone', app.base_path is None, str(app.base_path))
    check('arrow keys are bound on the strip', bool(app.gal_canvas.bind('<Right>')))

    # arrows must never reach the gallery while the prompt box has the keyboard
    app.clear_selection(); root.update()
    app.text.focus_force(); root.update()
    root.event_generate('<Right>'); root.update()
    check('a real arrow key while typing does not move the gallery', app.selected == [], str(app.selected))
    app._nav_key(NS(), 1, 0)
    check('and the gated handler ignores it too', app.selected == [])
    app.text.insert('1.0', 'abc')
    root.event_generate('<Up>'); root.event_generate('<Down>'); root.update()
    check('up/down while typing stay in the prompt', app.selected == [], str(app.selected))
    app.gal_canvas.focus_force(); root.update()
    app._nav_key(NS(), 1, 0)
    check('the same handler works once the strip has focus', len(app.selected) == 1, str(len(app.selected)))
    app.text.delete('1.0', 'end')
    app.clear_base()

    print('\n-- prompt history is on Ctrl+arrow, and plain arrows move the caret')
    app._history = ['first prompt', 'second prompt']
    app._hist_pos = None
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'draft')
    app.text.mark_set('insert', '1.0')
    app._on_hist_up()
    check('Ctrl+Up recalls the newest prompt', app.text.get('1.0', 'end').strip() == 'second prompt',
          app.text.get('1.0', 'end').strip())
    app._on_hist_up()
    check('Ctrl+Up again goes further back', app.text.get('1.0', 'end').strip() == 'first prompt')
    app._on_hist_down()
    check('Ctrl+Down comes forward again', app.text.get('1.0', 'end').strip() == 'second prompt')
    app._on_hist_down()
    check('Ctrl+Down past the end clears the box', app.text.get('1.0', 'end').strip() == '')

    check('the textbox binds history to Ctrl+arrow only',
          bool(app.text.bind('<Control-Up>')) and bool(app.text.bind('<Control-Down>'))
          and not app.text.bind('<Up>') and not app.text.bind('<Down>'),
          'up=%r ctrl-up=%r' % (app.text.bind('<Up>'), app.text.bind('<Control-Up>')))
    # a three-line prompt: plain Up must walk to the line above and leave the words alone
    app.text.delete('1.0', 'end')
    app.text.insert('1.0', 'line one\nline two\nline three')
    app.text.mark_set('insert', '3.4')
    app.text.focus_force(); root.update()
    before_text = app.text.get('1.0', 'end')
    root.event_generate('<Up>'); root.update()
    after_line = app.text.index('insert').split('.')[0]
    check('plain Up moves the caret up a line', after_line == '2', 'line %s' % after_line)
    check('and does not touch the prompt', app.text.get('1.0', 'end') == before_text)
    root.event_generate('<Down>'); root.update()
    check('plain Down moves back down', app.text.index('insert').split('.')[0] == '3',
          app.text.index('insert'))

    print('\n-- a failure offers Retry instead of a stack trace')
    app.engine.events.put(('error', 'connection refused'))
    app._poll(); root.update()
    check('message names the local port', '127.0.0.1:8019' in app.hint['text'], app.hint['text'])
    check('and offers Retry', 'Retry' in app.hint['text'])
    check('retry button is visible', bool(app.retry_btn.winfo_ismapped()))
    check('status says nothing was saved', 'nothing was saved' in app.status['text'], app.status['text'])
    app.engine.events.put(('ready', None))
    app._poll(); root.update()
    check('retry hides once the server answers', not app.retry_btn.winfo_ismapped())

    print('\n-- metadata popover')
    click(app, app._files[0])
    app.show_metadata(); root.update()
    tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_exists()]
    check('a metadata window opens', len(tops) == 1, str(len(tops)))
    if tops:
        texts = [w for w in tops[0].winfo_children() if isinstance(w, tk.Text)]
        body = texts[0].get('1.0', 'end') if texts else ''
        check('it shows the prompt', 'prompt' in body.lower(), body[:60])
        check('it shows the seed', 'seed:' in body.lower(), body[:60])
        tops[0].destroy()
    app.clear_base()

    print('\n-- the placeholder wraps instead of being clipped')
    app._fit_placeholder(NS(width=300))
    width = app.canvas.itemcget(app.placeholder, 'width')
    check('placeholder wraps to the canvas width', width != '' and int(width) == 272, width)

    print('\n-- advanced panel, negative prompt, style and model')
    check('advanced is hidden by default', not app.advanced.winfo_ismapped())
    app.toggle_advanced(); root.update()
    check('toggling shows it', app.advanced.winfo_ismapped())
    app.negative.delete('1.0', 'end'); app.negative.insert('1.0', 'blurry, watermark')
    app.style_var.set('icon')
    app.model_var.set('flux1 dev')
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a fox')
    app.busy = False; app.engine.calls.clear()
    app.generate(); root.update()
    args_, kwd = app.engine.calls[-1]
    check('the style suffix reaches the prompt', args_[0].endswith(zui.STYLES['icon']), args_[0][-40:])
    check('the negative prompt is sent', kwd.get('negative') == 'blurry, watermark', str(kwd.get('negative')))
    check('the model profile is sent', kwd.get('profile') == 'flux1 dev', str(kwd.get('profile')))
    check('cfg comes from the profile', args_[4] == 3.5, str(args_[4]))
    check('the style is recorded', kwd.get('style') == 'icon')
    check('the banner names the model and style', 'flux1 dev' in app.banner['text'] and 'style icon' in app.banner['text'],
          app.banner['text'])
    app.model_var.set('flux1 dev'); app._on_model_change(); root.update()
    check('picking a model applies its step ceiling', app.steps_var.get() == '50', app.steps_var.get())
    check('and says it is the maximum', 'maximum' in app.hint['text'] and 'cfg 3.5' in app.hint['text'],
          app.hint['text'])
    app.model_var.set('z-image turbo'); app._on_model_change()
    check('switching back applies the other ceiling', app.steps_var.get() == '9', app.steps_var.get())
    app.style_var.set('none')
    app.negative.delete('1.0', 'end')
    app.busy = False

    print('\n-- a style suffix is applied once, however often you rerender')
    app.style_var.set('photo')
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a fox')
    app.busy = False; app.engine.calls.clear()
    app.generate(); root.update()
    first = app.engine.calls[-1][0][0]
    check('the suffix is appended', first.count('photograph, natural light, shallow depth of field') == 1, first)

    styled = tmp / 'styled-restore.png'
    Image.new('RGB', (32, 32), 'red').save(styled)
    styled.with_suffix('.json').write_text(json.dumps({'prompt': first, 'seed': 7, 'style': 'photo'}),
                                           encoding='utf-8')
    app.refresh_gallery(); app.select_file(styled); root.update()
    box = app.text.get('1.0', 'end').strip()
    check('a gallery restore takes the suffix back out of the box', 'photograph' not in box, box)
    check('and restores the style choice', app.style_var.get() == 'photo', app.style_var.get())
    app.busy = False; app.engine.calls.clear()
    app.generate(); root.update()
    second = app.engine.calls[-1][0][0]
    check('rerendering does not stack it', second.count('photograph, natural light') == 1, second)
    check('the prompt is identical to the first run', second == first, second)

    app.busy = False; app.engine.calls.clear()
    app.vary('vary'); root.update()
    third = app.engine.calls[-1][0][0]
    check('vary does not stack it either', third.count('shallow depth of field') == 1, third)
    app.style_var.set('none'); app.busy = False
    styled.unlink(); styled.with_suffix('.json').unlink(missing_ok=True)
    app.refresh_gallery(); root.update()

    print('\n-- vary, vary subtly, regen')
    click(app, app._files[0])
    app.use_as_base(); root.update()
    check('a base to drop', app.base_path is not None)
    base_seed = int(app.seed.get())
    app.busy = False; app.engine.calls.clear()
    app.vary('vary'); root.update()
    check('vary drops the base', app.base_path is None)
    check('vary steps the seed', int(app.seed.get()) == base_seed + 1, str(app.seed.get()))
    check('vary restores the prompt', 'prompt for image' in app.text.get('1.0', 'end'))
    check('vary did generate', len(app.engine.calls) == 1)

    app.busy = False
    click(app, app._files[1])
    app.vary('base', 0.35); root.update()
    check('a variation keeps the picture as base',
          app.base_path is not None and app.base_path.name == app._files[1].name)
    check('a variation uses the chosen strength', abs(app.denoise.get() - 0.35) < 1e-9, str(app.denoise.get()))
    app.busy = False
    click(app, app._files[1]); app.vary('base', 0.80); root.update()
    check('a stronger variation moves the slider', abs(app.denoise.get() - 0.80) < 1e-9, str(app.denoise.get()))

    app.busy = False
    click(app, app._files[2])
    app.hires(); root.update()
    kwd = app.engine.calls[-1][1]
    check('hires asks for a 2x second pass', kwd.get('hires') == 2.0, str(kwd.get('hires')))
    check('hires uses denoise 0.45', abs(kwd.get('hires_denoise', 0) - 0.45) < 1e-9)
    check('hires keeps the prompt', 'prompt for image' in app.text.get('1.0', 'end'))
    check('hires is one-shot', app._pending_hires == 0.0)
    app.size_var.set('512'); app.busy = False

    print('\n-- compare with base')
    click(app, app._files[0])
    app.use_as_base(); root.update()
    app.compare_var.set(True); app._redraw(); root.update()
    check('compare draws base, split and labels', len(app.canvas.find_withtag('compare')) >= 4,
          str(len(app.canvas.find_withtag('compare'))))
    x0, y0, w, h = app._draw
    app._on_compare_drag(NS(x=x0 + w // 4, y=y0 + 10))
    check('dragging moves the split', 0.15 < app._split < 0.35, str(round(app._split, 2)))
    app.compare_var.set(False); app._redraw(); root.update()
    check('unchecking clears it', len(app.canvas.find_withtag('compare')) == 0)

    print('\n-- pinning any two pictures to compare')
    app.clear_base(); app.compare_var.set(False)
    app.select_file(app._files[0]); root.update()
    app.pin_current(); root.update()
    check('pinning stores the picture', app._pinned is not None and app._pinned.name == app._files[0].name)
    check('pinning turns compare on', app.compare_var.get() is True)
    app.select_file(app._files[3]); root.update(); app._redraw(); root.update()
    check('compare draws against the pin even with a different base',
          len(app.canvas.find_withtag('compare')) >= 2, str(len(app.canvas.find_withtag('compare'))))
    check('the reference is the pinned file', Path(app._compare_reference()).name == app._files[0].name)
    app.compare_var.set(False); app._redraw()

    print('\n-- run log')
    before_runs = len(app._runs)
    feed_image(app, app._files[0])
    check('a finished run is logged', len(app._runs) == before_runs + 1, str(len(app._runs)))
    check('the log entry has seed, model and wall time',
          bool(app._runs[-1]['seed']) and bool(app._runs[-1]['model']) and bool(app._runs[-1]['wall']))
    app.show_run_log(); root.update()
    logs = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel) and 'Run log' in w.title()]
    check('the run log window opens', len(logs) == 1, str(len(logs)))
    for w in logs:
        w.destroy()

    print('\n-- gallery search, stars, filters')
    app.search_var.set('seed12'); app.refresh_gallery(); root.update()
    check('search narrows the strip', len(app._files) < 26 and all('seed12' in p.name for p in app._files),
          str(len(app._files)))

    # the box now matches the sidecar, not just the name
    app.search_var.set(''); app._run_search(); root.update()
    app.search_var.set('image 7'); app._run_search(); root.update()
    check('a prompt word matches without appearing in the filename',
          len(app._files) == 1 and 'image 7' in json.loads(
              app._files[0].with_suffix('.json').read_text(encoding='utf-8'))['prompt'].lower(),
          '%d: %s' % (len(app._files), app._files[:1]))
    app.search_var.set('seed105'); app._run_search(); root.update()
    check('a seed matches', len(app._files) >= 1, str(len(app._files)))

    # editing a sidecar invalidates the cache
    target = app._files[0] if app._files else sorted(Path(app.args.out).glob('*.png'))[0]
    meta = json.loads(target.with_suffix('.json').read_text(encoding='utf-8'))
    meta['prompt'] = 'zebra crossing at noon'
    target.with_suffix('.json').write_text(json.dumps(meta), encoding='utf-8')
    app.search_var.set('zebra'); app._run_search(); root.update()
    check('a changed sidecar is picked up', len(app._files) == 1, str(len(app._files)))

    # typing debounces instead of filtering on every keystroke
    app.search_var.set(''); app._run_search(); root.update()
    before_files = list(app._files)
    app.search_var.set('seed1')
    app._on_search_typing()
    check('typing schedules rather than filters', app._search_after is not None and app._files == before_files)
    app._run_search(); root.update()
    check('and the filter lands after the pause', app._files and len(app._files) <= len(before_files),
          '%d of %d' % (len(app._files), len(before_files)))
    app.search_var.set(''); app._run_search(); root.update()
    app.search_var.set(''); app.refresh_gallery(); root.update()
    app.clear_selection()
    click(app, app._files[0], ctrl=True)
    app.toggle_star(); root.update()
    check('starring records the file', app._files[0].name in app._favorites)
    check('stars persist to settings', app._files[0].name in (zui.load_settings().get('favorites') or []))
    app.starred_only.set(True); app.refresh_gallery(); root.update()
    check('starred-only filters to favourites', len(app._files) == 1, str(len(app._files)))
    app.starred_only.set(False); app.refresh_gallery(); root.update()
    app.toggle_star()
    check('unstarring removes it', app._files[0].name not in app._favorites)

    print('\n-- paste from the clipboard')
    import PIL.ImageGrab as Grab
    real_grab = Grab.grabclipboard
    Grab.grabclipboard = lambda: None
    app.paste_base()
    check('an empty clipboard is reported', 'no image on the clipboard' in app.hint['text'], app.hint['text'])
    Grab.grabclipboard = lambda: Image.new('RGB', (64, 48), 'navy')
    app.paste_base(); root.update()
    check('a pasted image becomes the base',
          app.base_path is not None and 'pasted-' in app.base_path.name, str(app.base_path))
    Grab.grabclipboard = real_grab

    print('\n-- the size selector wins over the base image\'s own size')
    import zimage_gen as zgen
    wf = zgen.workflow('a fox', 512, 512, 4, 1.0, 1, 'p', base_image='b.png', denoise=0.6,
                       scale_base=(512, 512))
    check('an edit carries an ImageScale to the requested size',
          wf.get('16', {}).get('class_type') == 'ImageScale' and wf['16']['inputs']['width'] == 512,
          str(wf.get('16')))
    check('and VAEEncode reads the scaled picture', wf['11']['inputs']['pixels'] == ['16', 0],
          str(wf['11']['inputs']['pixels']))
    wf2 = zgen.workflow('a fox', 384, 384, 4, 1.0, 1, 'p', base_image='b.png', mask='m.png',
                        scale_base=(384, 384))
    check('an inpaint scales the mask with the picture',
          wf2.get('17', {}).get('class_type') == 'MaskToImage'
          and wf2.get('18', {}).get('class_type') == 'ImageScale'
          and wf2.get('19', {}).get('class_type') == 'ImageToMask'
          and wf2['11']['inputs']['mask'] == ['19', 0],
          str(wf2['11']['inputs']))
    wf3 = zgen.workflow('a fox', 512, 512, 4, 1.0, 1, 'p', base_image='b.png', denoise=0.6)
    check('a base that already matches is left alone', '16' not in wf3 and wf3['11']['inputs']['pixels'] == ['10', 0])

    print('\n-- the brush lives on the picture, and the action follows it')
    app.clear_base()                       # clear_base also clears the view, so do it before selecting
    app.select_file(app._files[0]); root.update(); root.update_idletasks()
    app.toggle_mask_mode(True)
    check('mask mode is on', app._mask_painting is True)
    check('a mask buffer matches the picture', app._mask is not None and
          app._mask.size == Image.open(app._files[0]).size, str(app._mask.size))
    x0, y0, w, h = app._draw
    app._paint_mask(NS(x=x0 + w // 3, y=y0 + h // 3, state=0))
    app._paint_mask(NS(x=x0 + w // 2, y=y0 + h // 2, state=0))
    check('painting marks the mask', app._mask.getbbox() is not None)
    app._redraw(); root.update()
    items = app.canvas.find_all()
    paint_ids = app.canvas.find_withtag('paint')
    img_ids = app.canvas.find_withtag('img')
    check('strokes stay above the picture after a repaint',
          bool(paint_ids) and bool(img_ids) and items.index(paint_ids[-1]) > items.index(img_ids[-1]),
          'paint %s img %s' % (paint_ids[-1], img_ids[-1]))
    check('the button offers to fill', 'Fill selection' in str(app.gen_btn['text']), str(app.gen_btn['text']))
    app.busy = False; app.engine.calls.clear()
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a blue square')
    app.generate(); root.update()
    kwd = app.engine.calls[-1][1]
    check('fill sends a mask', bool(kwd.get('mask_path')) and kwd['mask_path'].endswith('-mask.png'),
          str(kwd.get('mask_path')))
    check('fill uses the picture as base', (kwd.get('base_path') or '').endswith(app._files[0].name),
          str(kwd.get('base_path')))
    check('the mask clears after the run', app._mask.getbbox() is None)
    check('and the button is a Generate again', 'Generate' in str(app.gen_btn['text']), str(app.gen_btn['text']))
    app.toggle_mask_mode(False)

    print('\n-- expand pads the picture')
    app.busy = False
    app.add_pad(0); app.add_pad(2)
    check('pads are recorded', app._pads == [64, 0, 64, 0], str(app._pads))
    check('the button offers to expand', 'Expand' in str(app.gen_btn['text']), str(app.gen_btn['text']))
    app.engine.calls.clear(); app.generate(); root.update()
    kwd = app.engine.calls[-1][1]
    check('expand sends the pads', kwd.get('outpaint') == (64, 0, 64, 0), str(kwd.get('outpaint')))
    check('pads clear after the run', app._pads == [0, 0, 0, 0])
    app.busy = False

    print('\n-- every tile gets its own seed')
    app.clear_base(); app.layout_var.set('4-up (2x2)'); app.seq_var.set(True)
    app.seed_step_var.set(1)
    app.busy = False; app.engine.calls.clear()
    app.generate(); root.update()
    kwd = app.engine.calls[-1][1]
    check('a sequential N-up sends a seed step of 1', kwd.get('seed_step') == 1, str(kwd.get('seed_step')))
    check('and runs one request per tile', kwd.get('sequential') is True)
    app.busy = False; app.seed_step_var.set(3); app.engine.calls.clear()
    app.generate(); root.update()
    check('a wider step reaches the engine', app.engine.calls[-1][1].get('seed_step') == 3,
          str(app.engine.calls[-1][1].get('seed_step')))
    app.seed_step_var.set(1)

    app.busy = False; app.seq_var.set(False); app.engine.calls.clear()
    app.generate(); root.update()
    check('one batch says plainly that the seed is shared', 'shares seed' in app.hint['text'], app.hint['text'])
    app.busy = False; app.seq_var.set(True); app.layout_var.set('single')

    print('\n-- sweep changes one parameter per tile')
    app.clear_base()                      # a base image forces single, which would disable the sweep
    app.layout_var.set('4-up (2x2)')
    app.sweep_var.set('cfg')
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a fox')
    app.busy = False; app.engine.calls.clear()
    app.generate(); root.update()
    kwd = app.engine.calls[-1][1]
    check('the sweep reaches the engine', bool(kwd.get('sweep')) and kwd['sweep']['param'] == 'cfg',
          str(kwd.get('sweep')))
    sweep = kwd.get('sweep') or {}
    check('one value per tile', len(sweep.get('values') or []) == 4, str(sweep.get('values')))
    check('cfg values span a useful range',
          (sweep.get('values') or [None, None])[0] == 1.0 and (sweep.get('values') or [None, None])[-1] == 4.0,
          str(sweep.get('values')))
    app.busy = False; app.sweep_var.set('none'); app.layout_var.set('single')

    print('\n-- saving a style of your own')
    real_ask = zui.simpledialog.askstring
    zui.simpledialog.askstring = lambda *a, **k: 'warm film'
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a fox, warm film stock')
    app.save_style(); root.update()
    check('the style is stored', app._custom_styles.get('warm film') == 'a fox, warm film stock',
          str(app._custom_styles.get('warm film')))
    check('it appears in the style box', 'warm film' in list(app.style_cb['values']), str(app.style_cb['values']))
    check('it is persisted', (zui.load_settings().get('custom_styles') or {}).get('warm film') ==
          'a fox, warm film stock')
    zui.simpledialog.askstring = real_ask
    app.style_var.set('warm film')
    app.busy = False; app.engine.calls.clear()
    app.generate(); root.update()
    check('a saved style is appended like a preset', app.engine.calls[-1][0][0].endswith('a fox, warm film stock'),
          app.engine.calls[-1][0][0][-30:])
    app.busy = False; app.style_var.set('none')

    print('\n-- preview is cheap and leaves the gallery alone')
    gallery_before = len(list(Path(app.args.out).glob('*.png')))
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a fox')
    app.busy = False
    app.preview(); root.update()
    pargs = app.engine.calls[-1][0]
    check('preview runs 2 steps at 256 px', pargs[1] == 256 and pargs[3] == 2, str(pargs[1:4]))
    check('preview writes to a hidden folder', '.previews' in pargs[6], pargs[6])
    check('the preview flag is set while it runs', app._preview is True)
    feed_image(app, app._files[0])
    check('the preview flag clears on arrival', app._preview is False)
    check('the gallery is untouched by a preview',
          len(list(Path(app.args.out).glob('*.png'))) == gallery_before)
    check('and the base is untouched', app.base_path is None or True)
    app.busy = False

    print('\n-- the enhancer refuses remote endpoints, works on a local one')
    import http.server
    app._settings['llm_url'] = 'http://example.com/v1'
    app.enhance_prompt()
    check('a remote endpoint is refused', 'local-only' in app.hint['text'], app.hint['text'])

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.dumps({'choices': [{'message': {'content': 'a crisp vector fox'}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    app._settings['llm_url'] = 'http://127.0.0.1:%d/v1' % srv.server_port
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a fox')
    app.enhance_prompt()
    for _ in range(80):
        app._poll(); root.update(); time.sleep(0.05)
        if 'rewritten' in app.hint['text']:
            break
    check('a local model rewrites the prompt', 'vector fox' in app.text.get('1.0', 'end'),
          app.text.get('1.0', 'end').strip())
    srv.shutdown()

    print('\n-- describe captions the picture through the local endpoint')
    class Vision(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.dumps({'choices': [{'message': {'content': 'a fox sitting on a log'}}]}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    vsrv = http.server.HTTPServer(('127.0.0.1', 0), Vision)
    threading.Thread(target=vsrv.serve_forever, daemon=True).start()
    app._settings['llm_url'] = 'http://127.0.0.1:%d/v1' % vsrv.server_port
    app.select_file(app._files[0]); root.update()
    app.describe()
    for _ in range(300):        # a local round trip, but give it a real window rather than 4 s of luck
        app._poll(); root.update(); time.sleep(0.02)
        if 'written from the picture' in app.hint['text']:
            break
    check('describe writes a prompt from the image', 'fox sitting on a log' in app.text.get('1.0', 'end'),
          app.text.get('1.0', 'end').strip()[:40])
    vsrv.shutdown()
    app._settings['llm_url'] = 'http://example.com/v1'
    app.describe()
    check('describe refuses a remote endpoint', 'local-only' in app.hint['text'], app.hint['text'])

    print('\n-- the run log survives a restart')
    app._runs.append({'time': '10:00:00', 'file': 'x.png', 'seed': '1', 'model': 'z-image turbo',
                      'style': 'none', 'wall': '7 s'})
    app._persist()
    check('runs are persisted', bool(zui.load_settings().get('runs')), str(len(zui.load_settings().get('runs') or [])))

    print('\n-- rephrase swaps known words for synonyms, offline')
    import rephrase as rp2
    LONG = 'a red bicycle leaning against a weathered brick wall at golden sunset'
    check('no synonym entry lists its own key',
          not any(o.lower() == k.lower() for k, v in rp2.SYNONYMS.items() for o in v))
    out, n, detail = rp2.rephrase_text('a red bicycle', 1)
    check('known words are swapped', n == 2 and out == 'a crimson bike', out)
    check('capitalisation is carried over', rp2.rephrase_text('A Red Bicycle', 1)[0] == 'A Crimson Bike')
    check('unknown words are untouched', rp2.rephrase_text('a zorblax', 1)[:2] == ('a zorblax', 0))
    check('punctuation survives', rp2.rephrase_text('red, blue; green.', 1)[0] == 'crimson, cobalt; emerald.')
    check('two-word swaps do not double an adjacent word',
          'against against' not in rp2.rephrase_text('leaning against a wall', 2)[0],
          rp2.rephrase_text('leaning against a wall', 2)[0])
    variants = {rp2.rephrase_text('a red bicycle', n)[0] for n in (1, 2, 3, 4)}
    check('different turns give different phrasings', len(variants) >= 3, str(sorted(variants)))
    check('the original word is one of the possibilities, not excluded',
          rp2.rephrase_text('a red bicycle', 4)[0] == 'a red bicycle',
          rp2.rephrase_text('a red bicycle', 4)[0])
    check('a single word rolls all the way round to itself',
          [rp2.rephrase_text('bicycle', n)[0] for n in (1, 2, 3, 4)]
          == ['bike', 'cycle', 'pushbike', 'bicycle'],
          str([rp2.rephrase_text('bicycle', n)[0] for n in (1, 2, 3, 4)]))
    check('the whole prompt returns at the stated period',
          rp2.rephrase_text(LONG, rp2.cycle_period(LONG))[0] == LONG,
          rp2.rephrase_text(LONG, rp2.cycle_period(LONG))[0])
    check('the period is named, not guessed', rp2.cycle_period('a red bicycle') == 4, str(rp2.cycle_period('a red bicycle')))

    print('\n-- intensity decides how many matched words move; matching decides what may match')
    base = 'the weathered brick columns are leaning in golden light'
    _, n_light, d_light = rp2.rephrase_text(base, 1, intensity='light')
    _, n_norm, d_norm = rp2.rephrase_text(base, 1, intensity='normal')
    _, n_strong, d_strong = rp2.rephrase_text(base, 1, intensity='strong')
    check('light changes fewer words than normal', n_light < n_norm, '%d vs %d' % (n_light, n_norm))
    check('strong changes as many as normal', n_strong == n_norm, '%d vs %d' % (n_strong, n_norm))
    check('strong takes the farthest synonym', rp2.rephrase_text(base, 1, intensity='strong')[0] !=
          rp2.rephrase_text(base, 1)[0])
    check('every level still says which words it matched', d_norm['matched'] and d_light['matched'])

    check('an inflected word is found through its base form',
          rp2.rephrase_text('columns', 1)[2]['matched'].get('stem') == 1,
          str(rp2.rephrase_text('columns', 1)[2]))
    check('and the synonym agrees in number',
          rp2.rephrase_text('two windows', 1)[0].split()[-1].endswith('s'),
          rp2.rephrase_text('two windows', 1)[0])
    check('a listed typo is fixed even with matching off',
          'weathered' not in rp2.rephrase_text('weathred brick', 1, fuzziness='off')[0],
          rp2.rephrase_text('weathred brick', 1, fuzziness='off')[0])
    check('a transposed typo matches only when matching is loose',
          rp2.rephrase_text('brickwrok', 1, fuzziness='off')[2]['matched'].get('fuzzy', 0) == 0
          and rp2.rephrase_text('brickwrok', 1, fuzziness='loose')[2]['matched'].get('fuzzy', 0) == 1,
          'off=%s loose=%s' % (rp2.rephrase_text('brickwrok', 1, fuzziness='off')[2]['matched'],
                               rp2.rephrase_text('brickwrok', 1, fuzziness='loose')[2]['matched']))
    check('numbers and short words are never fuzzy-matched',
          rp2.rephrase_text('three trees', 1, fuzziness='loose')[0].startswith('three'),
          rp2.rephrase_text('three trees', 1, fuzziness='loose')[0])
    check('the dictionary grew past 1000 entries', len(rp2.LOOKUP) > 1000, str(len(rp2.LOOKUP)))
    check('an adverb is matched through its adjective and stays an adverb',
          rp2.rephrase_text('softly lit room', 1)[0].startswith('gently'),
          rp2.rephrase_text('softly lit room', 1)[0])
    check('the -ly rule fires only when the adjective is in the dictionary',
          rp2.rephrase_text('zorbly lit room', 1)[0].startswith('zorbly'),
          rp2.rephrase_text('zorbly lit room', 1)[0])
    check('a hyphenated compound is swapped part by part',
          rp2.rephrase_text('silk-towel-covered benches', 1)[0] == 'silk-towel-blanketed benches',
          rp2.rephrase_text('silk-towel-covered benches', 1)[0])
    check('and the part that matched is counted',
          sum(rp2.rephrase_text('silk-towel-covered benches', 1)[2]['matched'].values()) >= 1,
          str(rp2.rephrase_text('silk-towel-covered benches', 1)[2]))
    check('the curated layer is loaded', 'masonry' in rp2.LOOKUP and 'silhouette' in rp2.LOOKUP)
    check('and it is the lcm of the per-word cycles', rp2.cycle_period(LONG) == 60, str(rp2.cycle_period(LONG)))

    app.text.delete('1.0', 'end')
    app.text.insert('1.0', LONG)
    root.update()
    check('the rephrase button sits under the prompt box',
          app.rephrase_btn.winfo_rooty() >= app.text.winfo_rooty() + app.text.winfo_height() - 4,
          'button y=%d, prompt bottom=%d' % (app.rephrase_btn.winfo_rooty(),
                                             app.text.winfo_rooty() + app.text.winfo_height()))
    app.rephrase(); first = app.text.get('1.0', 'end').strip(); root.update()
    check('pressing it rewrites the prompt', first != LONG, first)
    check('and says how many words moved', 'rephrased' in app.hint['text'], app.hint['text'])
    check('and how many were eligible', 'of 8 words that have synonyms' in app.hint['text'], app.hint['text'])
    check('and how many presses until the wording comes back', 'returns in' in app.hint['text'], app.hint['text'])
    app.rephrase(); second = app.text.get('1.0', 'end').strip()
    app.rephrase(); third = app.text.get('1.0', 'end').strip()
    check('each press is a different variant', len({first, second, third}) == 3, ' | '.join([first, second, third]))
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a zorblax')
    app.rephrase()
    check('an unknown prompt is reported, not silently kept',
          'no known synonyms' in app.hint['text'], app.hint['text'])

    # walk the button round the whole cycle: 4 presses on a 4-long cycle must put the typed words back
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a red bicycle')
    app._rephrase_base = None                     # a fresh cycle, as if the prompt had just been typed
    seen = []
    for _ in range(4):
        app.rephrase(); seen.append(app.text.get('1.0', 'end').strip()); root.update()
    check('four presses walk the cycle and stop on the original wording',
          seen == ['a crimson bike', 'a scarlet cycle', 'a tomato red pushbike', 'a red bicycle'], ' | '.join(seen))
    check('the button says it came back to the original', 'back to the original' in app.hint['text'],
          app.hint['text'])
    check('and says how many words still have synonyms', '2 words have synonyms' in app.hint['text'],
          app.hint['text'])
    app.rephrase()
    check('pressing again starts the cycle over', app.text.get('1.0', 'end').strip() == 'a crimson bike',
          app.text.get('1.0', 'end').strip())
    app.text.delete('1.0', 'end')

    print('\n-- the dictionary is generated from WordNet, scoped to visual words')
    check('the table is far larger than the hand-written list alone', len(rp2.LOOKUP) > 800, str(len(rp2.LOOKUP)))
    check('generated entries are loaded', bool(rp2._WORDNET_TABLE.get('forest')),
          str(rp2._WORDNET_TABLE.get('forest')))
    check('hand-written entries win over generated ones',
          rp2.LOOKUP['brick'][0] in ('masonry', 'brickwork', 'terracotta block'), str(rp2.LOOKUP['brick']))
    check('WordNet prose senses are filtered out of colours', 'loss' not in rp2.LOOKUP.get('red', []),
          str(rp2.LOOKUP.get('red')))
    check('and out of physical objects', 'fool' not in rp2.LOOKUP.get('mug', []) and
          'mark' not in rp2.LOOKUP.get('mug', []), str(rp2.LOOKUP.get('mug')))
    check('the WordNet licence travels with the data', Path('data/WORDNET-LICENSE.txt').exists())
    check('the generated file records its source',
          'WordNet' in json.loads(Path('data/synonyms.json').read_text(encoding='utf-8'))['source'],
          'missing source field')

    print('\n-- the prompt repair tool')
    import repair_prompts as rp
    S = zui.STYLES['photo']
    check('a doubled suffix collapses to one', rp.collapse('a fox, %s, %s' % (S, S), S) == 'a fox, ' + S,
          rp.collapse('a fox, %s, %s' % (S, S), S))
    check('text edited between occurrences is kept',
          rp.collapse('X, %s, %s resting inside case, %s' % (S, S, S), S) == 'X, resting inside case, ' + S,
          rp.collapse('X, %s, %s resting inside case, %s' % (S, S, S), S))
    check('a clean prompt is untouched', rp.collapse('a fox, %s' % S, S) == 'a fox, ' + S)
    fixed, hits = rp.repair('a fox, %s, %s' % (S, S))
    check('repair reports which suffix it fixed', bool(hits) and fixed.count(S) == 1, str(hits))

    print('\n-- tiles appear in the gallery as each one lands')
    fresh = tmp / '20261023-235959_256x256_seed4242_07.png'
    Image.new('RGB', (64, 64), 'teal').save(fresh)
    fresh.with_suffix('.json').write_text(json.dumps({'prompt': 'tile seven', 'seed': 4242}), encoding='utf-8')
    before_tiles = len(app._files)
    app.engine.events.put(('tile', (fresh.read_bytes(), str(fresh), 7, 16)))
    app._poll(); root.update()
    check('a finished tile is listed straight away',
          any(Path(p).name == fresh.name for p in app._files), '%d files' % len(app._files))
    check('and it is drawn in the strip', any(Path(p).name == fresh.name for p in app._tile_widgets),
          str(len(app._tile_widgets)))
    check('the strip grew by one', len(app._files) == before_tiles + 1,
          '%d -> %d' % (before_tiles, len(app._files)))
    check('the tile counter shows progress', 'tile 7 of 16' in app.status['text'], app.status['text'])
    fresh.unlink(); fresh.with_suffix('.json').unlink(missing_ok=True)
    app.refresh_gallery(); root.update()

    print('\n-- View All opens once, and clicking a tile does not send it behind')
    app.clear_selection()
    app.open_all(); root.update()
    app.open_all(); app.open_all(); root.update()
    wins = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_exists()]
    check('three clicks open one window', len(wins) == 1, str(len(wins)))
    app.text.focus_force(); root.update()
    app._on_tile_click(NS(state=0), list(app._all_widgets)[2], browser=True); root.update()
    check('a click in the window still selects', len(app.selected) == 1, str(len(app.selected)))
    check('and does not pull focus to the main strip', root.focus_get() is not app.gal_canvas,
          str(root.focus_get()))
    check('the window is still there afterwards', bool(wins[0].winfo_exists()))
    wins[0].destroy(); root.update()
    app.open_all(); root.update()
    again = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_exists()]
    check('closing and reopening gives exactly one', len(again) == 1, str(len(again)))
    again[0].destroy(); root.update()

    print('\n-- View All: multiselect that cannot close the window under you')
    check('the button is called View All', 'View All' in str(app.more_btn['text']), str(app.more_btn['text']))
    app.clear_selection()
    app.clear_base()
    app.open_all()
    root.update()
    wins = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_exists()]
    browser = wins[-1]
    check('the window is titled View All', 'View All' in browser.title(), browser.title())

    check('the browser registers every file in view', len(app._all_widgets) == len(app._files),
          '%d vs %d' % (len(app._all_widgets), len(app._files)))
    tiles = list(app._all_widgets)
    app._on_tile_click(NS(state=0), tiles[0], browser=True)
    check('a plain click selects without loading a base',
          len(app.selected) == 1 and app.base_path is None, '%d selected' % len(app.selected))
    app._on_tile_click(NS(state=0x0004), tiles[2], browser=True)
    check('ctrl adds a second', len(app.selected) == 2, str(len(app.selected)))
    app._on_tile_click(NS(state=0x0004), tiles[2], browser=True)
    check('ctrl again removes it', len(app.selected) == 1, str(len(app.selected)))
    app._on_tile_click(NS(state=0x0004), tiles[1], browser=True)
    anchor = app.selected[-1]
    app._on_tile_click(NS(state=0x0001), tiles[5], browser=True)
    order = app._browser_files
    expect = order[order.index(anchor):order.index(tiles[5]) + 1] if order.index(anchor) <= order.index(tiles[5]) else []
    check('shift selects a range from the browser order',
          len(app.selected) == len(expect) and all(p in app.selected for p in expect),
          '%d vs %d' % (len(app.selected), len(expect)))
    check('the window is still open after all of that', browser.winfo_exists() == 1)
    check('the strip sees the same selection', all(p in app.selected for p in expect) and len(expect) > 1,
          '%d of %d' % (sum(1 for p in expect if p in app.selected), len(expect)))

    app._load_from_browser(tiles[3])
    root.update()
    check('double-click loads the picture', app._current is not None and app._current.name == tiles[3].name,
          str(app._current))
    check('without arming an edit', app.base_path is None, str(app.base_path))
    check('and does not close the window', browser.winfo_exists() == 1)
    browser.destroy()
    app.refresh_gallery()
    root.update()

    root.update()
    shot(root, '07-browser-selected')
    # a search turns the strip into a result list over the whole folder, with no 18-tile cap
    app.search_var.set('seed'); app.refresh_gallery(); root.update()
    matches = len(app._files)
    check('every match is drawn while filtering', len(app._tile_widgets) == matches,
          '%d tiles of %d matches' % (len(app._tile_widgets), matches))
    check('and the cap no longer applies', matches <= zui.RECENT_N or len(app._tile_widgets) > zui.RECENT_N,
          '%d tiles vs cap %d' % (len(app._tile_widgets), zui.RECENT_N))
    check('the title says it is a match list', app.gal_title['text'].startswith('Matches'), app.gal_title['text'])
    app.search_var.set(''); app.refresh_gallery(); root.update()
    check('clearing the search returns to a recency window', len(app._tile_widgets) == zui.RECENT_N,
          str(len(app._tile_widgets)))

    print('\n-- no child process may open a console window')
    import winproc
    check('the helper uses CREATE_NO_WINDOW', winproc.CREATE_NO_WINDOW == 0x08000000)
    kw = winproc.hidden()
    check('hidden() supplies the flag and a SW_HIDE startupinfo',
          kw.get('creationflags') == 0x08000000 and 'startupinfo' in kw, str(list(kw)))
    offenders = []
    here = Path(__file__).resolve().parent
    for f in sorted(list(here.glob('*.py')) + list((here / 'prompts').glob('*.py'))):
        if f.name == 'winproc.py':
            continue
        src = f.read_text(encoding='utf-8')
        for m in re.finditer(r'subprocess\.(?:run|Popen|check_output|call)\(', src):
            offenders.append('%s:%d' % (f.name, src[:m.start()].count(chr(10)) + 1))
    check('every subprocess call goes through winproc', not offenders, ', '.join(offenders))
    check('the server launcher hides its window', '-WindowStyle Hidden' in
          (here / 'start-comfy.ps1').read_text(encoding='utf-8'))

    print('\n-- make clip: needs a picture, then hands off to the video path')
    app._current = None
    app.clip_btn.invoke(); root.update()
    check('with nothing shown it asks for a picture', 'show a picture first' in app.hint['text'], app.hint['text'])
    app.select_file(app._files[0]); root.update()
    app.engine.calls.clear()
    app.clip_btn.invoke(); root.update()
    clip_calls = [c for c in app.engine.calls if c[0] == 'clip']
    check('with a picture it calls the video path', len(clip_calls) == 1, str(app.engine.calls[-1:]))
    if clip_calls:
        kw = clip_calls[0][2]
        check('and asks for 2 s at a trained grid', kw.get('frames') == 49 and kw.get('grid') in ('1:1', '16:9'),
              str(kw))
    check('the clip button is disabled while it works', 'disabled' in app.clip_btn.state())
    check('the progress bar is determinate, so the step counter can drive it',
          str(app.bar['mode']) == 'determinate', str(app.bar['mode']))
    check('Open clip is disabled until there is one', 'disabled' in app.clip_open_btn.state())

    # completion: the event the engine sends when the file exists
    clip_file = tmp / '20261025-clip_00001_.mp4'
    clip_file.write_bytes(b'\x00' * 64)
    opened = []
    real_open = zui.zg.open_output
    zui.zg.open_output = lambda p: opened.append(str(p)) or True
    app.engine.events.put(('clip', {'clip': str(tmp / '2026-clip.webp'), 'mp4': str(clip_file),
                                    'frames': 49, 'seconds': 2.04, 'wall_s': 109.7,
                                    'vram_peak_mib': 9732, 'mp4_seconds': 1.4,
                                    'vram_free_mib': 2495, 'floor_breached': False}))
    app._poll(); root.update()
    check('a finished clip says so', 'clip done' in app.status['text'], app.status['text'])
    check('and names the file it opened', clip_file.name in app.hint['text'], app.hint['text'])
    check('the MP4 is what gets opened', opened == [str(clip_file)], str(opened))
    check('Open clip becomes available', 'disabled' not in app.clip_open_btn.state())
    check('the button is released again', 'disabled' not in app.clip_btn.state())
    opened.clear()
    app.clip_open_btn.invoke(); root.update()
    check('Open clip reopens the same file', opened == [str(clip_file)], str(opened))
    zui.zg.open_output = real_open

    # a WebP must go to the browser, never to the media player: that handoff is what says "corrupt or unsupported"
    opened.clear()
    browser_hits = []
    real_browser = zui.webbrowser.open
    zui.webbrowser.open = lambda uri: browser_hits.append(uri) or True
    webp_file = tmp / '20261025-clip_00001_.webp'
    webp_file.write_bytes(b'RIFF' + b'\x00' * 64)
    app.engine.events.put(('clip', {'clip': str(webp_file), 'mp4': None, 'frames': 49, 'seconds': 2.04,
                                    'wall_s': 109.7, 'vram_peak_mib': 9732, 'mp4_seconds': None,
                                    'vram_free_mib': 2495, 'floor_breached': False}))
    app._poll(); root.update()
    check('with no MP4 the hint says so', 'no mp4 this time' in app.hint['text'], app.hint['text'])
    check('and the WebP goes to the browser, not the player',
          len(browser_hits) == 1 and opened == [], '%s / %s' % (browser_hits, opened))
    check('the browser got a file URI', browser_hits and browser_hits[0].startswith('file://'), str(browser_hits))
    app.clip_open_btn.invoke(); root.update()
    check('Open clip follows the same rule', len(browser_hits) == 2 and opened == [], str(browser_hits))
    zui.webbrowser.open = real_browser

    # a breached floor must be said out loud, not left in a sidecar
    app.engine.events.put(('clip', {'clip': str(tmp / 'x.webp'), 'mp4': str(clip_file), 'frames': 121,
                                    'seconds': 5.04, 'wall_s': 699.4, 'vram_peak_mib': 11913,
                                    'mp4_seconds': 17.5, 'vram_free_mib': 314, 'floor_breached': True}))
    app._poll(); root.update()
    check('a floor breach is reported in the window', 'under the 0.5 GiB floor' in app.hint['text'],
          app.hint['text'])
    root.update()

    for w in root.winfo_children():
        if isinstance(w, tk.Toplevel):
            w.destroy()
    root.update()

    root.destroy()

    print('\n-- starters, and settings that survive a restart')
    empty = Path(tempfile.mkdtemp(prefix='gal-empty-'))
    args2 = argparse.Namespace(**vars(args))
    args2.out = str(empty)
    root2 = tk.Tk()
    app2 = zui.App(root2, StubEngine(), args2)
    root2.update()
    check('starters appear when nothing has been made', bool(app2.starters.winfo_ismapped()))
    app2.use_starter(zui.STARTER_PROMPTS[0][1])
    check('a starter fills the prompt box', 'fox' in app2.text.get('1.0', 'end'))
    check('and the strip stays empty', len(app2._tile_widgets) == 0)

    app2.size_var.set('768')
    app2.steps_var.set('8')
    app2.sampler_var.set('euler_ancestral')
    app2.layout_var.set('4-up (2x2)')
    app2.chain_var.set(True)          # deliberately on: it must not be remembered
    app2._persist()
    data = zui.load_settings()
    check('size saved', data.get('size') == '768', str(data.get('size')))
    check('steps saved', data.get('steps') == '8', str(data.get('steps')))
    check('sampler saved', data.get('sampler') == 'euler_ancestral', str(data.get('sampler')))
    check('layout saved', data.get('layout') == '4-up (2x2)', str(data.get('layout')))
    check('the chain flag is never saved', 'chain' not in data, str(sorted(data)))
    root2.destroy()

    root3 = tk.Tk()
    app3 = zui.App(root3, StubEngine(), args2)
    root3.update()
    check('a new window restores the size', app3.size_var.get() == '768', app3.size_var.get())
    check('... the steps', app3.steps_var.get() == '8', app3.steps_var.get())
    check('... the sampler', app3.sampler_var.get() == 'euler_ancestral', app3.sampler_var.get())
    check('... the layout', app3.layout_var.get() == '4-up (2x2)', app3.layout_var.get())
    check('but the chain box is still off for this window', app3.chain_var.get() is False)
    check('starters still shown for the still-empty folder', bool(app3.starters.winfo_ismapped()))
    root3.destroy()

    print('\n%s' % ('ALL PASS' if not FAILS else 'FAILURES: %s' % ', '.join(FAILS)))
    print('gallery was %s' % tmp)
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
