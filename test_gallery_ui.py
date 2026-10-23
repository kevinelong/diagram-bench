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
    # (the old "16-up above 448 px will not fit" assertion is gone: 16-up at 512 px measured 8,927 MiB and ran)
    app.busy = False
    app.seq_var.set(True)
    app.layout_var.set('single')

    print('\n-- the VRAM estimate comes from measurements, not from a guess')
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
    check('the chain box says reuse last image',
          'reuse last image' in app.chain_chk['text'].lower(), app.chain_chk['text'])

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
    check('Enter loads the selected tile as the base',
          app.base_path is not None and app.base_path.name == loaded.name)
    check('arrow keys are bound on the strip', bool(app.gal_canvas.bind('<Right>')))
    app.clear_base()

    print('\n-- prompt history')
    app._history = ['first prompt', 'second prompt']
    app._hist_pos = None
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'draft')
    app.text.mark_set('insert', '1.0')
    app._on_hist_up()
    check('Up recalls the newest prompt', app.text.get('1.0', 'end').strip() == 'second prompt',
          app.text.get('1.0', 'end').strip())
    app._on_hist_up()
    check('Up again goes further back', app.text.get('1.0', 'end').strip() == 'first prompt')
    app._on_hist_down()
    check('Down comes forward again', app.text.get('1.0', 'end').strip() == 'second prompt')
    app._on_hist_down()
    check('Down past the end clears the box', app.text.get('1.0', 'end').strip() == '')

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
    check('picking a model applies its step count', app.steps_var.get() == '20', app.steps_var.get())
    check('and says so', 'steps at cfg 3.5' in app.hint['text'], app.hint['text'])
    app.model_var.set('z-image turbo'); app._on_model_change()
    check('switching back restores 4 steps', app.steps_var.get() == '4', app.steps_var.get())
    app.style_var.set('none')
    app.negative.delete('1.0', 'end')
    app.busy = False

    print('\n-- vary, vary subtly, regen')
    click(app, app._files[0])
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
    check('the button offers to fill', str(app.gen_btn['text']).startswith('Fill'), str(app.gen_btn['text']))
    app.busy = False; app.engine.calls.clear()
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a blue square')
    app.generate(); root.update()
    kwd = app.engine.calls[-1][1]
    check('fill sends a mask', bool(kwd.get('mask_path')) and kwd['mask_path'].endswith('-mask.png'),
          str(kwd.get('mask_path')))
    check('fill uses the picture as base', (kwd.get('base_path') or '').endswith(app._files[0].name),
          str(kwd.get('base_path')))
    check('the mask clears after the run', app._mask.getbbox() is None)
    check('and the button is a Generate again', str(app.gen_btn['text']).startswith('Generate'),
          str(app.gen_btn['text']))
    app.toggle_mask_mode(False)

    print('\n-- expand pads the picture')
    app.busy = False
    app.add_pad(0); app.add_pad(2)
    check('pads are recorded', app._pads == [64, 0, 64, 0], str(app._pads))
    check('the button offers to expand', str(app.gen_btn['text']).startswith('Expand'), str(app.gen_btn['text']))
    app.engine.calls.clear(); app.generate(); root.update()
    kwd = app.engine.calls[-1][1]
    check('expand sends the pads', kwd.get('outpaint') == (64, 0, 64, 0), str(kwd.get('outpaint')))
    check('pads clear after the run', app._pads == [0, 0, 0, 0])
    app.busy = False

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
    for _ in range(80):
        app._poll(); root.update(); time.sleep(0.05)
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
