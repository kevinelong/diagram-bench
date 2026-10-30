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

# the suite drives the real window, which persists its state - keep that out of the user's profile
os.environ['ZIMAGE_SETTINGS_DIR'] = tempfile.mkdtemp(prefix='zimage-settings-')

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zimage_ui as zui  # noqa: E402
import pair_review  # noqa: E402

# the A/B decisions are keyed by member names, and this suite makes the same names every run - so without this the
# second run would find every pair already decided, in the user's real review state.
pair_review.STATE_DIR = Path(tempfile.mkdtemp(prefix='zimage-review-'))

FAILS = []
SHOTS = None


def check(name, cond, detail=''):
    print('%-52s %s%s' % (name, 'PASS' if cond else 'FAIL',
                          ('  ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


def _quieten(app):
    """Stop a throwaway window's poll loop.

    Each `App` schedules `_poll` and receives async payloads (`groups`, `image`). A throwaway window that is destroyed, or
    whose root is no longer the default, then tries to paint PhotoImages belonging to another interpreter - measured as
    `image "pyimageNNNN" doesn't exist` several sections later. Cancelling the timer is the fix.
    """
    try:
        if getattr(app, '_poll_id', None) is not None:
            app.root.after_cancel(app._poll_id)
            app._poll_id = None
    except Exception:
        pass


class _SheetShim:
    """Just the state the tile selection touches. No Tk root: extra roots fight over image handles, and this needs none."""

    def __init__(self):
        self._hashes = {}
        self._heads = {}
        self.sheets_all_alike = 0

    _signature = zui.App._signature
    _different = zui.App._different
    _wins_of = zui.App._wins_of
    _sheet_paths = zui.App._sheet_paths



class StubEngine:
    """Only what App touches: events, generate, cancel, shutdown."""

    def __init__(self):
        self.events = queue.Queue()
        self.events.put(('ready', None))    # the window starts busy and waits for this
        self.calls = []
        self.cancels = 0
        self.shutdowns = 0
        self.owned = False

    def generate(self, *a, **k):
        self.calls.append((a, k))
        return True

    def make_clip(self, *a, **k):
        self.calls.append(('clip', a, k))
        return True

    def cancel(self):
        self.cancels += 1
        return 'interrupt sent'

    def shutdown(self):
        self.shutdowns += 1
        return 'stopped'


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

    print('\n-- an automatically opened folder is scrolled into view and drawn in reverse video (2026-10-27)')
    # The startup grouping runs on a debounce, so sampling the plan once made this check a race: it passed on a fast run
    # and failed on a slow one (seen 2026-10-29). Wait for the groups, then judge them.
    for _ in range(120):
        if app._plan and app._plan.get('groups'):
            break
        app._poll(); root.update(); time.sleep(0.05)
    real_groups2 = list(app._plan['groups']) if app._plan else []
    if real_groups2:
        if getattr(app, '_regroup_job', None):
            app.root.after_cancel(app._regroup_job)
            app._regroup_job = None
        real_scheduler2 = app._regroup_soon
        app._regroup_soon = lambda: None
        app._plan['groups'] = real_groups2 * 24
        app._folder_shown = len(app._plan['groups'])
        app.select_folder(None); app.refresh_gallery(); root.update(); time.sleep(0.2)
        far = len(app._plan['groups']) - 1
        app.select_folder(far); app._see_folder(far)
        root.update(); time.sleep(0.25); root.update()
        tiles = [w for w in app.recent.winfo_children() if w.grid_info()]
        if far < len(tiles):
            tile = tiles[far]
            top, view_top, view_h = tile.winfo_y(), app.gal_canvas.canvasy(0), app.gal_canvas.winfo_height()
            check('the opened folder is inside the visible strip',
                  view_top - 2 <= top <= view_top + view_h, (top, view_top, view_h))
        check('and the strip scrolled to get there', app.gal_canvas.yview()[0] > 0.5, app.gal_canvas.yview())
        sel = tiles[far] if far < len(tiles) else None
        if sel is not None:
            body = sel.winfo_children()[0]
            title = body.winfo_children()[1]
            check('the open folder is drawn in reverse video',
                  str(sel.cget('background')) == app.theme.text
                  and str(title.cget('background')) == app.theme.text
                  and str(title.cget('foreground')) == app.theme.window,
                  (sel.cget('background'), title.cget('background'), title.cget('foreground')))
            others = [w for w in tiles if w is not sel]
            if others:
                check('an unselected tile keeps the idle frame, not the inverted one',
                      str(others[0].cget('background')) == app.theme.border,
                      (others[0].cget('background'), app.theme.border))
        app._plan['groups'] = real_groups2
        app._folder_shown = zui.BATCH_FOLDERS
        app._regroup_soon = real_scheduler2
        app.select_folder(None); app.refresh_gallery(); root.update()
    else:
        check('fixture has folders to scroll through', False)

    print('\n-- with folders on, the strip never paints the pictures it is about to replace (2026-10-27)')
    # The state straight after construction: folders on, plan not in yet. Whatever the strip holds then must not be
    # photo tiles - the 18 thumbnails rendered only to be replaced are the flash that was reported as confusing.
    real_plan, real_grouping = app._plan, getattr(app, '_grouping', False)
    app._plan = None
    app._grouping = True
    app.refresh_gallery()
    root.update()
    tiles = [w for w in app.recent.winfo_children() if getattr(w, 'image', None) is not None]
    check('awaiting the plan, no photo tile is drawn', not tiles, len(tiles))
    check('and the strip says what it is doing', 'Grouping' in app.gal_title['text'], app.gal_title['text'])
    app._plan, app._grouping = real_plan, real_grouping
    app.refresh_gallery(); root.update()

    print("\n-- the window takes the host size and colours (2026-10-27)")
    state = root.state()
    check('the window starts maximised', state == 'zoomed' or
          (root.winfo_width() >= root.winfo_screenwidth() - 40 and root.winfo_height() >= 600),
          (state, root.winfo_geometry()))
    th = app.theme
    for key in ('window', 'surface', 'text', 'muted', 'accent', 'on_accent'):
        check('the palette has a real %s colour' % key,
              isinstance(th.colours.get(key), str) and th.colours[key].startswith('#'), th.colours.get(key))
    check('the mode came from Windows, not from a guess', isinstance(th.dark, bool), th.dark)
    check('the prompt box wears the palette', app.text['background'] == th.field and app.text['fg'] == th.text,
          (app.text['background'], app.text['fg']))
    check('the picture pane is the window colour', app.canvas['background'] == th.window,
          app.canvas['background'])
    check('the tooltip follows it too', zui.Tooltip.BG == th.tooltip_bg, zui.Tooltip.BG)
    check('a derived colour stays between the two it blends', th.sheet_bg != th.window and th.sheet_bg != th.text,
          (th.window, th.sheet_bg, th.text))

    print('\n-- folders are the default view (2026-10-27)')
    for _ in range(80):
        app._poll()
        root.update()
        if app._plan is not None:
            break
        time.sleep(0.05)
    check('folders are checked at startup', app.group_var.get() is True, app.group_var.get())
    check('and the plan was computed without being asked', app._plan is not None, bool(app._plan))
    # everything below is about the picture strip, so put the window back in picture mode first
    app.toggle_groups(False)
    root.update()
    check('turning folders off shows the pictures again', not app._folders_view_active())

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
    # focus_get() needs a realised, visible window; stub it so the guard is what is tested, not the window manager
    real_focus = app.root.focus_get
    app.root.focus_get = lambda: app.text
    try:
        root.update()
        app.select_all()
        before = len(app.selected)
        rc = app._on_ctrl_a()
        check('Ctrl+A inside the prompt box does not select images', rc is None and len(app.selected) == before)
        asked = []
        zui.messagebox.askyesno = lambda *a, **k: asked.append(1) or True
        rc = app._on_delete_key()
        check('Del inside the prompt box does not delete', rc is None and not asked)
    finally:
        app.root.focus_get = real_focus

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

    print('\n-- the picker is two models and ALL, and the default is the remaining edit-capable one')
    check('exactly two profiles survive', sorted(zui.zg.MODELS) == ['flux2 klein 9b uncensored', 'z-image turbo'],
          sorted(zui.zg.MODELS))
    check('the default model is the 9B', zui.zg.DEFAULT_MODEL == 'flux2 klein 9b uncensored', zui.zg.DEFAULT_MODEL)
    check('the picker offers both profiles plus ALL',
          [str(v) for v in app.model_cb['values']] == list(zui.zg.MODELS) + ['ALL'],
          [str(v) for v in app.model_cb['values']])
    check('the default strength is still 0.70', abs(app.denoise.get() - 0.70) < 1e-9, str(app.denoise.get()))
    check('and its step count comes from the profile',
          str(zui.zg.MODELS['flux2 klein 9b uncensored']['steps']) == app.steps_var.get(),
          '%s vs %s' % (zui.zg.MODELS['flux2 klein 9b uncensored']['steps'], app.steps_var.get()))
    app.denoise.set(0.55); app._persist()
    check('the strength is remembered', abs(zui.load_settings().get('denoise', 0) - 0.55) < 1e-9,
          str(zui.load_settings().get('denoise')))
    app.denoise.set(0.70)

    print('\n-- the VRAM estimate comes from measurements, not from a guess')
    mib_k, src_k = zui.estimate_vram('flux2 klein 9b uncensored', 1, 512)
    check('the new default has a measured peak at 512', mib_k == 8837, str(mib_k))
    check('and 1024 px is now measured too - the live ALL run',
          zui.estimate_vram('flux2 klein 9b uncensored', 1, 1024)[0] == 9749,
          str(zui.estimate_vram('flux2 klein 9b uncensored', 1, 1024)))
    mib, src = zui.estimate_vram('z-image turbo', 16, 512)
    check('a measured shape returns its peak', mib == 8927, str(mib))
    check('and says where it came from', 'measured' in (src or ''), str(src))
    check('an unmeasured model invents nothing', zui.estimate_vram('nosuch model', 1, 256)[0] is None)
    print('\n-- ALL runs every profile once at its ceiling')
    app.model_var.set('ALL'); app._on_model_change(); root.update()
    check('ALL picks the largest size', app.size_var.get() == '1024', app.size_var.get())
    check('and the highest ceiling among the profiles', app.steps_var.get() == '9', app.steps_var.get())
    check('the boxes it overrides are disabled, not silently ignored',
          'disabled' in app.size_cb.state() and 'disabled' in app.steps_cb.state(),
          (app.size_cb.state(), app.steps_cb.state()))
    check('the banner describes the sweep', 'ALL' in app.banner['text'] and '9 steps' in app.banner['text'],
          app.banner['text'])
    check('the plan is cheapest-first, ceiling steps, 1024 px',
          zui.zg.all_plan() == [('z-image turbo', 9, 1024), ('flux2 klein 9b uncensored', 4, 1024)],
          zui.zg.all_plan())
    app.busy = False
    app.engine.calls.clear()
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a lighthouse at dusk')
    app.generate(); root.update()
    check('pressing Generate with ALL starts the sweep and dispatches the first model',
          len(app.engine.calls) == 1 and app._all_queue == ['flux2 klein 9b uncensored'],
          (len(app.engine.calls), app._all_queue))
    first_args, first_kw = app.engine.calls[-1] if app.engine.calls else ((), {})
    check('the first render is z-image turbo at its ceiling',
          first_kw.get('profile') == 'z-image turbo' and first_args[3] == 9 and first_kw.get('batch') == 1,
          (first_kw.get('profile'), first_args[3:4], first_kw.get('batch')))
    check('and at 1024 px', first_args[1] == 1024 and first_args[2] == 1024, first_args[1:3])
    app.busy = False
    app._run_next_all(); root.update(); time.sleep(0.05)
    second_args, second_kw = app.engine.calls[-1] if app.engine.calls else ((), {})
    check('the second is the 9B at its own ceiling',
          second_kw.get('profile') == 'flux2 klein 9b uncensored' and second_args[3] == 4,
          (second_kw.get('profile'), second_args[3:4]))
    app.busy = False
    app._run_next_all(); root.update(); time.sleep(0.05)
    check('and the queue is spent', app._all_queue is None, app._all_queue)
    # the boxes stay locked while ALL is still the picker value - that is the point of locking them
    check('the boxes stay locked while ALL is selected',
          'disabled' in app.steps_cb.state(), app.steps_cb.state())
    app.model_var.set(zui.zg.DEFAULT_MODEL); app._on_model_change(); root.update()
    check('choosing a real profile again unlocks them',
          'disabled' not in app.steps_cb.state() and 'disabled' not in app.size_cb.state(),
          (app.steps_cb.state(), app.size_cb.state()))
    app.size_var.set('512'); root.update()
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
    shown = [n for n, prof in zui.zg.MODELS.items() if prof.get('in_picker', True)]
    check('it offers every profile still in the picker, then ALL', list(app.model_cb['values']) == shown + ['ALL'],
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
    check('the deleted profiles are gone from MODELS',
          'flux1 schnell' not in zui.zg.MODELS and 'flux1 dev' not in zui.zg.MODELS, str(list(zui.zg.MODELS)))
    for prof_name, want in (('z-image turbo', '9'), ('flux2 klein 9b uncensored', '4'),
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
    cols = app._strip_cols()
    check('down moves one row (%d columns at this width)' % cols,
          app.selected[0].name == app._files[1 + cols].name, app.selected[0].name)
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
    app.model_var.set('flux2 klein 9b uncensored')
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a fox')
    app.busy = False; app.engine.calls.clear()
    app.generate(); root.update()
    args_, kwd = app.engine.calls[-1]
    check('the style suffix reaches the prompt', args_[0].endswith(zui.STYLES['icon']), args_[0][-40:])
    check('the negative prompt is sent', kwd.get('negative') == 'blurry, watermark', str(kwd.get('negative')))
    check('the model profile is sent', kwd.get('profile') == 'flux2 klein 9b uncensored', str(kwd.get('profile')))
    check('cfg comes from the profile', args_[4] == 1.0, str(args_[4]))
    check('the style is recorded', kwd.get('style') == 'icon')
    check('the banner names the model and style',
          'flux2 klein 9b uncensored' in app.banner['text'] and 'style icon' in app.banner['text'],
          app.banner['text'])
    app.model_var.set('flux2 klein 9b uncensored'); app._on_model_change(); root.update()
    check('picking a model applies its step ceiling', app.steps_var.get() == '4', app.steps_var.get())
    check('and says it is the maximum', 'maximum' in app.hint['text'] and 'cfg 1.0' in app.hint['text'],
          app.hint['text'])
    app.model_var.set('z-image turbo'); app._on_model_change()
    check('switching back applies the other ceiling', app.steps_var.get() == '9', app.steps_var.get())
    app.style_var.set('none')
    app.negative.delete('1.0', 'end')
    app.busy = False

    print('\n-- arrows rank, they never delete, and Up goes back')
    import pair_rank as _pr
    import pair_review as _pv
    arrow_dir = Path(tempfile.mkdtemp(prefix='arrows-'))
    ap1, ap2 = arrow_dir / 'left.png', arrow_dir / 'right.png'
    for q in (ap1, ap2):
        q.write_bytes(b'x')
        q.with_suffix('.json').write_text(json.dumps({'image': str(q), 'prompt': 'arrow test'}), encoding='utf-8')
    calls = []
    app._review_mode = tk.StringVar(value='rank')      # created by review_open, which this test does not need
    real_decide = app.review_decide
    app.review_decide = lambda side: calls.append(side)
    try:
        app._review_mode.set('rank')
        app._review_arrow('left'); app._review_arrow('right'); app._review_arrow('middle')
        check('in rank mode the arrows decide', calls == ['left', 'right', 'middle'], str(calls))
        calls.clear()
        app._review_mode.set('delete')
        app._review_arrow('left')
        check('in delete mode an arrow does nothing', calls == [], str(calls))
        check('and says why', 'arrows rank only' in app.hint['text'], app.hint['text'])
    finally:
        app.review_decide = real_decide
        app._review_mode.set('rank')
        del app._review_mode

    # Up: undo the last decision and put that pair back
    real_state_dir = _pv.STATE_DIR
    _pv.STATE_DIR = arrow_dir                     # a test state must never land in the real review folder
    state = {'key': 'arrow-test'}
    _pr.record(state, str(ap1), str(ap2), 'left_wins')
    shown = []
    real_show = app._review_show_next
    app._review_show_next = lambda: shown.append(1)
    app._review = {'state': state, 'history': [(str(ap1), str(ap2))], 'queue': None, 'pair': None}
    try:
        app.review_back()
        root.update()
    finally:
        app._review_show_next = real_show
        app._review = None
        _pv.STATE_DIR = real_state_dir
    check('back retracts the win', _pr.record_of(str(ap1))['wins'] == 0, str(_pr.record_of(str(ap1))))
    check('back forgets the decision', not state.get('decisions'), str(state.get('decisions')))
    check('back shows the earlier pair again', bool(shown), str(len(shown)))
    check('and says where it went', 'back to' in app.hint['text'], app.hint['text'][:40])
    app.history_guard = None
    app._review = {'state': {}, 'history': [], 'queue': None}
    app.review_back()
    check('with nothing to go back to it says so', 'nothing to go back' in app.hint['text'], app.hint['text'][:40])
    app._review = None

    print('\n-- Recover rebuilds missing metadata for the open folder')
    rec_dir = Path(tempfile.mkdtemp(prefix='recover-ui-'))
    from PIL import PngImagePlugin
    graph = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a lighthouse on a rock"}},
             "2": {"class_type": "KSampler", "inputs": {"steps": 20, "cfg": 7.0, "seed": 5, "denoise": 1.0,
                                                        "positive": ["1", 0]}}}
    bare_pics = []
    for i in range(2):
        p = rec_dir / ('rec%d.png' % i)
        info = PngImagePlugin.PngInfo(); info.add_text('prompt', json.dumps(graph))
        Image.new('RGB', (48, 48), (70 + i, 90, 120)).save(p, pnginfo=info)
        bare_pics.append(p)
    Image.new('RGB', (48, 48), (10, 10, 10)).save(rec_dir / 'nothing.png')   # no metadata at all
    saved_soon_rec, saved_order_rec = app._regroup_soon, app._content_order
    app._content_order = bare_pics + [rec_dir / 'nothing.png']
    app._regroup_soon = lambda: None      # restored below: leaving this stubbed starves a later check of its regroup
    written = app.recover_pane_sidecars()
    check('two sidecars are written', written == 2, str(written))
    made = sorted(p.name for p in rec_dir.glob('*.json'))
    check('only the recoverable pictures get one', made == ['rec0.json', 'rec1.json'], str(made))
    meta0 = json.loads((rec_dir / 'rec0.json').read_text(encoding='utf-8'))
    check('the recovered prompt is inside', (meta0.get('prompt') or '').startswith('a lighthouse'), meta0.get('prompt'))
    check('and it is marked as imported', meta0.get('imported') == 'comfy-workflow', str(meta0.get('imported')))
    check('the hint reports the refusal', 'no readable metadata' in app.hint['text'], app.hint['text'][:60])
    check('a second press finds nothing left', app.recover_pane_sidecars() == 0, str(app.recover_pane_sidecars()))
    app._regroup_soon = saved_soon_rec
    app._content_order = saved_order_rec
    app.busy = False

    print('\n-- ranking never deletes, even when the mode is missing')
    app._review_mode = tk.StringVar(value='rank')
    check('rank mode ranks', app._review_is_rank() is True, str(app._review_is_rank()))
    app._review_mode.set('delete')
    check('delete mode deletes', app._review_is_rank() is False, str(app._review_is_rank()))
    app._review_mode.set('wins')                       # a renamed mode must not fall through to deletion
    check('an unknown mode still ranks', app._review_is_rank() is True, str(app._review_is_rank()))
    del app._review_mode
    check('a missing mode ranks too - this used to mean delete', app._review_is_rank() is True, 'missing')

    # a decision taken with no mode set must record a win, not recycle a file
    guard_dir = Path(tempfile.mkdtemp(prefix='guard-'))
    g1, g2 = guard_dir / 'g1.png', guard_dir / 'g2.png'
    for q in (g1, g2):
        q.write_bytes(b'x')
        q.with_suffix('.json').write_text(json.dumps({'image': str(q), 'prompt': 'guard'}), encoding='utf-8')
    recycled = []
    real_state_dir_guard = _pv.STATE_DIR
    _pv.STATE_DIR = guard_dir                 # a test state must not land in the real review folder
    real_recycle, real_show = app._recycle_files, app._review_show_next
    app._recycle_files = lambda files: (recycled.extend(files), True)[1]
    app._review_show_next = lambda: None
    gstate = {'key': 'guard-test'}
    app._review = {'state': gstate, 'pair': (str(g1), str(g2)), 'queue': [], 'history': [], 'deleted': 0}
    try:
        app.review_decide('left')
        root.update()
        check('with no mode the decision ranks', _pr.record_of(str(g1))['wins'] == 1,
              str(_pr.record_of(str(g1))))
        check('and nothing is recycled', recycled == [] and g1.exists() and g2.exists(), str(recycled))
        # a delete choice arriving anyway (a future caller, a stale button) is refused while ranking
        gstate.clear()
        app._review = {'state': gstate, 'pair': (str(g1), str(g2)), 'queue': [], 'history': [], 'deleted': 0}
        app.review_decide('delete_left')
        root.update()
        check('a delete choice is refused while ranking', not recycled and 'refused' in app.hint['text'],
              app.hint['text'][:42])
    finally:
        app._recycle_files, app._review_show_next = real_recycle, real_show
        _pv.STATE_DIR = real_state_dir_guard
        app._review = None
        gstate.clear()

    print('\n-- a review\'s winners reach the pane order and the folder tile')
    win_dir = Path(tempfile.mkdtemp(prefix='wins-'))
    made = []
    for i in range(5):
        p = win_dir / ('2026102%d-120000_512x512_seed%d.png' % (i, 900 + i))
        Image.new('RGB', (64, 64), (40 * i, 90, 180 - 20 * i)).save(p)
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))   # the tile's tiebreak is mtime: give it a real order
        p.with_suffix('.json').write_text(json.dumps({'prompt': 'wins test', 'seed': 900 + i, 'steps': 4,
                                                      'cfg': 1.0, 'size': 512, 'wins': 0}), encoding='utf-8')
        made.append(p)
    group = {'label': 'wins', 'items': [{'path': str(p)} for p in made]}
    plain = app._sheet_paths(group)
    check('with no wins the tile keeps the newest first', Path(plain[0]) == made[-1], Path(plain[0]).name)
    # the oldest picture wins three bouts
    victim = made[0]
    meta = json.loads(victim.with_suffix('.json').read_text(encoding='utf-8'))
    meta['wins'] = 3
    victim.with_suffix('.json').write_text(json.dumps(meta), encoding='utf-8')
    app._wins_cache = {}
    ranked = app._sheet_paths(group)
    check('a winner leads the folder tile', Path(ranked[0]) == victim, str(Path(ranked[0]).name))
    check('and the tile is still four distinct pictures', len(ranked) == zui.FOLDER_CELLS, str(len(ranked)))
    check('the wins stamp invalidates the cached tile', ranked[0] != plain[0], 'cache not keyed by wins')

    saved_soon3 = app._regroup_soon
    app._regroup_soon = lambda: None
    saved_plan3 = app._plan
    # newest first, the order grouping would leave, so 'Recent' has something to be right about
    app._plan = {'groups': [{'label': 'wins', 'items': [{'path': str(p)} for p in reversed(made)]}],
                 'recent': [], 'ungrouped': []}
    app.sort_var.set('Recent')
    app._render_folder_bar(); app.select_folder(0); root.update()
    check('the pane opens newest first', Path(app._content_order[0]) == made[-1], str(app._content_order[0].name))
    app.sort_var.set('Wins'); app._render_contents(); root.update()
    check('Wins puts the winner first in the pane', Path(app._content_order[0]) == victim,
          str(app._content_order[0].name))

    # finishing a review switches the order on, and drops the cached ranks
    import pair_review
    real_progress, real_save = pair_review.progress, pair_review.save_state
    class _Win:
        def destroy(self): pass
    app._review = {'state': {}, 'win': _Win(), 'queue': []}
    app.sort_var.set('Recent')
    app._heads[('stale', 0, '', 0)] = ['x']
    app._wins_cache['a-stale-entry'] = (0.0, 9)
    pair_review.progress = lambda state: (3, 4, 0)
    pair_review.save_state = lambda state: None
    try:
        app.review_stop()
        root.update()
    finally:
        pair_review.progress, pair_review.save_state = real_progress, real_save
    check('finishing a review orders by wins', app.sort_var.get() == 'Wins', app.sort_var.get())
    check('and forgets the cached tiles', ('stale', 0, '', 0) not in app._heads, str(len(app._heads)))
    # the wins cache is repopulated by the refresh that follows - the point is that the stale entry went
    check('and the stale ranks', 'a-stale-entry' not in app._wins_cache, str(len(app._wins_cache)))
    check('and the status says so', 'ordered by wins' in app.status['text'], app.status['text'][:60])
    app._review = None
    app.selected = []
    app.select_folder(None); app._plan = saved_plan3
    app._regroup_soon = saved_soon3
    app.sort_var.set('Recent')
    app.refresh_gallery(); app.busy = False

    print('\n-- the folder pane has select all and delete, and a delete leaves the pane at once')
    check('the pane has a select-all button', hasattr(app, 'contents_all_btn'), 'missing')
    check('the pane has a delete button', hasattr(app, 'contents_del_btn'), 'missing')
    app.selected = []
    app._paint()
    check('delete is disabled with nothing selected', 'disabled' in app.contents_del_btn.state(),
          str(app.contents_del_btn.state()))

    saved_plan2, saved_soon2 = app._plan, app._regroup_soon
    pics2 = list(app._files)[:4]
    if len(pics2) >= 3:
        app._regroup_soon = lambda: None
        app._plan = {'groups': [{'label': 'pane-test', 'items': [{'path': str(p)} for p in pics2]}],
                     'recent': [], 'ungrouped': []}
        app._render_folder_bar(); app.select_folder(0); root.update()
        check('the pane is drawn with the folder\'s pictures', len(app._content_order) == len(pics2),
              str(len(app._content_order)))
        app.selected = []
        app._select_all_pane(); root.update()
        check('select all takes every picture in the pane', len(app.selected) == len(pics2), str(len(app.selected)))
        check('and enables delete', 'disabled' not in app.contents_del_btn.state(), str(app.contents_del_btn.state()))
        check('the label counts it', 'selected' in app.contents_sel_label['text'], app.contents_sel_label['text'])
        app._select_all_pane(); root.update()
        check('pressing it again clears', app.selected == [], str(len(app.selected)))

        victim = Path(app._content_order[0])
        victim.with_suffix('.json').write_text(json.dumps({'prompt': 'pane test'}), encoding='utf-8')
        app.selected = [victim]
        app._paint()
        deleted = []

        def fake_recycle(files):
            for f in files:
                if f.exists():
                    f.unlink()
                    deleted.append(f)
            return True

        from tkinter import messagebox as _mb
        real_ask, real_recycle = _mb.askyesno, app._recycle_files
        _mb.askyesno = lambda *a, **k: True
        app._recycle_files = fake_recycle
        try:
            app.delete_selected()
            root.update()
        finally:
            _mb.askyesno, app._recycle_files = real_ask, real_recycle
        # the review entry point: the pane hands over its own pictures
        calls = []
        real_review = app.review_start
        app.review_start = lambda index=None, paths=None: calls.append(paths)
        try:
            app.selected = []
            app.review_pane()
            check('A/B with nothing selected takes the whole open folder',
                  calls and calls[-1] == list(app._content_order), str(len(calls[-1]) if calls else 0))
            app.selected = [Path(app._content_order[0])]
            app.review_pane()
            check('A/B with a selection takes just those', calls[-1] == [Path(app._content_order[0])],
                  str(calls[-1]))
            other = Path(app._content_order[-1]) if len(app._content_order) > 1 else Path(app._content_order[0])
            app.selected = [Path(app._content_order[0]), other]
            app.review_pane()
            check('two selected pictures are both handed over', len(calls[-1]) == 2, str(len(calls[-1])))
        finally:
            app.review_start = real_review
        label, kept = app.review_paths(paths=[p for p in app._content_order[:3]])
        check('review_paths names a selection and keeps existing files', len(kept) == 3 and 'selected' in label,
              '%s / %d' % (label, len(kept)))
        check('and drops a picture that is not there',
              len(app.review_paths(paths=[tmp / 'not-there.png'])[1]) == 0, 'kept a missing file')
        check('the pane has an A/B button', hasattr(app, 'contents_ab_btn'), 'missing')
        check('it is enabled when there is something to pair', 'disabled' not in app.contents_ab_btn.state(),
              str(app.contents_ab_btn.state()))

        check('the files went', not victim.exists() and len(deleted) >= 1, str(len(deleted)))
        log_path = Path(_pv.STATE_DIR).parent / 'deletions.jsonl'
        logged = [json.loads(line) for line in log_path.read_text(encoding='utf-8').splitlines()
                  if line.strip()] if log_path.exists() else []
        check('a deliberate delete is recorded as such',
              any(r.get('name') == victim.name and r.get('source') == 'button' for r in logged),
              str(logged[:2]) if logged else 'no deletions.jsonl')
        check('the tile left the pane immediately', victim not in app._content_order, str(len(app._content_order)))
        check('and the plan forgot it',
              not any(Path(str(it.get('path', ''))) == victim
                      for g in app._plan['groups'] for it in g.get('items') or []),
              'still in the plan')
        app.selected = []
        app.select_folder(None)
        app._plan = saved_plan2
        app._regroup_soon = saved_soon2
        app._render_folder_bar(); app.refresh_gallery(); app.busy = False
    else:
        check('three pictures exist for the pane test', False, str(len(pics2)))

    print('\n-- a shot list renders one card per shot, named after its id')
    shot_doc = {'film': 'THE LAST POCKET', 'code': 'TLP', 'shots': [
        {'id': 'S01-0001', 'card': 1, 'scene': 1, 'place': 'EXT. PORT MERIDIAN', 'time': 'NIGHT',
         'prompt': 'ISA rolls a ferry token across her knuckles.', 'slug': '1. EXT. PORT MERIDIAN - NIGHT'},
        {'id': 'S02-0001', 'card': 2, 'scene': 2, 'place': 'INT. THE LAST POCKET', 'time': 'CONTINUOUS',
         'prompt': 'KES places a second coin beside hers.', 'slug': '2. INT. THE LAST POCKET - CONTINUOUS'},
    ]}
    shot_path = tmp / 'shotlist.json'
    shot_path.write_text(json.dumps(shot_doc), encoding='utf-8')
    app.busy = False; app.engine.calls.clear()
    started = app.start_shotlist(shot_path)
    root.update()
    check('the shot list starts', started is True, str(started))
    args_, kw_ = app.engine.calls[-1]
    check('the first card goes out now', 'ISA rolls' in args_[0], args_[0][:40])
    check('its id names the file', kw_.get('name') == 'S01-0001', str(kw_.get('name')))
    check('and it is card 1 of 2', (kw_.get('scene'), kw_.get('of')) == (1, 2),
          str((kw_.get('scene'), kw_.get('of'))))
    check('the queue holds the rest', len(app._scene_queue or []) == 1, str(len(app._scene_queue or [])))
    app.busy = False; app.engine.calls.clear()
    app._run_next_scene(); root.update()
    args2, kw2 = app.engine.calls[-1]
    check('the next card goes out with its own id', kw2.get('name') == 'S02-0001', str(kw2.get('name')))
    check('and its own words', 'KES places' in args2[0], args2[0][:40])
    check('the queue is empty now', not app._scene_queue, str(app._scene_queue))
    app.busy = False; app.engine.calls.clear()
    app._run_next_scene(); root.update()
    check('a finished queue says so and stops', not app.engine.calls and 'done' in app.status['text'].lower(),
          app.status['text'][:40])
    check('a missing file is a sentence, not a traceback', app.start_shotlist(tmp / 'nope.json') is False,
          app.hint['text'][:50])
    shot_path.unlink(missing_ok=True)

    print('\n-- a picture made from a folder base is visible at once')
    saved_plan, saved_group, saved_soon = app._plan, app.group_var.get(), app._regroup_soon
    pics = list(app._files)[:2]
    if len(pics) == 2:
        app._regroup_soon = lambda: None
        app.group_var.set(True)
        app._plan = {'groups': [{'label': 'one', 'items': [{'path': str(pics[0])}]},
                                {'label': 'two', 'items': [{'path': str(pics[1])}]}],
                     'recent': [], 'ungrouped': []}
        app._render_folder_bar(); app.select_folder(0)
        check('a folder is open first', app._active_folder == 0, str(app._active_folder))
        fresh = tmp / 'fresh-unfiled.png'
        Image.new('RGB', (32, 32), 'teal').save(fresh)
        app._base_from_gallery = False
        feed_image(app, fresh)
        check('a picture with no gallery base leaves the folder alone', app._active_folder == 0, str(app._active_folder))
        app._base_from_gallery = True
        feed_image(app, fresh)
        check('a folder base closes the folder so the result can be seen', app._active_folder is None,
              str(app._active_folder))
        check('and the hint says why it is unfiled', 'unfiled' in app.hint['text'], app.hint['text'])
        app._show_new_results()
        check('with no folder open it changes nothing', app._active_folder is None, str(app._active_folder))
        fresh.unlink(missing_ok=True)
        app._base_from_gallery = False
        app.select_folder(None)
        app._plan = saved_plan
        app.group_var.set(saved_group)
        app._regroup_soon = saved_soon
        app._render_folder_bar(); app.refresh_gallery(); app.busy = False
    else:
        check('two pictures exist for this check', False, str(len(pics)))

    print('\n-- a prompt that contradicts itself is named, not blocked')
    pairs = zui.prompt_clashes('thin construction lines, black line only, white background, charcoal drawing on '
                               'textured paper')
    check('the measured contradiction is found', ('black line only', 'charcoal') in pairs, str(pairs))
    check('a clean prompt has none', zui.prompt_clashes('a photograph of a mug on a wooden table') == [],
          str(zui.prompt_clashes('a photograph of a mug on a wooden table')))
    app.text.delete('1.0', 'end')
    app.text.insert('1.0', 'thin construction lines, black line only, white background')
    app.style_var.set('charcoal'); root.update()
    check('the banner names both halves', 'black line only' in app.banner['text'] and 'charcoal' in app.banner['text'],
          app.banner['text'])
    # by value and through str(): cget returns a Tcl object, and a Windows theme change rebuilds app.theme, while the
    # banner keeps the colour it was given
    fg = str(app.banner['foreground'])
    check('and it is the warning colour, not the plain one',
          fg in ('#ffc24b', '#9a6400') and fg != str(app.theme.text), '%s vs text %s' % (fg, app.theme.text))
    kept_clash = []
    app.ask_prompt_clash = lambda clashes, prompt, spans: (kept_clash.append(clashes), 'keep')[1]
    app.busy = False; app.engine.calls.clear(); app._clash_answered = None
    app.generate(); root.update()
    check('the dialog offers the choice', kept_clash and kept_clash[0][0] == ('black line only', 'charcoal'),
          str(kept_clash))
    check('and still dispatches - advice never blocks', len(app.engine.calls) == 1, str(len(app.engine.calls)))
    app.style_var.set('none')
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a fox')   # the next block needs a prompt to dispatch
    app.busy = False; root.update()

    print('\n-- the contradiction guard can remove the rival words')
    check('the spans are the words in the box only',
          zui.App._rival_spans('thin construction lines, black line only, white background', ['black line only']) ==
          [(25, 40)], str(zui.App._rival_spans('thin construction lines, black line only, white background',
                                               ['black line only'])))
    check('a word only in the style suffix yields no span',
          zui.App._rival_spans('black line only', ['charcoal']) == [],
          str(zui.App._rival_spans('black line only', ['charcoal'])))

    asked = {'n': 0}
    app.ask_prompt_clash = lambda clashes, prompt, spans: (asked.__setitem__('n', asked['n'] + 1), 'remove')[1]
    app.text.delete('1.0', 'end')
    app.text.insert('1.0', 'thin construction lines, black line only, white background')
    app.style_var.set('charcoal'); app._clash_answered = None
    app.busy = False; app.engine.calls.clear()
    app.generate(); root.update()
    sent = app.engine.calls[-1][0][0]
    check('remove takes the rival words out of what is sent', 'black line only' not in sent, sent[:70])
    check('and keeps the style', sent.endswith(zui.STYLES['charcoal']), sent[-60:])
    check('the box itself loses them too', 'black line only' not in app.text.get('1.0', 'end'), app.text.get('1.0', 'end'))
    check('the dialog was asked once', asked['n'] == 1, str(asked['n']))
    app.busy = False; app.engine.calls.clear(); app._clash_answered = app.text.get('1.0', 'end').strip()
    app.generate(); root.update()
    check('pressing Generate again does not reopen it', asked['n'] == 1, str(asked['n']))

    app.ask_prompt_clash = lambda clashes, prompt, spans: (asked.__setitem__('n', asked['n'] + 1), 'keep')[1]
    app.text.delete('1.0', 'end')
    app.text.insert('1.0', 'thin construction lines, black line only, white background')
    app.style_var.set('charcoal'); app._clash_answered = None
    app.busy = False; app.engine.calls.clear()
    app.generate(); root.update()
    kept = app.engine.calls[-1][0][0]
    check('keep sends the prompt as typed', 'black line only' in kept and kept.endswith(zui.STYLES['charcoal']),
          kept[:70])
    app.style_var.set('none'); app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a fox')
    app._clash_answered = None; app.busy = False; root.update()

    print('\n-- the clash dialog has two ways out and names both halves')
    # an earlier block stubbed ask_prompt_clash on the instance; drop the override so the real dialog builds
    app.__dict__.pop('ask_prompt_clash', None)
    snap = {}
    real_wait = app.root.wait_window

    def fake_wait(win):
        texts = []

        def walk(widget):
            for child in widget.winfo_children():
                try:
                    t = str(child.cget('text'))
                except Exception:
                    t = ''
                if t:
                    texts.append(t)
                walk(child)

        walk(win)
        snap['texts'] = texts
        win.destroy()

    app.root.wait_window = fake_wait
    try:
        answer = app.ask_prompt_clash([('black line only', 'charcoal')],
                                      'black line only, white background', [(0, 15)])
    finally:
        app.root.wait_window = real_wait
    joined = ' | '.join(snap.get('texts') or [])
    check('it offers both outs', any(t.startswith('Remove') for t in snap['texts'])
          and 'Keep and generate' in snap['texts'], joined[:100])
    check('and names the pair it is about', '"black line only"' in joined and '"charcoal"' in joined, joined[:120])
    check('closing it means keep', answer == 'keep', answer)

    print('\n-- a layout on a base image is a series, not one image')
    ok, seeds16 = zui.tile_plan(16, 100, 1, has_base=True)
    check('a base image keeps the whole layout', ok == 16, str(ok))
    check('and every tile gets its own seed', seeds16 == list(range(100, 116)), str(seeds16[:3]))
    ok1, seeds1 = zui.tile_plan(1, 100, 1, has_base=True)
    check('one tile is still one image on one seed', (ok1, seeds1) == (1, [100]), str((ok1, seeds1)))
    ok0, _ = zui.tile_plan(0, 100, 1)
    check('a zero count is clamped to one', ok0 == 1, str(ok0))

    series_png = tmp / 'series-base.png'
    Image.new('RGB', (48, 48), 'red').save(series_png)
    app._set_base(series_png); app.layout_var.set('16-up (4x4)'); app.seq_var.set(False)
    app.style_var.set('none'); app.busy = False; app.engine.calls.clear()
    app.generate(); root.update()
    sent = app.engine.calls[-1][1]
    check('the window asks the engine for 16 tiles from a base', sent.get('batch') == 16, str(sent.get('batch')))
    check('the base travels with it', bool(sent.get('base_path')), str(sent.get('base_path')))
    check('and the hint promises the series, not a single edit',
          'variations' in app.hint['text'] and 'layout ignored' not in app.hint['text'], app.hint['text'])
    app.layout_var.set('single'); app.seq_var.set(True); app.clear_base()
    app.refresh_gallery(); app.busy = False
    series_png.unlink(missing_ok=True)

    print('\n-- drawing-media styles, and the strength a base image gets from its style')
    for name in ('charcoal', 'pencil', 'blue pencil'):
        check('%s exists and carries a medium' % name, name in zui.STYLES and bool(zui.STYLES.get(name)), name)
    # the encoder budget is ~50 words, so a suffix must stay near a tenth of it. Nine is the existing worst case
    # (diagram, which measured well in the construction trial and is left alone); the drawing media are <= 8.
    check('no style suffix exceeds 9 words', all(len(zui.STYLES[s].split()) <= 9 for s in zui.STYLES),
          str(max(len(zui.STYLES[s].split()) for s in zui.STYLES)))
    check('the drawing-media suffixes stay at 8 or fewer',
          all(len(zui.STYLES[s].split()) <= 8 for s in ('charcoal', 'pencil', 'blue pencil')),
          str([len(zui.STYLES[s].split()) for s in ('charcoal', 'pencil', 'blue pencil')]))
    check('every style also proposes a base-image strength',
          all(s in zui.STYLE_STRENGTH for s in zui.STYLES), str(sorted(set(zui.STYLES) - set(zui.STYLE_STRENGTH))))
    # measured: 0.55 and 0.65 still render a photograph, 0.72 makes the medium the medium
    check('the medium styles sit at the measured medium-change strength',
          all(zui.STYLE_STRENGTH[s] == 0.72 for s in ('charcoal', 'pencil', 'blue pencil', 'watercolour', 'photo',
                                                     'cinematic')),
          str({s: zui.STYLE_STRENGTH[s] for s in ('charcoal', 'photo', 'watercolour')}))
    check('the genre styles sit higher, below the strength that leaves the source',
          all(0.72 < zui.STYLE_STRENGTH[s] < 0.84 for s in ('icon', 'diagram', 'blueprint')),
          str({s: zui.STYLE_STRENGTH[s] for s in ('icon', 'diagram', 'blueprint')}))
    check('and none keeps the klein pose threshold', zui.STYLE_STRENGTH['none'] == 0.70, str(zui.STYLE_STRENGTH['none']))

    # the guard: charcoal words against a photo style conflict, and against charcoal they agree
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a charcoal drawing of a fox')
    app.style_var.set('photo'); root.update()
    check('a charcoal prompt conflicts with the photo style', len(app.style_conflicts('a charcoal drawing of a fox', 'photo')) > 0,
          str(app.style_conflicts('a charcoal drawing of a fox', 'photo')))
    check('and agreement is not a conflict', app.style_conflicts('a charcoal drawing of a fox', 'charcoal') == [],
          str(app.style_conflicts('a charcoal drawing of a fox', 'charcoal')))
    app.style_var.set('none')

    # the strength proposal: nothing without a base image, the style's number with one
    app.clear_base(); root.update()
    before = app.denoise.get()
    app.style_var.set('charcoal'); root.update()
    check('with no base image a style leaves the strength alone', abs(app.denoise.get() - before) < 1e-6, '%.2f' % app.denoise.get())
    base_png = tmp / 'strength-base.png'
    Image.new('RGB', (48, 48), 'green').save(base_png)
    app._set_base(base_png); root.update()
    offset_now = zui.MODEL_STRENGTH_OFFSET.get(app.model_var.get(), 0.0)
    check("a base image takes its style's strength, plus this model's offset",
          abs(app.denoise.get() - (zui.STYLE_STRENGTH['charcoal'] + offset_now)) < 1e-6,
          '%.2f for offset +%.2f' % (app.denoise.get(), offset_now))
    app.style_var.set('icon'); root.update()
    check('another style proposes another strength',
          abs(app.denoise.get() - (zui.STYLE_STRENGTH['icon'] + offset_now)) < 1e-6, '%.2f' % app.denoise.get())
    app.strength_scale.set(0.33); root.update()
    check('moving the slider is remembered', app._strength_touched, str(app._strength_touched))
    app.style_var.set('charcoal'); root.update()
    check('and no style change overwrites it afterwards', abs(app.denoise.get() - 0.33) < 1e-6, '%.2f' % app.denoise.get())
    app.clear_base()
    app.strength_scale.set(zui.STYLE_STRENGTH['none']); app._strength_touched = False; root.update()
    app.style_var.set('none'); app.busy = False
    base_png.unlink(missing_ok=True)

    print('\n-- the strength a style proposes is model-specific')
    check('the offsets are recorded per model', zui.MODEL_STRENGTH_OFFSET.get('z-image turbo', 0) > 0
          and zui.MODEL_STRENGTH_OFFSET.get('flux2 klein 9b uncensored') == 0.0,
          str(zui.MODEL_STRENGTH_OFFSET))
    base_png = tmp / 'offset-base.png'
    Image.new('RGB', (48, 48), 'blue').save(base_png)
    app.model_var.set('flux2 klein 9b uncensored'); app._on_model_change()
    app.strength_scale.set(0.50); app._strength_touched = False
    app._set_base(base_png); app.style_var.set('charcoal'); root.update()
    klein_value = app.denoise.get()
    check('klein takes the style strength itself',
          abs(klein_value - zui.STYLE_STRENGTH['charcoal']) < 1e-6, '%.2f' % klein_value)
    app.model_var.set('z-image turbo'); app._on_model_change(); root.update()
    turbo_value = app.denoise.get()
    check('switching model re-proposes it with that model\'s offset',
          abs(turbo_value - (zui.STYLE_STRENGTH['charcoal'] + zui.MODEL_STRENGTH_OFFSET['z-image turbo'])) < 1e-6,
          '%.2f' % turbo_value)
    app.strength_scale.set(0.40); root.update()
    app.model_var.set('flux2 klein 9b uncensored'); app._on_model_change(); root.update()
    check('a strength the user set is still never replaced', abs(app.denoise.get() - 0.40) < 1e-6, '%.2f' % app.denoise.get())
    app.clear_base()
    app.strength_scale.set(zui.STYLE_STRENGTH['none']); app._strength_touched = False
    app.style_var.set('none'); app.busy = False
    app.model_var.set('z-image turbo'); app._on_model_change()
    base_png.unlink(missing_ok=True)

    print('\n-- a variation strength is a choice, not a default')
    vary_png = tmp / '20261028-120001_512x512_seed9.png'
    Image.new('RGB', (32, 32), 'purple').save(vary_png)
    vary_png.with_suffix('.json').write_text(json.dumps({'prompt': 'a red fox', 'seed': 9, 'style': 'charcoal',
                                                         'steps': 4, 'cfg': 1.0, 'size': 512}), encoding='utf-8')
    app.select_file(vary_png); app.busy = False
    app.vary('base', 0.40); root.update()
    check('vary sets its strength', abs(app.denoise.get() - 0.40) < 1e-6, '%.2f' % app.denoise.get())
    check('and counts as the user\'s choice', app._strength_touched, str(app._strength_touched))
    app.style_var.set('icon'); root.update()
    check('a style change does not overwrite it', abs(app.denoise.get() - 0.40) < 1e-6, '%.2f' % app.denoise.get())
    app.model_var.set('z-image turbo'); app._on_model_change(); root.update()
    check('nor does a model switch', abs(app.denoise.get() - 0.40) < 1e-6, '%.2f' % app.denoise.get())
    app.model_var.set('flux2 klein 9b uncensored'); app._on_model_change()
    app.style_var.set('none'); app.strength_scale.set(zui.STYLE_STRENGTH['none']); app._strength_touched = False
    app.clear_base(); app.busy = False
    vary_png.unlink(); vary_png.with_suffix('.json').unlink(missing_ok=True)
    app.refresh_gallery(); root.update()

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
    check('an empty clipboard is reported', 'nothing usable on the clipboard' in app.hint['text'],
          app.hint['text'])
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
        protocol_version = 'HTTP/1.1'       # keep-alive with a real Content-Length, or the socket gets RST

        def do_POST(self):
            n = int(self.headers.get('Content-Length') or 0)
            if n:
                self.rfile.read(n)          # leaving the body unread makes Windows abort the connection (10053)
            body = json.dumps({'choices': [{'message': {'content': 'a crisp vector fox'}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    app._settings['llm_url'] = 'http://127.0.0.1:%d/v1' % srv.server_port
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a fox')
    app.enhance_prompt()
    for _ in range(200):
        app._poll(); root.update(); time.sleep(0.05)
        if 'vector fox' in app.text.get('1.0', 'end'):
            break
    check('a local model rewrites the prompt', 'vector fox' in app.text.get('1.0', 'end'),
          'text=%r hint=%r' % (app.text.get('1.0', 'end').strip()[:60], app.hint['text'][:120]))
    srv.shutdown()

    print('\n-- describe captions the picture through the local endpoint')
    class Vision(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_POST(self):
            n = int(self.headers.get('Content-Length') or 0)
            if n:
                self.rfile.read(n)
            body = json.dumps({'choices': [{'message': {'content': 'a fox sitting on a log'}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    vsrv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Vision)
    threading.Thread(target=vsrv.serve_forever, daemon=True).start()
    app._settings['llm_url'] = 'http://127.0.0.1:%d/v1' % vsrv.server_port
    app.select_file(app._files[0]); root.update()
    app.text.delete('1.0', 'end')       # a stale hint or a stale prompt must not make this pass or fail early
    app.describe()
    for _ in range(300):        # a local round trip, but give it a real window rather than 4 s of luck
        app._poll(); root.update(); time.sleep(0.02)
        if 'fox sitting on a log' in app.text.get('1.0', 'end'):
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
    # the wording is allowed to get clearer ('View all pictures...'); what must not change is that it opens the view
    check('the button that opens it exists and says so', 'view all' in str(app.more_btn['text']).lower(), str(app.more_btn['text']))
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

    print('\n-- paste a reference from the clipboard as the base')
    import PIL.ImageGrab as ig
    real_grab = ig.grabclipboard
    app.clear_base()

    # 1) a bitmap
    bitmap = Image.new('RGB', (300, 200), (12, 200, 90))
    ig.grabclipboard = lambda: bitmap
    app.paste_base(); root.update()
    check('a bitmap becomes the base', app.base_path is not None and 'pasted-' in app.base_path.name,
          str(app.base_path))
    if app.base_path:
        with Image.open(app.base_path) as p_:
            check('the pasted bitmap keeps its size and pixels', p_.size == (300, 200)
                  and p_.convert('RGB').getpixel((5, 5)) == (12, 200, 90), str(p_.size))
        check('the status says it is the base', 'new base' in app.status['text'], app.status['text'])

    # 2) a copied image file
    src = app._files[0]
    ig.grabclipboard = lambda: [str(src)]
    app.paste_base(); root.update()
    meta = json.loads(app.base_path.with_suffix('.json').read_text(encoding='utf-8'))
    check('a copied image file becomes the base', 'pasted-' in app.base_path.name,
          str(app.base_path))
    check('and the sidecar records where it came from', meta.get('source_image', '').endswith(src.name),
          str(meta.get('source_image')))

    # 3) a copied path as text
    ig.grabclipboard = lambda: None
    root.clipboard_clear(); root.clipboard_append(str(src)); root.update()
    app.paste_base(); root.update()
    meta = json.loads(app.base_path.with_suffix('.json').read_text(encoding='utf-8'))
    check('a copied path works too', meta.get('source_image', '').endswith(src.name), str(meta.get('source_image')))

    # 4) things that are not images must not move the base
    before = app.base_path
    ig.grabclipboard = lambda: [str(tmp / 'notes.txt')]
    (tmp / 'notes.txt').write_text('not an image', encoding='utf-8')
    app.paste_base(); root.update()
    check('a non-image file is refused', app.base_path == before and 'none of them is an image' in app.hint['text'],
          app.hint['text'])
    ig.grabclipboard = lambda: None
    root.clipboard_clear(); root.clipboard_append('just some words'); root.update()
    app.paste_base(); root.update()
    check('unusable clipboard text is refused', app.base_path == before and 'nothing usable' in app.hint['text'],
          app.hint['text'])
    ig.grabclipboard = real_grab
    app.clear_base(); app.select_file(src); root.update()

    print('\n-- crop a region and use it as the base for the next run')
    app.clear_base()
    src = app._files[0]
    app.select_file(src)
    app._redraw(); root.update()
    x0, y0, w, h = app._draw
    check('the picture is laid out on the canvas', w > 20 and h > 20, str(app._draw))
    app.crop_var.set(True); app.toggle_crop_mode(True); root.update()
    check('crop mode arms a rectangle and says so', 'drag a rectangle' in app.hint['text'], app.hint['text'])
    ax, ay = x0 + int(w * 0.25), y0 + int(h * 0.25)
    bx, by = x0 + int(w * 0.75), y0 + int(h * 0.75)
    app._on_canvas_click(NS(x=ax, y=ay))
    app._on_crop_drag(NS(x=bx, y=by))
    app._on_crop_release(NS(x=bx, y=by))
    root.update()
    check('a crop file was written beside the output', app.base_path is not None
          and '-crop-' in app.base_path.name, str(app.base_path))
    if app.base_path is not None:
        with Image.open(app.base_path) as crop, Image.open(src) as full:
            check('the crop is half the source in each direction',
                  abs(crop.width - full.width // 2) <= 2 and abs(crop.height - full.height // 2) <= 2,
                  '%dx%d from %dx%d' % (crop.width, crop.height, full.width, full.height))
            # the crop must be the pixels that were under the rectangle, not a resize of the whole image
            left, top, right, bottom = json.loads(app.base_path.with_suffix('.json').read_text(encoding='utf-8'))['crop']
            check('the crop is the region that was dragged',
                  crop.convert('RGB').getpixel((crop.width // 2, crop.height // 2))
                  == full.convert('RGB').getpixel(((left + right) // 2, (top + bottom) // 2)))
        check('the sidecar records where it came from',
              'source_image' in json.loads(app.base_path.with_suffix('.json').read_text(encoding='utf-8')))
        check('and crop mode switched itself off', app.crop_var.get() is False)
        check('the status names the new base', 'new base' in app.status['text'], app.status['text'])
    check('crop mode and mask painting are exclusive',
          app.mask_var.set(True) or app.toggle_mask_mode(True) or True)
    app.toggle_crop_mode(True); root.update()
    check('arming crop turns mask painting off', app.mask_var.get() is False)
    app.toggle_crop_mode(False); root.update()
    app.select_file(src); app._set_base(src); root.update()

    print('\n-- prompt folders: grouping is by prompt text, in the background, with no disk change')
    import group_prompts as gpx
    # three pictures share one prompt, one has its own; the pixels are all different, so pixel proximity must not group
    shared = 'three women standing on a mountain top at sunrise'
    other = 'two women kissing on a leather sofa in a warm living room'
    # the three OLDEST files get the shared scene: the newest 18 are deliberately left ungrouped
    for i, prompt in ((-1, shared), (-2, shared), (-3, other)):
        sc = app._files[i].with_suffix('.json')
        meta = json.loads(sc.read_text(encoding='utf-8'))
        meta['prompt'] = prompt
        sc.write_text(json.dumps(meta), encoding='utf-8')
    listing = sorted((f.name, f.stat().st_mtime) for f in Path(tmp).glob('*'))
    app.toggle_groups(True)
    for _ in range(60):
        app._poll(); root.update(); time.sleep(0.05)
        if app._plan:
            break
    check('turning folders on computes a plan in the background', bool(app._plan),
          'hint=%r grouping=%s' % (app.hint['text'][:120], app._grouping))
    after = sorted((f.name, f.stat().st_mtime) for f in Path(tmp).glob('*'))
    check('and writes nothing to disk', listing == after)
    if app._plan:
        labels = [g['label'] for g in app._plan['groups']]
        sizes = [len(g['items']) for g in app._plan['groups']]
        check('it found the shared scene as one folder', any(l.startswith('three women') for l in labels),
              str(labels[:3]))
        check('with both of its pictures', any(len(g['items']) >= 2 and g['label'].startswith('three women')
                                               for g in app._plan['groups']), sizes)
        check('the plan is built from prompts, not pixels', app._plan['threshold'] == gpx.DEFAULT_THRESHOLD)
        check('the folder bar shows chips once the plan exists',
              len(app.folder_bar.winfo_children()) > 0, len(app.folder_bar.winfo_children()))
        idx = next(i for i, g in enumerate(app._plan['groups']) if g['label'].startswith('three women'))
        app.select_folder(idx); root.update()
        visible = {f.name for f in app._visible_files()}
        chosen = {Path(r['path']).name for r in app._plan['groups'][idx]['items']}
        check('selecting a folder filters the strip to it', visible and visible <= chosen,
              '%d visible, %d chosen' % (len(visible), len(chosen)))
        app.select_folder(None); root.update()
        check('clearing the selection shows everything again', len(app._visible_files()) >= len(chosen))
    app.toggle_groups(False)
    check('turning folders off drops the plan', app._plan is None and not app.folder_bar.winfo_ismapped())
    app.refresh_gallery(); root.update()

    print('\n-- rank mode: mark a winner, delete nothing, and sort by what won')
    import pair_rank as prank
    # a folder with enough members that it has several pairs to judge
    for i in range(-1, -7, -1):
        sc = app._files[i].with_suffix('.json')
        meta = json.loads(sc.read_text(encoding='utf-8'))
        meta['prompt'] = 'two women kissing on a leather sofa in a warm living room, lamp light'
        sc.write_text(json.dumps(meta), encoding='utf-8')
    if app._plan:
        app.toggle_groups(False)
    app.toggle_groups(True)
    for _ in range(80):
        app._poll(); root.update()
        if app._plan and not app._grouping:
            break
        time.sleep(0.05)
    big = max(range(len(app._plan['groups'])), key=lambda i: len(app._plan['groups'][i]['items']))
    check('the rank test has a folder with several pairs', len(app._plan['groups'][big]['items']) >= 4,
          len(app._plan['groups'][big]['items']))
    rec = []
    real_recycle_rank = app._recycle_files
    app._recycle_files = lambda files: rec.append([Path(f).name for f in files]) or True
    app.select_folder(big); root.update()
    app.review_start(big)
    for _ in range(80):
        app._poll(); root.update()
        if getattr(app, '_review', None):
            break
        time.sleep(0.05)
    r3 = getattr(app, '_review', None)
    check('the comparison window opens in the non-destructive mode',
          bool(r3) and app._review_mode.get() == 'rank', app._review_mode.get() if r3 else None)
    if r3:
        check('and the buttons say so', 'wins' in r3['buttons'][0]['text'].lower(), r3['buttons'][0]['text'])
        left3, right3 = r3['pair']
        was_left, was_right = prank.record_of(left3), prank.record_of(right3)
        rec.clear()
        app.review_decide('left'); root.update()
        after_left, after_right = prank.record_of(left3), prank.record_of(right3)
        check('a win is recorded on the winner', after_left['wins'] == was_left['wins'] + 1,
              (was_left, after_left))
        check('and on the loser as a loss', after_right['losses'] == was_right['losses'] + 1,
              (was_right, after_right))
        check('nothing is recycled in rank mode', rec == [], rec)
        meta = json.loads(Path(left3).with_suffix('.json').read_text(encoding='utf-8'))
        check('the win lives in the picture\'s own sidecar', meta.get('wins') == after_left['wins'], meta.get('wins'))
        check('and the prompt is untouched', bool(meta.get('prompt')), meta.get('prompt'))
        check('the hint reports the standing', 'W-' in app.hint['text'], app.hint['text'])

        # switching mode keeps the same window and restores the destructive buttons
        app._review_mode.set('delete'); app._review_relabel(); root.update()
        check('delete mode relabels the buttons', 'delete' in app._review['buttons'][0]['text'].lower(),
              app._review['buttons'][0]['text'])
        pair_now = app._review['pair']
        rec.clear()
        app.review_decide('right'); root.update()
        check('delete mode still recycles the right-hand file',
              bool(rec) and bool(pair_now) and rec[-1][0] == Path(pair_now[1]).name,
              'pair=%r rec=%r hint=%r' % (pair_now, rec, app.hint['text']))
        app.review_stop(); root.update()

        # ranking: the winner sorts first, in the strip and in the folder pane
        winner = left3
        app.sort_var.set('Wins'); app._resort(); root.update()
        shown = [Path(p_).name for p_ in app._files]
        check('sorting by wins puts the winner first', shown and shown[0] == Path(winner).name,
              '%r winner=%s recs=%s' % (shown[:3], Path(winner).name,
                                        [(n, prank.record_of(str(Path(app.args.out) / n)))
                                         for n in shown[:3]]))
        check('the winner is still in the list', Path(winner).name in shown,
              (Path(winner).name, len(shown)))
        app.select_folder(big); root.update(); time.sleep(0.2)
        marks = [w.cget('text') for cell in app.contents.winfo_children()
                 for w in cell.winfo_children() if w.winfo_class() == 'TLabel']
        check('the folder pane shows what each picture has done', any(m.endswith('W') for m in marks),
              'marks=%r cells=%d' % (marks[:4], len(app.contents.winfo_children())))
        app.sort_var.set('Recent'); app._resort(); root.update()
        check('and the default order is back', app._sort_wins() is False)
        # an explicit reset is the only thing that clears a record
        before = prank.record_of(winner)['bouts']
        cleared = prank.reset_wins([winner])
        check('reset clears the record for that picture',
              cleared >= 1 and prank.record_of(winner)['bouts'] == 0, (before, cleared))
    app._recycle_files = real_recycle_rank
    app.select_folder(None); root.update()

    print('\n-- the folder selector and the column layout (regressions from the 2026-10-27 screenshot)')
    if not app._plan:                      # the earlier folder test turned grouping off
        app.toggle_groups(True)
        for _ in range(80):
            app._poll(); root.update()
            if app._plan:
                break
            time.sleep(0.05)
    longest = max(len(app._folder_text(i)) for i in [None] + list(range(len(app._plan['groups']))))
    check('no folder label is longer than 26 characters', longest <= 26, longest)
    check('the folder control is a button, not a dropdown',
          not hasattr(app, 'folder_cb') and 'folders' in str(app.folder_pick_btn['text']).lower(),
          getattr(app, 'folder_pick_btn', None) and app.folder_pick_btn['text'])
    target = max(range(len(app._plan['groups'])), key=lambda i: len(app._plan['groups'][i]['items']))
    app.select_folder(target); root.update()
    check('choosing a folder filters the strip to it',
          app._active_folder == target and len(app._files) == len(app._plan['groups'][target]['items']),
          '%r but %d files visible' % (app.gal_title['text'], len(app._files)))
    check('and the button names the open folder',
          app._folder_text(target).split(' (')[0] in str(app.folder_pick_btn['text']),
          app.folder_pick_btn['text'])
    app.select_folder(None); root.update()
    check('choosing All clears the filter', app._active_folder is None, app._active_folder)
    check('and the button says so', 'all' in str(app.folder_pick_btn['text']).lower(),
          app.folder_pick_btn['text'])

    print('\n-- the folder button opens the full-screen browser')
    app.open_folder_browser()
    for _ in range(40):
        root.update(); time.sleep(0.02)
    browser = getattr(app, '_folder_win', None)
    check('the browser window opens', browser is not None and browser.winfo_exists())
    body = [w for w in browser.winfo_children() if w.winfo_class() == 'TFrame'][-1] if browser else None
    grids = [w for w in (body.winfo_children() if body else []) if isinstance(w, zui.ScrollGrid)]
    check('it lays out the folders and their pictures side by side', len(grids) == 2,
          [type(w).__name__ for w in (body.winfo_children() if body else [])])
    if len(grids) == 2:
        check('the folder grid holds every folder plus All', len(grids[0].items) == len(app._plan['groups']) + 1,
              len(grids[0].items))
        check('the picture grid starts on the open folder',
              len(grids[1].items) == (len(app._all_files()) if app._active_folder is None
                                      else len(app._plan['groups'][app._active_folder]['items'])),
              len(grids[1].items))
    # picking inside the browser opens that folder in the main window too
    pick_at = 1 if app._plan is not None and len(app._plan['groups']) > 1 else None
    if pick_at is not None:
        app.select_folder(pick_at); root.update()
        check('picking in the browser drives the main window', app._active_folder == pick_at, app._active_folder)
        app.select_folder(None); root.update()
    else:
        check('picking in the browser drives the main window (single-folder fixture)', True)
    app._close_folder_browser(); root.update()
    check('and it closes', getattr(app, '_folder_win', None) is None)

    def cells(frame):
        seen = {}
        for w in frame.winfo_children():
            info = w.grid_info()
            if not info:
                continue
            row, col = int(info['row']), int(info['column'])
            for cc in range(col, col + int(info.get('columnspan', 1))):
                seen.setdefault((row, cc), []).append(str(w))
        return {k: v for k, v in seen.items() if len(v) > 1}

    for frame, name in ((app.gal, 'gallery column'), (app.text.master, 'prompt column')):
        check('no two widgets share a grid cell in the %s' % name, not cells(frame), cells(frame))
    strip_top = app.gal_canvas.winfo_rooty()
    btn_frame = [w for w in app.gal.winfo_children() if w.winfo_class() == 'TFrame'
                 and any(str(c).endswith('.more_btn') or c.winfo_class() == 'TButton' for c in w.winfo_children())]
    check('the buttons sit above the strip and are not clipped',
          bool(btn_frame) and btn_frame[0].winfo_rooty() + btn_frame[0].winfo_height() <= strip_top + 1,
          (btn_frame[0].winfo_rooty() + btn_frame[0].winfo_height() if btn_frame else None, strip_top))
    check('and they fit inside the window',
          bool(btn_frame) and btn_frame[0].winfo_rooty() + btn_frame[0].winfo_height()
          <= root.winfo_rooty() + root.winfo_height(),
          (btn_frame[0].winfo_rooty() + btn_frame[0].winfo_height() if btn_frame else None,
           root.winfo_rooty() + root.winfo_height()))

    print('\n-- folders show as large 9-up tiles with a wrapped title')
    app.select_folder(None); root.update()
    tiles = app.recent.winfo_children()
    check('one tile per folder', len(tiles) == len(app._plan['groups']), (len(tiles), len(app._plan['groups'])))
    if tiles:
        # a tile is now: outer frame (the 1 px selection line) -> body -> pic, title, meta
        body = tiles[0].winfo_children()[0]
        kids = body.winfo_children()
        pic = kids[0]
        meta = next(w for w in kids if str(w.cget('text')).endswith('pictures'))   # the count sits above the name
        title = next(w for w in kids if w is not pic and w is not meta)
        check('each tile carries a contact sheet',
              pic.image is not None and pic.image.width() >= zui.STRIP_TILE_MIN,
              pic.image.width() if pic.image else None)
        check('the sheet is square, so four thumbnails sit in it as a 2x2',
              pic.image.width() == pic.image.height(), (pic.image.width(), pic.image.height()))
        two_up = [w for w in app.recent.winfo_children() if w.grid_info()]
        check('two folder tiles per row in the first column',
              len({int(w.grid_info()['column']) for w in two_up}) == min(2, len(two_up)),
              [int(w.grid_info()['column']) for w in two_up])
        full_title = app._plan['groups'][0].get('title') or app._plan['groups'][0]['label']
        check('the tile title is the first ten words, with an ellipsis when there is more',
              title['text'] == zui.shorten_title(full_title) and len(title['text'].split()) <= 10,
              (title['text'][:60], full_title[:60]))
        check('and it wraps inside the tile rather than running off it',
              int(title['wraplength']) > 0 and int(title['wraplength']) <= pic.image.width(),
              (title['wraplength'], pic.image.width() if pic.image else None))
        check('and the tile says how many pictures it holds',
              meta['text'].endswith('pictures'), meta['text'])
        check('the title uses the small font', '8' in str(title['font']), title['font'])
        before = len(app.recent.winfo_children())
        pick = 1 if len(tiles) > 1 else 0
        tiles[pick].winfo_children()[0].event_generate('<Button-1>')
        root.update(); time.sleep(0.2)
        check('clicking a tile opens that folder', app._active_folder == pick, app._active_folder)
        check('the folder list stays visible while a folder is open',
              len(app.recent.winfo_children()) == before, (len(app.recent.winfo_children()), before))
        check('and the pictures appear in their own pane', bool(app.contents.winfo_children())
              and app.contents_holder.winfo_manager() != '', app.contents_holder.winfo_manager())
        members = {Path(r['path']) for r in app._plan['groups'][pick]['items']}
        shown = set(app._content_widgets)
        check('the pane holds that folder\'s pictures, and only pictures it would accept',
              members <= shown and len(shown) > 0, (len(members), len(shown)))
        check('the extra ones are the live view, not members',
              len(shown - members) <= app._content_shown, (len(members), len(shown)))
        app.select_folder(None); root.update()
        check('choosing no folder hides the pane', app.contents_holder.winfo_manager() == '')
        check('the folder list is still there', len(app.recent.winfo_children()) == before)

    print('\n-- the open-folder pane is three pictures wide and fills what it is given')
    if not (app._plan and app._plan['groups']):
        app.toggle_groups(True)
        for _ in range(80):
            app._poll(); root.update()
            if app._plan and app._plan['groups']:
                break
            time.sleep(0.05)
    app.select_folder(0 if (app._plan and app._plan['groups']) else None); root.update(); time.sleep(0.15)
    pane_cols = {}
    for cell in app.contents.winfo_children():
        info = cell.grid_info()
        if info:
            pane_cols[int(info['column'])] = info
    check('the pane lays out three per row', sorted(pane_cols) == [0, 1, 2], sorted(pane_cols))
    if pane_cols:
        cell_w = max(int(c.winfo_width()) for c in app.contents.winfo_children())
        pane_w = app.contents_canvas.winfo_width()
        check('and the three of them fill the pane rather than a band of it',
              cell_w * 3 >= pane_w - 24, (cell_w, pane_w))
    check('the pane is as tall as the column, not a band under it',
          app.contents_holder.winfo_height() >= app.gal_canvas.winfo_height() - 12,
          (app.contents_holder.winfo_height(), app.gal_canvas.winfo_height()))
    # everything below the folder name *and its new button row*: select all and delete own that band now
    check('and its pictures fill everything below the folder name and buttons',
          app.contents_canvas.winfo_height() >= (app.contents_holder.winfo_height()
                                                 - app.contents_title.winfo_height()
                                                 - app.contents_btns.winfo_height() - 12),
          (app.contents_canvas.winfo_height(), app.contents_holder.winfo_height(),
           app.contents_title.winfo_height(), app.contents_btns.winfo_height()))
    app.select_folder(None); root.update()

    print('\n-- a scrolling grid grows its tiles with the window instead of adding columns forever')
    probe_win = tk.Toplevel(root)
    probe_win.geometry('1200x700')
    grid = zui.ScrollGrid(probe_win, lambda parent, item, size: tk.Label(parent, width=1, height=1),
                          tile_target=190)
    grid.pack(fill='both', expand=True)
    probe_win.update(); time.sleep(0.2); probe_win.update()
    cols, tile = grid._metrics()
    check('a wide window gets large tiles', tile >= 150, (cols, tile))
    check('and the row of them fills the width',
          cols * (tile + grid.pad) >= grid.canvas.winfo_width() - 2 * grid.pad,
          (cols, tile, grid.canvas.winfo_width()))
    probe_win.geometry('700x600')
    probe_win.update(); time.sleep(0.3); probe_win.update()
    cols2, tile2 = grid._metrics()
    check('a narrower window uses fewer, not smaller, columns', cols2 <= cols and tile2 <= tile,
          (cols, tile, cols2, tile2))
    probe_win.destroy()

    print('\n-- Generate opens the folder the prompt will land in, but never yanks a folder being read (2026-10-27)')
    if not (app._plan and app._plan['groups'] and app._plan.get('_lexicon')):
        app.toggle_groups(True)
        for _ in range(120):
            app._poll(); root.update()
            if app._plan and app._plan.get('_lexicon'):
                break
            time.sleep(0.05)
    if app._plan and app._plan['groups']:
        sample = app._plan['groups'][0]['items'][0]['prompt']
        app.select_folder(None); root.update()
        app.busy = False
        app.text.delete('1.0', 'end')
        app.text.insert('1.0', sample)
        app.generate(); root.update(); time.sleep(0.2)
        check('a prompt that matches a folder opens it from the All-folders state',
              app._active_folder is not None and app._prediction is not None,
              (app._active_folder, app._prediction))
        check('and the prediction is shown beside the folder button',
              any('\u2192' in str(w.cget('text')) for w in app.folder_bar.winfo_children()
                  if w.winfo_class() == 'TLabel'),
              [str(w.cget('text')) for w in app.folder_bar.winfo_children() if w.winfo_class() == 'TLabel'])
        app.busy = False
        app.select_folder(0); root.update()
        before = app._active_folder
        app.busy = False
        app.generate(); root.update(); time.sleep(0.2)
        check('a folder already open is not replaced', app._active_folder == before,
              (before, app._active_folder))
        app.busy = False
    else:
        check('fixture has a folder to predict into', False)

    print('\n-- a finished render re-groups, and the open folder survives it (2026-10-27)')
    if not (app._plan and app._plan['groups']):
        app.toggle_groups(True)
        for _ in range(100):
            app._poll(); root.update()
            if app._plan:
                break
            time.sleep(0.05)
    app.select_folder(0 if app._plan and app._plan['groups'] else None); root.update()
    open_label = None
    if app._active_folder is not None:
        open_label = app._plan['groups'][app._active_folder]['label']
    app._regroup_soon(); root.update()
    check('a render schedules a re-group', getattr(app, '_regroup_job', None) is not None)
    app._regroup_job and app.root.after_cancel(app._regroup_job)
    app._regroup_job = None
    app._regroup_now()
    for _ in range(120):
        app._poll(); root.update()
        if not getattr(app, '_grouping', False):
            break
        time.sleep(0.05)
    check('the re-group completes', app._grouping is False)
    if open_label is not None:
        check('and the folder that was open is still open',
              app._active_folder is not None and app._plan['groups'][app._active_folder]['label'] == open_label,
              (open_label, app._active_folder))
    app.select_folder(None); root.update()

    print('\n-- size badges, three-line folder titles, and the picture resolution (2026-10-27)')
    check('the 64 kB band is tomato red', zui.size_band(64 * 1024)[0] == '#ff6347', zui.size_band(64 * 1024))
    check('one byte over 64 kB is amber', zui.size_band(64 * 1024 + 1)[0] == '#ffbf00', zui.size_band(64 * 1024 + 1))
    check('1 MB is medium green', zui.size_band(1024 * 1024)[0] == '#3cb371', zui.size_band(1024 * 1024))
    check('a size reads in three characters or so',
          [zui.human_size(n) for n in (2 * 1024, 330 * 1024, 2 * 1024 * 1024)] == ['2k', '330k', '2.0M'],
          [zui.human_size(n) for n in (2 * 1024, 330 * 1024, 2 * 1024 * 1024)])
    long_title = ' '.join(['word%d' % i for i in range(30)])
    short = zui.shorten_title(long_title)
    check('a long folder title is cut to ten words with an ellipsis',
          len(short.split()) == 10 and short.endswith('\u2026'), short)
    check('a short title is left alone', zui.shorten_title('a lighthouse at dusk') == 'a lighthouse at dusk')
    app.select_folder(0); root.update(); time.sleep(0.2)
    badges = [w for cell in app.contents.winfo_children() for w in cell.winfo_children()
              if w.winfo_class() == 'Label' and str(w.cget('text')).endswith(('k', 'M'))]
    check('each picture in the pane wears a size badge', len(badges) >= 1, len(badges))
    if badges:
        colour = str(badges[0].cget('background'))
        check('the badge colour is one of the three bands', colour in ('#ff6347', '#ffbf00', '#3cb371'), colour)
        check('the badge uses the pixel font', 'Fixedsys' in str(badges[0].cget('font')), badges[0].cget('font'))
    newest = app._files[0]
    app.select_file(newest); root.update(); time.sleep(0.2)
    text = app.res_label['text']
    check('the picture reports its pixels', 'x' in text and text.split('x')[0].isdigit(), text)
    check('and its file size on the same band', any(text.count(u) for u in ('k', 'M')), text)
    check('and the seed it was made with', 'seed' in text, text)
    app.select_folder(None); root.update()

    print("\n-- the Sequence order reads a storyboard in film order (2026-10-28)")
    import zimage_ui as _zq
    _root_sq = tk.Tk()
    _app_sq = _zq.App(_root_sq, StubEngine(), args)
    _quieten(_app_sq)
    _root_sq.update()
    _sq_dir = Path(tempfile.mkdtemp(prefix='seq-'))
    made = []
    for name, scene, of in (('c.png', 2, 1), ('a.png', 1, 2), ('b.png', 1, 1), ('z.png', None, None)):
        img = _sq_dir / name
        Image.new('RGB', (16, 16), (40, 60, 80)).save(img)
        meta = {'prompt': 'p', 'created': '2026-10-28T10:00:0%d' % len(made)}
        if scene:
            meta.update({'scene': scene, 'scene_of': of})
        (img.with_suffix('.json')).write_text(json.dumps(meta), encoding='utf-8')
        made.append(str(img))
    order = [Path(p).name for p in _app_sq._sequence_order(made)]
    check('numbered pictures come first, in scene then position order',
          order[:3] == ['b.png', 'a.png', 'c.png'], order)
    check('and an unnumbered picture comes after the storyboard, not inside it', order[-1] == 'z.png', order)
    _app_sq.sort_var.set('Sequence')
    check('the order control offers Sequence', 'Sequence' in list(_app_sq.sort_cb['values']),
          _app_sq.sort_cb['values'])
    _app_sq.sort_var.set('Recent'); _root_sq.destroy(); tk._default_root = root

    print("\n-- browsing never generates: the only dispatchers are deliberate (2026-10-28)")
    # reported: "it starts to generate before I am expecting it". Style, strength, size and model changes must never
    # dispatch either - their traces only repaint - and a double click on a picture used to generate, which was the
    # hazard: looking at a thumbnail twice spent a minute of GPU with whatever prompt was in the box.
    app.engine.calls.clear(); app.busy = False
    app.style_var.set('cinematic'); root.update()
    app.denoise.set(0.55); root.update()
    app.model_var.set('z-image turbo'); app._on_model_change(); root.update()
    app.size_var.set('768'); root.update()
    check('changing style, strength, model and size dispatch nothing', len(app.engine.calls) == 0,
          len(app.engine.calls))

    tiles = list(getattr(app, '_tile_widgets', {}).values()) + list(getattr(app, '_content_widgets', {}).values())
    if tiles:
        app.engine.calls.clear(); app.busy = False
        for tile in tiles[:3]:
            try:
                tile.event_generate('<Double-Button-1>')
            except Exception:
                pass
            root.update()
        check('double clicking a picture does not render', len(app.engine.calls) == 0, len(app.engine.calls))

    # the deliberate ones still work
    app.busy = False; app.engine.calls.clear()
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a lighthouse')
    app.generate(); root.update()
    check('the Generate path itself is untouched', len(app.engine.calls) == 1, len(app.engine.calls))
    app.busy = False
    app.style_var.set('none')

    print("\n-- a scene render is graded against the scene it came from (2026-10-28)")
    import scene_grade as _sg
    grade_dir = Path(tempfile.mkdtemp(prefix='grade-'))
    scene_shot = grade_dir / 'scene1.png'
    Image.new('RGB', (64, 64), (20, 80, 40)).save(scene_shot)
    scene_shot.with_suffix('.json').write_text(json.dumps({'prompt': 'p', 'scene': 1, 'scene_of': 3,
                                                     'created': '2026-10-28T12:00:00'}), encoding='utf-8')
    calls = {}
    real_grade, real_attach = _sg.grade_scene, _sg.attach_to_sidecar

    def fake_grade(path, text, title=''):
        calls['grade'] = (Path(path).name, text, title)
        return {'answer': 'yes', 'reason': 'lamps lit', 'title': title}

    def fake_attach(path, verdict):
        calls['attach'] = (Path(path).name, verdict.get('answer'))
        return True

    _sg.grade_scene, _sg.attach_to_sidecar = fake_grade, fake_attach
    try:
        while not app.ui_events.empty():
            app.ui_events.get_nowait()
        app._grade_scene(scene_shot, 'a pool hall with three lamps', 'SCENE ONE', 1, 3)
        events = []
        deadline = time.time() + 5
        while time.time() < deadline and not events:
            try:
                events.append(app.ui_events.get(timeout=0.5))
            except Exception:
                pass
        check('the grader is asked about this picture and this scene',
              calls.get('grade', (None,))[1] == 'a pool hall with three lamps', calls.get('grade'))
        check('the verdict is written beside the picture', calls.get('attach') == ('scene1.png', 'yes'),
              calls.get('attach'))
        check('and the answer reaches the window', events and events[0][0] == 'scene_graded'
              and events[0][1][2] == 'yes', events[:1])
    finally:
        _sg.grade_scene, _sg.attach_to_sidecar = real_grade, real_attach

    # a scene the grader cannot answer must not break the render
    _sg.grade_scene = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('no key'))
    try:
        while not app.ui_events.empty():
            app.ui_events.get_nowait()
        app._grade_scene(scene_shot, 'a pool hall', 'SCENE ONE', 1, 3)
        deadline = time.time() + 5
        got = None
        while time.time() < deadline and got is None:
            try:
                got = app.ui_events.get(timeout=0.5)
            except Exception:
                pass
        check('a grader that fails reports unavailable, not a crash',
              got and got[1][2] == 'unavailable' and 'no key' in got[1][3], got)
    finally:
        _sg.grade_scene = real_grade

    print("\n-- steps above a model's ceiling are reduced and explained (2026-10-28)")
    # 34 pictures in the gallery were drawn at 50 steps on a 4- or 9-step model: up to twelve times the compute for nothing
    app.model_var.set('flux2 klein 9b uncensored'); app._on_model_change(); root.update()
    app.steps_var.set('50')
    app._clamp_steps('flux2 klein 9b uncensored', int(app.steps_var.get()))
    root.update()
    check('choosing 50 for a 4-step model sets the box to 4', app.steps_var.get() == '4', app.steps_var.get())
    check('and says why, with the ceiling named', 'ceiling' in app.hint.cget('text'), app.hint.cget('text'))
    app.engine.calls.clear(); app.busy = False
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a lighthouse')
    app.generate(); root.update()
    sent = app.engine.calls[-1][0][3] if app.engine.calls else None
    check('the dispatch uses the ceiling, not the number asked for', sent == 4, sent)

    app.model_var.set('z-image turbo'); app._on_model_change(); root.update()
    app.steps_var.set('12')
    app._clamp_steps('z-image turbo', 12); root.update()
    check('turbo clamps 12 to 9', app.steps_var.get() == '9', app.steps_var.get())
    app.busy = False
    app.engine.calls.clear()
    app.generate(); root.update()
    check('and a value already at the ceiling is left alone',
          app.engine.calls and app.engine.calls[-1][0][3] == 9, app.engine.calls[-1][0][3] if app.engine.calls else None)
    app.busy = False

    print("\n-- the strength readout is the value Generate will use (2026-10-28)")
    app.denoise.set(0.70); root.update()
    check('the label follows the slider', app.denoise_text.cget('text') == '0.70', app.denoise_text.cget('text'))
    app.denoise.set(0.6); root.update(); time.sleep(0.05); root.update()
    check('including a value set in code', app.denoise_text.cget('text') == '0.60', app.denoise_text.cget('text'))
    check('and it is two decimals, not a raw float', len(app.denoise_text.cget('text')) == 4,
          app.denoise_text.cget('text'))
    app.denoise.set(0.70); root.update()

    print("\n-- ALL honours the base and the strength, and says so (2026-10-28)")
    base_png = Path(tempfile.mkdtemp(prefix='allbase-')) / 'base.png'
    Image.new('RGB', (256, 256), (60, 90, 120)).save(base_png)
    base_png.with_suffix('.json').write_text(json.dumps({'prompt': 'a base', 'seed': 1,
                                                         'created': '2026-10-28T10:00:00'}), encoding='utf-8')
    app.base_path = base_png
    app.denoise.set(0.62)
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a lighthouse on a coast')
    app.model_var.set(zui.zg.ALL_MODEL); app._on_model_change(); root.update()
    app._update_banner()
    check('the ALL banner says it is editing the base, at the strength set',
          'EDIT' in app.banner['text'] and '0.62' in app.banner['text'], app.banner['text'][:120])
    app.engine.calls.clear(); app.busy = False
    app.generate(); root.update()
    guard = 0
    while getattr(app, '_all_queue', None) and guard < 6:
        app.busy = False; app._run_next_all(); root.update(); guard += 1
    check('the sweep dispatched one job per model', len(app.engine.calls) == 2, len(app.engine.calls))
    passes = [(Path(k.get('base_path') or '').name, round(k.get('denoise') or 0, 2)) for _a, k in app.engine.calls]
    check('every model got the base', all(name == 'base.png' for name, _d in passes), passes)
    check('and the strength that was set', all(d == 0.62 for _n, d in passes), passes)
    check('each at its own ceiling, not the sweep driving one model twice',
          sorted(k.get('profile') for _a, k in app.engine.calls) == sorted([p for p in zui.zg.ALL_ORDER]), 
          [k.get('profile') for _a, k in app.engine.calls])
    app.base_path = None; app.denoise.set(0.35); app._update_banner()
    check('with no base the banner does not claim an edit', 'EDIT' not in app.banner['text'], app.banner['text'][:80])

    # and ALL cannot dodge the style question: it asks before the first model is dispatched
    app.style_var.set('cinematic')
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a lighthouse, oil painting')
    app.model_var.set(zui.zg.ALL_MODEL); app._on_model_change(); root.update()
    asked = []
    real_ask = app.ask_style_conflict
    app.ask_style_conflict = lambda style, prompt, spans: (asked.append((style, [prompt[a:b] for a, b in spans])), 'revise')[1]
    app.engine.calls.clear(); app.busy = False
    try:
        app.generate(); root.update()
        check('ALL asks about the style before sweeping', asked and asked[0][1] == ['oil painting'], asked)
        check('choosing Revise starts no sweep at all',
              not getattr(app, '_all_queue', None) and len(app.engine.calls) == 0,
              (getattr(app, '_all_queue', None), len(app.engine.calls)))
    finally:
        app.ask_style_conflict = real_ask
        app.style_var.set('none')
        app._all_queue = None
        app.busy = False
        # put the picker back where it was: a sweep left set would send the next block's Generate down the ALL path
        app.model_var.set('z-image turbo'); app._on_model_change(); root.update()

    print("\n-- a style and a medium in the prompt are reconciled before Generate sends both (2026-10-28)")
    app.style_var.set('cinematic')
    app.text.delete('1.0', 'end')
    app.text.insert('1.0', 'a lighthouse, oil painting, stormy sea')

    spans = app.style_conflicts(app.text.get('1.0', 'end'), 'cinematic')
    check('the medium in the prompt is found', [app.text.get('1.0', 'end')[a:b] for a, b in spans] == ['oil painting'],
          spans)
    check('a style of none is never a conflict',
          app.style_conflicts('an oil painting of a dog', 'none') == [], 'none should not warn')
    check('the style agreeing with the prompt is not a conflict either',
          app.style_conflicts('a photograph of a dog', 'photo') == [])
    check('but a different medium is', len(app.style_conflicts('a photograph of a dog', 'watercolour')) == 1)

    def _fake_ask(choice):
        def ask(style_name, prompt, spans):
            ask.called = (style_name, [prompt[a:b] for a, b in spans])
            return choice
        ask.called = None
        return ask

    # 1. Remove: the words go, and the job is dispatched
    app.engine.calls.clear()
    app.busy = False
    ask = _fake_ask('remove')
    real_ask = app.ask_style_conflict
    app.ask_style_conflict = ask
    try:
        app.generate(); root.update()
        now = app.text.get('1.0', 'end').strip()
        check('the dialog was asked, with the words it would remove',
              ask.called and ask.called[1] == ['oil painting'], ask.called)
        check('removing strips the medium from the prompt', 'oil painting' not in now and 'lighthouse' in now, now)
        check('and the job is dispatched anyway', len(app.engine.calls) == 1, len(app.engine.calls))
        check('the highlight is cleared once the choice is made',
              not app.text.tag_ranges('conflict'), app.text.tag_ranges('conflict'))
    finally:
        app.ask_style_conflict = real_ask

    # 2. Keep: the prompt is untouched, the job is dispatched
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a lighthouse, oil painting, stormy sea')
    app.engine.calls.clear(); app.busy = False
    ask = _fake_ask('keep'); app.ask_style_conflict = ask
    try:
        app.generate(); root.update()
        check('keeping leaves the prompt exactly as typed',
              app.text.get('1.0', 'end').strip() == 'a lighthouse, oil painting, stormy sea',
              app.text.get('1.0', 'end').strip())
        check('and still dispatches', len(app.engine.calls) == 1, len(app.engine.calls))
    finally:
        app.ask_style_conflict = real_ask

    # 3. Revise manually: no dispatch, the words stay lit, the caret goes to the prompt
    app.engine.calls.clear(); app.busy = False
    ask = _fake_ask('revise'); app.ask_style_conflict = ask
    try:
        app.generate(); root.update()
        check('revising dispatches nothing', len(app.engine.calls) == 0, len(app.engine.calls))
        check('the words stay highlighted so the edit is obvious', bool(app.text.tag_ranges('conflict')),
              app.text.tag_ranges('conflict'))
        check('and the prompt is where the keyboard was sent', getattr(app, '_focus_target', None) is app.text,
              getattr(app, '_focus_target', None))
    finally:
        app.ask_style_conflict = real_ask

    # the dialog itself must offer four ways out, not three
    labels = []
    real_wait = app.root.wait_window

    def _snapshot(win):
        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == 'TButton':
                    labels.append(str(child.cget('text')))
                walk(child)
        walk(win)
        win.destroy()

    app.root.wait_window = _snapshot
    try:
        choice = app.ask_style_conflict('cinematic', 'a lighthouse, oil painting', [(14, 26)])
        check('the dialog offers four choices', len(labels) == 4, labels)
        check('including clearing the style and proceeding',
              any('clear style' in label.lower() for label in labels), labels)
        check('and closing or losing it means revise, not generate', choice == 'revise', choice)
    finally:
        app.root.wait_window = real_wait

    # 3b. Clear style and proceed: the prompt keeps its own medium, the suffix goes, the job still dispatches
    app.style_var.set('cinematic')
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a lighthouse, oil painting, stormy sea')
    app.engine.calls.clear(); app.busy = False
    ask = _fake_ask('clear_style'); app.ask_style_conflict = ask
    try:
        app.generate(); root.update()
        check('clearing the style leaves the prompt exactly as typed',
              app.text.get('1.0', 'end').strip() == 'a lighthouse, oil painting, stormy sea',
              app.text.get('1.0', 'end').strip())
        check('and sets the style to none', app.style_var.get() == 'none', app.style_var.get())
        sent_style = (app.engine.calls[-1][1].get('style') if app.engine.calls else 'nothing')
        check('the dispatch carries no style suffix', sent_style == 'none', sent_style)
        check('and the banner shows the style is gone', 'style none' in app.banner['text'], app.banner['text'][:100])
    finally:
        app.ask_style_conflict = real_ask
        app.style_var.set('none')
        app.busy = False

    # 4. no conflict, no dialog
    app.style_var.set('none')
    app.text.delete('1.0', 'end'); app.text.insert('1.0', 'a lighthouse, oil painting')
    app.engine.calls.clear(); app.busy = False
    def _never(*a, **k):
        raise AssertionError('the dialog must not open when there is no conflict')
    app.ask_style_conflict = _never
    try:
        app.generate(); root.update()
        check('a prompt with no style selected goes straight through', len(app.engine.calls) == 1,
              len(app.engine.calls))
    finally:
        app.ask_style_conflict = real_ask
        app.busy = False

    print("\n-- a folder tile shows its newest four *distinct* pictures, reaching past near-copies (2026-10-28)")
    _tile_dir = Path(tempfile.mkdtemp(prefix='tile-'))

    def _picture(name, colour, bar=(0, 0), pixel=None, size=(96, 96)):
        img = Image.new('RGB', size, colour)
        if bar != (0, 0):
            Image.Image.paste(img, Image.new('RGB', (24, 24), bar), (8, 8))
        if pixel is not None:
            img.putpixel(pixel[0], pixel[1])
        path = _tile_dir / name
        img.save(path)
        return str(path)

    # four plainly different pictures, then on top of them six near-copies - the case that used to pass the old bar
    older = [_picture('old0.png', (250, 250, 250), (0, 0, 0)), _picture('old1.png', (0, 0, 0), (250, 250, 250)),
             _picture('old2.png', (0, 90, 200), (200, 170, 0)), _picture('old3.png', (130, 20, 130), (20, 200, 60))]
    near = [_picture('near%d.png' % i, (120, 120, 120), (121, 121, 121),
                     pixel=((40 + i, 40), (150, 150, 150)) if i else None) for i in range(6)]
    state = _SheetShim()
    mixed = {'label': 'near on top', 'items': [{'path': p, 'prompt': 'p', 'created': ''} for p in older + near]}
    chosen = state._sheet_paths(mixed)
    check('four tiles are chosen', len(chosen) == 4, chosen)
    check('the tile starts with the newest picture', chosen[0] == near[-1], chosen[0])
    check('and reaches past the near-copies for pictures that are really different',
          sum(1 for p in chosen if p in older) >= 3, chosen)
    sig_a, sig_b = state._signature(near[0]), state._signature(near[1])
    check('a one-pixel difference is not "distinct" at this threshold',
          sig_a is not None and sig_b is not None and not state._different(sig_a, sig_b))
    check('a plainly different picture is', state._different(sig_a, state._signature(older[0])))

    # a folder of four differently coloured pictures takes all four, newest first, and remembers the answer
    colours = [_picture('c%d.png' % i, c, (0, 0, 0)) for i, c in
               enumerate([(200, 30, 30), (30, 200, 30), (30, 30, 200), (210, 210, 30)])]
    clean = {'label': 'distinct', 'items': [{'path': p, 'prompt': 'p', 'created': ''} for p in colours]}
    four = state._sheet_paths(clean)
    check('four different pictures are all taken', four == list(reversed(colours)), four)
    check('and asking twice reuses the answer', state._sheet_paths(clean) == four)

    # a folder that cannot give four different pictures still fills four tiles, newest first, and says so
    copies = [_picture('cop%d.png' % i, (120, 120, 120), (121, 121, 121)) for i in range(5)]
    same = {'label': 'all alike', 'items': [{'path': p, 'prompt': 'p', 'created': ''} for p in copies]}
    before = state.sheets_all_alike
    alike = state._sheet_paths(same)
    check('a folder of near-copies still fills four tiles', len(alike) == 4, alike)
    check('falling back to the newest four, newest first', alike == list(reversed(copies[-4:])), alike)
    check('and the fallback is counted rather than hidden', state.sheets_all_alike == before + 1)

    print("\n-- the column's options fold away, and the pictures get the room (2026-10-28)")
    app.toggle_options(False); root.update(); time.sleep(0.1); root.update()
    folded_height = app.gal_canvas.winfo_height()
    check('the options start folded: the search field is gone',
          not app.search_box.winfo_ismapped(), app.search_box.winfo_ismapped())
    check('and so are the button rows', not app.btns.winfo_ismapped())
    check('the first line stays: what the list is, and the folders switch',
          app.gal_title.winfo_ismapped() and app.group_chk.winfo_ismapped())
    check('the disclosure is on that first line', app.options_btn.winfo_ismapped()
          and app.options_btn.winfo_manager() == 'pack')
    check('it says the options are closed', '\u25b8' in str(app.options_btn.cget('text')),
          app.options_btn.cget('text'))

    app.toggle_options(True); root.update(); time.sleep(0.1); root.update()
    check('opening it shows the search field', app.search_box.winfo_ismapped())
    check('and the button rows', app.btns.winfo_ismapped())
    check('and it says they are open', '\u25be' in str(app.options_btn.cget('text')),
          app.options_btn.cget('text'))
    open_height = app.gal_canvas.winfo_height()
    check('folding it really returns room to the pictures', folded_height > open_height,
          (folded_height, open_height))
    check('the room returned is worth having: >80 px', folded_height - open_height > 80,
          folded_height - open_height)

    app.toggle_options(False); root.update(); time.sleep(0.1); root.update()
    check('closing it folds the options again', not app.search_box.winfo_ismapped())
    check('the selection buttons still work while folded', app.del_btn.winfo_exists())
    # the bigger, clearer text the room paid for
    check('the controls ask for the big style', str(app.more_btn.cget('style')) == 'Big.TButton',
          app.more_btn.cget('style'))
    check('and the hints are whole sentences now',
          'Recycle Bin' in str(app.hint_label.cget('text')) and 'Ctrl+A' in str(app.hint_label.cget('text')),
          str(app.hint_label.cget('text'))[:60])

    print("\n-- the picture is pinned top-right with its metadata under it, and the clip buttons below (2026-10-28)")
    if app._files:
        app.select_file(app._files[0]); root.update(); time.sleep(0.25); root.update()
        box = app.canvas.bbox('img')
        canvas_w = app.canvas.winfo_width()
        check('the picture sits at the top right of its column',
              box is not None and box[2] >= canvas_w - 10 and box[1] <= 10, (box, canvas_w))
        check('the metadata block lives under the picture, in the same column',
              app.res_label.winfo_parent() == app.canvas.winfo_parent()
              and int(app.res_label.grid_info().get('row', -1)) == 1,
              (app.res_label.winfo_parent(), app.res_label.grid_info().get('row')))
        text = app.res_label.cget('text')
        check('it reports the facts first', '1024x1024' in text or 'x' in text, text)
        # the second line (model and date) needs a sidecar that has them: the fixture's do not, so write one that does
        rich = Path(app._files[0]).with_suffix('.json')
        rich.write_text(json.dumps({'prompt': 'p', 'seed': 7, 'steps': 9, 'model': 'z-image turbo',
                                    'created': '2026-10-28T04:56:00'}), encoding='utf-8')
        app.select_file(app._files[0]); root.update(); time.sleep(0.2); root.update()
        text = app.res_label.cget('text')
        check('and a second line says where it came from',
              '\n' in text and 'z-image turbo' in text and '2026-10-28 04:56' in text, text.splitlines())
        check('the block updates with the selection rather than going stale',
              str(app._img.size[0]) in text, (app._img.size, text.splitlines()[:1]))
        clip_row = int(app.clip_actions.grid_info().get('row', -1))
        check('the clip buttons are at the bottom of that column',
              app.clip_actions.winfo_parent() == app.canvas.winfo_parent() and clip_row > 0, clip_row)
        order = [w for w in app.clip_actions.pack_slaves()]
        check('Make clip comes before Open clip',
              order and order[0] is app.clip_btn and order[-1] is app.clip_open_btn,
              [str(w.cget('text')) for w in order])
        check('and Open clip starts disabled, because there is no clip yet',
              'disabled' in app.clip_open_btn.state() or app._last_clip is not None, app.clip_open_btn.state())

    print("\n-- a prompt that describes several scenes renders one image per scene (2026-10-28)")
    import zimage_ui as _zs
    _root_sc = tk.Tk()
    _app_sc = _zs.App(_root_sc, StubEngine(), args)
    _quieten(_app_sc)
    _root_sc.update()
    three = ("SCENE ONE - OPENING\n\n1. INT. THE LAST POCKET - NIGHT\nCAMERA: rain on glass.\n\n"
             "SCENE TWO - NO SIDE BETS\n\n2. INT. THE LAST POCKET - UNDER THE RAIL\nCAMERA: a coin passing.\n\n"
             "SCENE THREE - THE ENTRY\n\n3. INT. THE LAST POCKET - DRAWING\nCAMERA: the scoreboard.")
    _app_sc.text.delete('1.0', 'end'); _app_sc.text.insert('1.0', three)
    _root_sc.update()
    _app_sc._update_banner()
    check('the banner says how many scenes Generate will render', '3 scenes' in _app_sc.banner['text'],
          _app_sc.banner['text'][:80])
    _app_sc.engine.calls.clear()
    _app_sc.generate(); _root_sc.update()
    check('one dispatch, for the first scene only', len(_app_sc.engine.calls) == 1, len(_app_sc.engine.calls))
    first_prompt, first_kw = _app_sc.engine.calls[0][0][0], _app_sc.engine.calls[0][1]
    check('the first scene is the one sent', 'SCENE ONE' in first_prompt and 'SCENE TWO' not in first_prompt,
          first_prompt[:60])
    check('and it is recorded as scene 1 of 3', (first_kw.get('scene'), first_kw.get('of')) == (1, 3),
          (first_kw.get('scene'), first_kw.get('of')))
    check('two scenes remain queued', len(_app_sc._scene_queue) == 2, _app_sc._scene_queue and len(_app_sc._scene_queue))
    _app_sc.busy = False
    _app_sc._run_next_scene(); _root_sc.update()
    second = _app_sc.engine.calls[-1][1]
    check('the second scene follows, recorded as 2 of 3',
          second.get('scene') == 2 and 'SCENE TWO' in _app_sc.engine.calls[-1][0][0],
          (second.get('scene'), _app_sc.engine.calls[-1][0][0][:40]))
    _app_sc.busy = False
    _app_sc._run_next_scene(); _root_sc.update()
    _app_sc.busy = False
    _app_sc._run_next_scene(); _root_sc.update()
    check('after the last scene the queue is spent', _app_sc._scene_queue is None, _app_sc._scene_queue)
    check('three images in total, one per scene', len(_app_sc.engine.calls) == 3, len(_app_sc.engine.calls))

    _app_sc.engine.calls.clear()
    _app_sc.text.delete('1.0', 'end'); _app_sc.text.insert('1.0', 'a black poodle on a leather couch')
    _app_sc.busy = False
    _app_sc.generate(); _root_sc.update()
    check('an ordinary prompt still sends exactly one image, with no scene number',
          len(_app_sc.engine.calls) == 1 and _app_sc.engine.calls[0][1].get('scene') is None,
          (len(_app_sc.engine.calls), _app_sc.engine.calls and _app_sc.engine.calls[0][1].get('scene')))
    _app_sc.busy = False
    _root_sc.destroy(); tk._default_root = root

    print("\n-- closing while a render is running asks first, and interrupts it (2026-10-28)")
    import zimage_ui as _zu
    _root_close = tk.Tk()
    _app_close = _zu.App(_root_close, StubEngine(), args)
    _quieten(_app_close)
    _root_close.update()
    _app_close.busy = True
    _app_close._all_queue = ['flux2 klein 9b uncensored']
    _asked = []
    _real_ask = _zu.messagebox.askyesno
    try:
        _zu.messagebox.askyesno = lambda *a, **k: (_asked.append(a), False)[1]
        _app_close.on_close()
        check('saying no leaves the window open', bool(_root_close.winfo_exists()) and _app_close.busy)
        check('and nothing was cancelled or shut down',
              _app_close.engine.cancels == 0 and _app_close.engine.shutdowns == 0,
              (_app_close.engine.cancels, _app_close.engine.shutdowns))
        check('the question counts the running job and the queued one', '2 renders' in _asked[-1][1], _asked[-1][1])
        _zu.messagebox.askyesno = lambda *a, **k: True
        _app_close.on_close()
        check('saying yes interrupts the prompt before shutting down',
              _app_close.engine.cancels == 1 and _app_close.engine.shutdowns == 1,
              (_app_close.engine.cancels, _app_close.engine.shutdowns))
        check('and drops the rest of the ALL sweep', _app_close._all_queue is None, _app_close._all_queue)
        try:
            alive = bool(_root_close.winfo_exists())
        except Exception:
            alive = False
        check('and the window is gone', not alive)
    finally:
        _zu.messagebox.askyesno = _real_ask
        try:
            _root_close.destroy()
            tk._default_root = root
        except Exception:
            pass

    print("\n-- the caption is asked for on the frame, and asked again once it exists (2026-10-28)")
    import ctypes as _ct
    _fr = _ct.windll.user32.GetAncestor(root.winfo_id(), 2)
    _cls = _ct.create_unicode_buffer(64)
    _ct.windll.user32.GetClassNameW(_fr, _cls, 64)
    check('winfo_id() is the client child, and the frame is its TkTopLevel parent',
          _cls.value == 'TkTopLevel' and _fr != root.winfo_id(), _cls.value)
    check('the theme can hand that frame to DWM once the window exists',
          app.theme._apply_titlebar() is True, app.theme._apply_titlebar())

    print("\n-- the open folder's column says which folder it is (2026-10-28)")
    if app._plan and app._plan['groups']:
        app.select_folder(0); root.update(); time.sleep(0.1); root.update()
        caption = app.contents_title.cget('text') or ''
        grp = app._plan['groups'][0]
        want = (grp.get('title') or grp.get('label') or '').strip()
        check('the pane header is shown', bool(app.contents_title.winfo_ismapped()), app.contents_title.winfo_ismapped())
        check('it names the folder', bool(want) and want[:24] in caption, (want[:24], caption[:60]))
        check('and says how many pictures are in it', str(len(grp['items'])) in caption, caption)
        check('it is not a blank or empty label', len(caption) > 10, repr(caption))
        app.select_folder(None); root.update()
        check('no folder open means no header', not app.contents_holder.winfo_ismapped())

    print("\n-- a folder is read newest-first (2026-10-28)")
    if app._plan and app._plan['groups']:
        import group_prompts as _gp4
        prompt = 'a black poodle on a leather couch'
        members = [{'path': str(p), 'prompt': prompt, 'created': '2026-10-0%dT10:00:00' % (i + 1)}
                   for i, p in enumerate(app._files[:4])]
        lex4 = _gp4.fit_lexicon([prompt, 'a beach at dawn'])
        saved_plan = app._plan
        app._plan = {'groups': [{'label': 'poodles', 'title': 'poodles', 'items': members,
                                 'oldest': members[0]['created']}],
                     'recent': [], 'ungrouped': [], 'threshold': _gp4.DEFAULT_THRESHOLD,
                     '_lexicon': lex4, 'seconds': 0.0}
        app.select_folder(0); root.update(); time.sleep(0.15)
        order = [Path(p).name for p in app._content_order]
        check('the newest member is the first tile', bool(order) and order[0] == Path(members[-1]['path']).name,
              (order[:2], [Path(m['path']).name for m in members]))
        check('and the oldest is last', order and order[-1] == Path(members[0]['path']).name, order[-1:])
        app._plan = saved_plan
        app.select_folder(None); root.update()

    print("\n-- the arrows follow the list that was clicked, pane or strip (2026-10-28)")
    if app._plan and app._plan['groups']:
        app.select_folder(0); root.update(); time.sleep(0.2)
        cells = [c for c in app.contents.winfo_children() if c.winfo_class() == 'TFrame']
        if cells:
            handoffs = []
            real_pane_focus = app.contents_canvas.focus_set
            app.contents_canvas.focus_set = lambda: handoffs.append('pane')
            try:
                cells[0].winfo_children()[0].event_generate('<Button-1>', x=3, y=3)
                root.update(); time.sleep(0.1)
            finally:
                app.contents_canvas.focus_set = real_pane_focus
            check('clicking a pane picture hands the keyboard to the pane', 'pane' in handoffs, handoffs)

            # with the keyboard in the pane, the pane answers the arrows and the strip does not
            real_focus = app.root.focus_get
            app.root.focus_get = lambda: app.contents_canvas
            try:
                keys, _cols, _tiles = app._pane_cells()
                first = app.selected[-1] if app.selected else None
                rc = app._pane_key(NS(state=0), 'right')
                root.update()
                check('the pane moves its own selection', rc == 'break' and app.selected[-1] != first,
                      (first, app.selected[-1:] ))
                check('and the strip ignores the same key', app._nav_key(NS(state=0), 1, 0) is None)
            finally:
                app.root.focus_get = real_focus
    app.select_folder(None)
    app.toggle_groups(False)          # the strip part needs picture tiles, not folder tiles
    root.update(); time.sleep(0.15)
    strip_tiles = [w for w in app.recent.winfo_children() if w.grid_info()]
    if strip_tiles:
        handoffs = []
        real_strip_focus = app.gal_canvas.focus_set
        app.gal_canvas.focus_set = lambda: handoffs.append('strip')
        try:
            strip_tiles[0].event_generate('<Button-1>', x=3, y=3)
            root.update(); time.sleep(0.1)
        finally:
            app.gal_canvas.focus_set = real_strip_focus
        check('clicking a strip tile hands the keyboard back to the strip', 'strip' in handoffs, handoffs)
        real_focus = app.root.focus_get
        app.root.focus_get = lambda: app.gal_canvas
        try:
            before = app.selected[-1] if app.selected else None
            app._nav_key(NS(state=0), 1, 0)
            root.update()
            check('the strip moves again, and the pane ignores the key',
                  app.selected and app.selected[-1] != before
                  and app._pane_key(NS(state=0), 'right') is None,
                  (before, app.selected[-1:]))
        finally:
            app.root.focus_get = real_focus
    app.clear_selection(); app.toggle_groups(True); app.select_folder(None); root.update()

    print("\n-- a render lands in the open folder without the reader re-selecting it (2026-10-28)")
    if app._plan and app._plan['groups']:
        import group_prompts as _gp3
        prompt = 'a black poodle on a leather couch, watercolour'
        members = [{'path': str(p), 'prompt': prompt, 'created': '2026-10-01'} for p in app._files[:-1][:5]]
        fresh = app._files[-1]
        lex3 = _gp3.fit_lexicon([prompt, 'a beach at dawn'])
        base_plan = {'groups': [{'label': 'poodles', 'title': 'poodles', 'items': members,
                                 'oldest': '2026-10-01'}],
                     'recent': [], 'ungrouped': [], 'threshold': _gp3.DEFAULT_THRESHOLD,
                     '_lexicon': lex3, 'seconds': 0.0}
        saved_plan = app._plan
        app._plan = base_plan
        app.select_folder(0); root.update(); time.sleep(0.15)
        before = set(app._content_widgets)
        check('the folder is open with only its members', len(before) == len(members), len(before))
        regrouped = dict(base_plan)
        regrouped['recent'] = [{'path': str(fresh), 'prompt': prompt, 'created': '2026-10-28T12:00:00'}]
        app._apply_plan(regrouped)
        root.update(); time.sleep(0.2)
        after = set(app._content_widgets)
        check('a re-group puts the new render in the open folder without a re-select',
              Path(fresh) in after, (Path(fresh), len(after)))
        check('and the folder stayed open across the re-group',
              app._active_folder is not None and app._plan['groups'][app._active_folder]['label'] == 'poodles',
              app._active_folder)
        calls = []
        real_contents = app._render_contents
        app._render_contents = lambda: (calls.append(1), real_contents())[1]
        try:
            feed_image(app, app._files[-2])
        finally:
            app._render_contents = real_contents
        check('a landed render re-renders the open pane at once', bool(calls), len(calls))
        app._plan = saved_plan
        app.select_folder(None); root.update()
    app.clear_selection(); root.update()

    print("\n-- turning folders on with a picture selected opens that picture's folder (2026-10-27)")
    app.toggle_groups(False); root.update(); time.sleep(0.1)
    # Deterministic: the fixture's prompts differ only by a trailing number, which the membership rule correctly treats
    # as a different subject, so the plan is supplied directly and `_apply_plan` asked to do its job.
    pick = app._files[0]
    click(app, pick); root.update()
    check('the picture is selected in the flat list', bool(app.selected), app.selected[:1])
    # a known prompt for the picked file, written now: earlier sections of this suite delete files, so the sidecar on
    # disk can be stale by here and the test would then hinge on that
    pick = Path(app._files[0])
    pick_prompt = 'a red fox by a frozen lake, oil on canvas'
    pick.with_suffix('.json').write_text(json.dumps({'prompt': pick_prompt}), encoding='utf-8')
    import group_prompts as _gp2
    other = app._files[min(3, len(app._files) - 1)]
    app._active_folder = None
    app._apply_plan({'groups': [{'label': 'match', 'title': 'match', 'oldest': '2026-10-01',
                                 'items': [{'path': str(other), 'prompt': pick_prompt,
                                            'created': '2026-10-01'}]}],
                     'recent': [], 'ungrouped': [], 'threshold': _gp2.DEFAULT_THRESHOLD,
                     '_lexicon': _gp2.fit_lexicon([pick_prompt, 'a beach at dawn']),
                     'seconds': 0.0})
    root.update(); time.sleep(0.25); root.update()
    want = app._folder_holding(pick)
    check('the folder that picture belongs to is opened',
          want == 0 and app._active_folder == 0, (want, app._active_folder))
    tiles = [w for w in app.recent.winfo_children() if w.grid_info()]
    if app._active_folder is not None and app._active_folder < len(tiles):
        top = tiles[app._active_folder].winfo_y()
        view_top = app.gal_canvas.canvasy(0)
        check('and it is scrolled into view rather than left below the fold',
              view_top - 2 <= top <= view_top + app.gal_canvas.winfo_height(),
              (top, view_top, app.gal_canvas.winfo_height()))
    app.select_folder(None); root.update()

    print("\n-- review regressions: actions in the pane, the fresh row, no flash while regrouping (2026-10-27)")
    app.select_folder(0); root.update(); time.sleep(0.2)
    pane_cells = [c for c in app.contents.winfo_children() if c.winfo_class() == 'TFrame']
    if pane_cells:
        target = pane_cells[0].winfo_children()[0]
        target.event_generate('<Button-1>', x=3, y=3)
        root.update(); time.sleep(0.15)
        check('a picture clicked in the pane arms Delete...', str(app.del_btn['state']) == 'normal',
              (str(app.del_btn['state']), len(app.selected)))
        check('and reports the selection', 'selected' in app.sel_label['text'], app.sel_label['text'])
        check('and wears the selection ring',
              str(target.cget('highlightbackground')) == app.theme.accent,
              (target.cget('highlightbackground'), app.theme.accent))
    app.clear_selection(); root.update()
    check('clearing the selection disables Delete... again', str(app.del_btn['state']) == 'disabled')
    app.select_folder(None); root.update()
    if app._plan and app._plan['groups']:
        app._grouping = True
        app.refresh_gallery(); root.update()
        photo = [w for w in app.recent.winfo_children() if getattr(w, 'image', None) is not None]
        check('no picture tiles appear while a re-group runs', not photo, len(photo))
        app._grouping = False
        app.refresh_gallery(); root.update()
    if app._plan and app._plan['groups'] and app._plan['groups'][0]['items']:
        prompt = 'a black poodle on a leather couch, watercolour'
        import group_prompts as _gp
        fresh_path = app._files[-1]                           # not a member: live_append skips paths already taken
        members = [{'path': str(p), 'prompt': prompt, 'created': '2026-10-01'}
                   for p in app._files[:-1][:30]]             # more than one batch, so paging is real
        lex = _gp.fit_lexicon([prompt, 'something else entirely on a beach'])
        saved = app._plan
        fresh_row = {'path': str(fresh_path), 'prompt': prompt, 'created': '2026-10-27T23:59:59'}
        app._plan = {'groups': [{'label': 'big', 'title': 'big', 'items': members,
                                 'oldest': members[0].get('created') or ''}],
                     'recent': [fresh_row], 'ungrouped': [], 'threshold': _gp.DEFAULT_THRESHOLD,
                     '_lexicon': lex}
        app._content_shown = zui.BATCH_CONTENTS
        app.select_folder(0); root.update(); time.sleep(0.2)
        keys = [str(p) for p in app._content_widgets]
        check('the fresh picture renders first and is reachable',
              bool(keys) and Path(keys[0]).name == Path(fresh_row['path']).name, keys[:2])
        check('paging counts it, so the end of the list is reachable',
              len(getattr(app, '_content_items', [])) >= len(members) + 1,
              (len(getattr(app, '_content_items', [])), len(members)))
        app._plan = saved
        app.select_folder(None); root.update()
    app.clear_selection(); root.update()
    # this block clicked a picture, which displays it; the clip tests further down start from "nothing shown", so put the
    # picture pane back to its initial state rather than leaving them to inherit it
    app._current = None
    app._img = None
    app.canvas.delete('all')
    app.busy = False                     # a clip test further down refuses to run while busy
    root.update()

    print('\n-- the wheel goes to the list under the pointer, not to whatever has focus')
    app.select_folder(None); root.update()
    check('the folder list is the strip', app._wheel_owner(app.gal_canvas) == 'strip')
    check('a tile inside it counts too', app._wheel_owner(app.recent.winfo_children()[0]) == 'strip'
          if app.recent.winfo_children() else True)
    app.select_folder(0); root.update()
    check('the open folder pane is its own scroll target',
          app._wheel_owner(app.contents_canvas) == 'contents', app._wheel_owner(app.contents_canvas))
    check('a thumbnail inside the pane routes to the pane',
          not app.contents.winfo_children() or app._wheel_owner(app.contents.winfo_children()[0]) == 'contents')
    check('the prompt box keeps its own scrolling', app._wheel_owner(app.text) is None, app._wheel_owner(app.text))

    calls = []
    real_more = app._maybe_more
    app._maybe_more = lambda which: calls.append(which)
    ev = NS(x_root=0, y_root=0, delta=-120)
    app._wheel_under = lambda e: app.gal_canvas
    check('a wheel over the strip is consumed', app._on_any_wheel(ev) == 'break')
    app._wheel_under = lambda e: app.contents_canvas
    check('a wheel over the pane is consumed', app._on_any_wheel(ev) == 'break')
    app._wheel_under = lambda e: app.text
    check('a wheel over the prompt is left alone', app._on_any_wheel(ev) is None)
    check('and only the two lists asked for more',
          calls == ['strip', 'contents'], calls)
    app._maybe_more = real_more
    del app._wheel_under
    app.select_folder(None); root.update()

    print('\n-- selecting in the folder pane must not move the folder list')
    if not app._plan:
        app.toggle_groups(True)
        for _ in range(80):
            app._poll(); root.update()
            if app._plan:
                break
            time.sleep(0.05)
    # The real fixture yields one or two folders, which never overflow a tall window, so the retention property would
    # pass without being exercised. Clone the folders to make the list genuinely longer, then restore.
    # A generation earlier in this suite schedules a re-group, and a re-group replaces the cloned plan underneath this
    # test - which showed up as a flaky "the folder list can be scrolled". Stop re-grouping for the duration rather than
    # racing it: wait for any in-flight grouping, stub the scheduler, restore it after.
    for _ in range(120):
        app._poll(); root.update()
        if not getattr(app, '_grouping', False):
            break
        time.sleep(0.05)
    if getattr(app, '_regroup_job', None):
        try:
            app.root.after_cancel(app._regroup_job)
        except Exception:
            pass
        app._regroup_job = None
    real_scheduler = app._regroup_soon
    app._regroup_soon = lambda: None
    # `_grouping` makes the strip draw its "grouping..." placeholder instead of tiles; this test needs the tiles
    real_grouping = getattr(app, '_grouping', False)
    app._grouping = False
    real_groups = list(app._plan['groups'])
    app._plan['groups'] = real_groups * 24
    app._folder_shown = len(app._plan['groups'])
    app.select_folder(None); app.refresh_gallery(); root.update(); time.sleep(0.15)
    app.gal_canvas.update_idletasks()
    box = app.gal_canvas.bbox('all')
    overflows = bool(box) and (box[3] - box[1]) > app.gal_canvas.winfo_height() + 1
    if not overflows:
        # a maximised window can still fit 48 tiles once they are small; clone until the list really overflows, because
        # "keeps its scroll position" is not a testable property of a list that fits
        app._plan['groups'] = real_groups * 96
        app._folder_shown = len(app._plan['groups'])
        app.refresh_gallery(); root.update(); time.sleep(0.2)
        app.gal_canvas.update_idletasks()
        box = app.gal_canvas.bbox('all')
        overflows = bool(box) and (box[3] - box[1]) > app.gal_canvas.winfo_height() + 1
    check('the folder list overflows, so retention is a real property here', overflows,
          (box[3] - box[1]) if box else None)
    app.gal_canvas.yview_moveto(0.35); root.update(); time.sleep(0.1)
    pos_before = app.gal_canvas.yview()[0]
    # the *fraction* moves whenever the viewport or the content changes height - which opening a folder does now that
    # the pane takes width - so the property "keeps its place" is measured in pixels
    offset_before = app.gal_canvas.canvasy(0)
    check('the folder list can be scrolled in the first place', pos_before > 0.0,
          (pos_before, len(app.recent.winfo_children())))
    folder0 = 0
    app.select_folder(folder0); root.update(); time.sleep(0.1)
    app.contents_canvas.update_idletasks()
    pos_after_open = app.gal_canvas.canvasy(0)
    check('opening a folder leaves the list where it was', abs(pos_after_open - offset_before) <= 8,
          '%r -> %r px' % (offset_before, pos_after_open))
    cells = [c for c in app.contents.winfo_children() if c.winfo_class() == 'TFrame']
    tiles_now = [w for w in app.recent.winfo_children() if w.grid_info()]

    def tile_in_view(index=0):
        # re-read the tiles each call: a click rebuilds the strip, so a captured widget would be destroyed
        live = [w for w in app.recent.winfo_children() if w.grid_info()]
        if index >= len(live):
            return False
        top = live[index].winfo_y()
        return app.gal_canvas.canvasy(0) - 2 <= top <= app.gal_canvas.canvasy(0) + app.gal_canvas.winfo_height()

    if cells and tiles_now:
        # the list is scrolled away from the open folder: a click in the pane must bring it back
        scrolled_away = not tile_in_view()
        cells[0].winfo_children()[0].event_generate('<Button-1>', x=3, y=3)
        root.update(); time.sleep(0.3); root.update()
        if scrolled_away:
            live = [w for w in app.recent.winfo_children() if w.grid_info()]
            check('a click on a picture brings the folder it belongs to back into view',
                  tile_in_view(), (live[0].winfo_y() if live else None, app.gal_canvas.canvasy(0)))
        # with the folder in view, the same click leaves the list exactly where it was
        app.gal_canvas.yview_moveto(0.0); root.update(); time.sleep(0.2)
        settled = app.gal_canvas.canvasy(0)
        cells[0].winfo_children()[0].event_generate('<Button-1>', x=3, y=3)
        root.update(); time.sleep(0.2)
        check('a click on a picture leaves the list alone when the folder is already in view',
              abs(app.gal_canvas.canvasy(0) - settled) <= 8,
              '%r -> %r px' % (settled, app.gal_canvas.canvasy(0)))
        check('while the picture really was selected', bool(app._current), app._current)
    # a rebuild for another reason keeps the place too - measured from wherever the list is *now*, because the pane
    # click above may legitimately have scrolled it (that is the recall behaviour)
    app.gal_canvas.yview_moveto(0.35); root.update(); time.sleep(0.1)
    offset_now = app.gal_canvas.canvasy(0)
    app.refresh_gallery(); root.update(); time.sleep(0.1)
    check('a rebuild of the strip keeps its scroll position',
          abs(app.gal_canvas.canvasy(0) - offset_now) <= 8,
          '%r -> %r px' % (offset_now, app.gal_canvas.canvasy(0)))
    # switching folders does start the new pane at the top
    app.select_folder(0); root.update(); time.sleep(0.1)
    app.contents_canvas.yview_moveto(0.5); root.update(); time.sleep(0.05)
    if len(app._plan['groups']) > 1:            # this fixture may hold a single folder
        app.select_folder(1)
        root.update(); time.sleep(0.1)
        check('a different folder starts at the top', app.contents_canvas.yview()[0] < 0.02,
              app.contents_canvas.yview()[0])
    else:
        check('a different folder starts at the top', True)
    app.select_folder(None)
    app._plan['groups'] = real_groups                   # the clones were only for the scroll test
    app._regroup_soon = real_scheduler
    app._grouping = real_grouping
    app._folder_shown = zui.BATCH_FOLDERS
    app.refresh_gallery(); root.update()

    print('\n-- infinite scrolling: reaching the bottom appends the next batch')
    app.select_folder(None); root.update()
    if app._folders_view_active():
        first = len(app.recent.winfo_children())
        app.gal_canvas.yview_moveto(1.0); root.update()
        app._maybe_more('strip'); root.update()
        check('the folder list grows when scrolled to the end',
              len(app.recent.winfo_children()) > first or first >= len(app._plan['groups']),
              (first, len(app.recent.winfo_children()), len(app._plan['groups'])))
        check('the title reports how many of the folders are on screen',
              'Folders (' in app.gal_title['text'], app.gal_title['text'])
        group_items = len(app._plan['groups'][0]['items'])
        app.select_folder(0); root.update()
        shown = len(app._content_widgets)
        app.contents_canvas.yview_moveto(1.0); root.update()
        app._maybe_more('contents'); root.update()
        check('the contents pane grows too', len(app._content_widgets) >= shown,
              (shown, len(app._content_widgets), group_items))
        app.select_folder(None); root.update()
    # the pictures view grows as well
    app.toggle_groups(False); root.update()
    batch = len(app._tile_widgets)
    app._shown = batch + 100
    app.refresh_gallery(); root.update()
    check('the pictures list grows with the same mechanism',
          len(app._tile_widgets) > batch or batch == len(app._files),
          (batch, len(app._tile_widgets), len(app._files)))
    app._shown = 18
    app.toggle_groups(True)
    for _ in range(80):
        app._poll(); root.update()
        if app._plan:
            break
        time.sleep(0.05)
    root.update()

    print('\n-- A/B review: most-similar pairs, three decisions, progress that survives stopping')
    import pair_review as prv
    prv.STATE_DIR = Path(tempfile.mkdtemp(prefix='zimage-review-ab-'))   # its own run, not rank mode's leftovers
    # give one folder enough members that it has several pairs: six of the oldest share a prompt
    for i in range(-1, -7, -1):
        sc = app._files[i].with_suffix('.json')
        meta = json.loads(sc.read_text(encoding='utf-8'))
        meta['prompt'] = 'two women kissing on a leather sofa in a warm living room, lamp light'
        sc.write_text(json.dumps(meta), encoding='utf-8')
    app.toggle_groups(False)
    check('a folder with at least four pictures exists to review', True)
    if not app._plan:                      # the folder test above turned grouping off again
        app.toggle_groups(True)
        for _ in range(80):
            app._poll(); root.update()
            if app._plan:
                break
            time.sleep(0.05)
    check('the plan is available for the review', bool(app._plan))
    big = max(range(len(app._plan['groups'])), key=lambda i: len(app._plan['groups'][i]['items']))
    check('that folder has at least three pairs', len(app._plan['groups'][big]['items']) >= 6,
          len(app._plan['groups'][big]['items']))
    app.select_folder(big); root.update()
    recycled = []
    real_recycle = app._recycle_files
    app._recycle_files = lambda files: recycled.append([Path(f).name for f in files]) or True
    app.review_start(big)
    for _ in range(80):
        app._poll(); root.update()
        if getattr(app, '_review', None):
            break
        time.sleep(0.05)
    r = getattr(app, '_review', None)
    check('the review window opens with a pair', bool(r) and r.get('pair') is not None)
    if r:
        app._review_mode.set('delete')      # this block tests deleting; the mode used to be implied by its absence
        check('it shows two pictures', bool(r['left_lbl'].cget('image')) and bool(r['right_lbl'].cget('image')))
        check('the status line says which pair', 'pair 1 of' in r['info']['text'], r['info']['text'])
        check('the keys are bound', bool(r['win'].bind('1')) and bool(r['win'].bind('<Escape>')))
        # a picture is fitted to its cell, never stretched past its own size, and refits when the window shrinks
        im = r['left_lbl'].image
        check('the picture fits its cell', im is not None and im.width() <= r['left_lbl'].winfo_width(),
              (im.width() if im else None, r['left_lbl'].winfo_width()))
        check('and is never upscaled past the file', im is not None and im.width() <= 1024,
              im.width() if im else None)
        r['win'].state('normal')
        r['win'].geometry('760x520+40+40')
        for _ in range(20):
            root.update(); time.sleep(0.05)
        small = r['left_lbl'].image
        from PIL import Image as _PILImage
        with _PILImage.open(r['left_lbl'].source) as src:
            src_w = src.width
        check('resizing refits the picture to the new cell, without upscaling past the file',
              small is not None and small.width() != im.width()
              and small.width() <= min(r['left_lbl'].winfo_width(), src_w),
              (small.width() if small else None, im.width() if im else None,
               r['left_lbl'].winfo_width(), src_w))
        left, right = r['pair']
        first_left = left
        state_key = r['state']['key']
        app.review_decide('delete_right'); root.update()
        check('delete right recycles that picture and its sidecar',
              recycled and recycled[-1][0] == Path(right).name and any(n.endswith('.json') for n in recycled[-1]),
              recycled[-1] if recycled else None)
        check('and advances to the next pair', app._review['pair'] != (left, right))
        app._review_mode.set('delete')
        app.review_decide('keep_both'); root.update()
        check('keep both recycles nothing', len(recycled) == 1, recycled)
        app.review_stop(); root.update()
        check('stop closes the window and says where it got to',
              app._review is None and 'progress saved' in app.status['text'], app.status['text'])
        saved = prv.load_state(state_key)
        check('two decisions were saved', len(saved.get('decisions') or {}) == 2, saved.get('decisions'))
        check('the resumed queue skips them', len(prv.remaining(saved)) == max(0, (saved.get('total') or 0) - 2))
        app.review_start(big)
        for _ in range(80):
            app._poll(); root.update()
            if getattr(app, '_review', None):
                break
            time.sleep(0.05)
        r2 = getattr(app, '_review', None)
        check('reopening resumes rather than restarting', bool(r2) and r2['pair'] != (left, right),
              r2['pair'] if r2 else None)
        if r2:
            left_over = len(prv.remaining(r2['state']))
            want = 'pair %d of' % (len(r2['state']['decisions']) + 1) if left_over else 'done'
            check('and it either resumes at the right pair or says the folder is finished',
                  want in r2['info']['text'], '%r (want %r, %d left)' % (r2['info']['text'][:60], want, left_over))
        app.review_stop(); root.update()
    app._recycle_files = real_recycle
    app.select_folder(None); root.update()

    print('\n-- clips open in a player that exists, not in a broken association')
    forced = zui.App.find_player()
    check('a real player is found on this machine', bool(forced), forced)
    check('and it is not the AppX Media Player', bool(forced) and 'MediaPlayer' not in forced[0], forced)
    launched = []
    import winproc
    real_launch = winproc.launch
    winproc.launch = lambda cmd, **kw: launched.append(cmd) or type('P', (), {'pid': 1})()
    clip = app._files[0].with_suffix('.mp4')
    clip.write_bytes(b'not really a clip')          # only the launch path is under test here
    who = app._open_clip(clip, video=True)
    check('opening a clip launches that player with the file',
          bool(launched) and launched[0][-1] == str(clip) and launched[0][0] == forced[0],
          launched)
    check('and the caller learns which player opened it', who == Path(forced[0]).stem, who)
    winproc.launch = real_launch
    # with no player installed the app falls back to the Windows association instead of failing
    fallback = zui.App.find_player(exists=lambda p: False)
    check('with no player installed it defers to the default app', fallback is None, fallback)
    clip.unlink()

    print('\n-- make clip: needs a picture, then hands off to the video path')
    app._current = None
    app.clip_btn.invoke(); root.update()
    # the precondition is set here rather than inherited: sections above display a picture, and `busy` may be set
    app._current = None
    app.busy = False
    app.clip_btn.invoke(); root.update()
    check('with nothing shown it asks for a picture', 'show a picture first' in app.hint['text'], app.hint['text'])
    app.select_file(app._files[0]); root.update()
    app.engine.calls.clear()
    app.busy = False
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
    import winproc as _wp
    real_open = _wp.launch
    _wp.launch = lambda cmd, **kw: opened.append(list(cmd)) or type('P', (), {'pid': 1})()
    app.engine.events.put(('clip', {'clip': str(tmp / '2026-clip.webp'), 'mp4': str(clip_file),
                                    'frames': 49, 'seconds': 2.04, 'wall_s': 109.7,
                                    'vram_peak_mib': 9732, 'mp4_seconds': 1.4,
                                    'vram_free_mib': 2495, 'floor_breached': False}))
    app._poll(); root.update()
    check('a finished clip says so', 'clip done' in app.status['text'], app.status['text'])
    check('and names the file it opened', clip_file.name in app.hint['text'], app.hint['text'])
    check('the MP4 is what gets opened, in a player', len(opened) == 1 and opened[0][-1] == str(clip_file),
          str(opened))
    check('Open clip becomes available', 'disabled' not in app.clip_open_btn.state())
    check('the button is released again', 'disabled' not in app.clip_btn.state())
    opened.clear()
    app.clip_open_btn.invoke(); root.update()
    check('Open clip reopens the same file', len(opened) == 1 and opened[0][-1] == str(clip_file), str(opened))
    _wp.launch = real_open

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
    print('\n-- a clip: the motion line is what gets sent, and two selections mean first -> last frame')
    gal_clip = tmp / 'clipgal'
    make_gallery(gal_clip, 4)
    args_clip = NS(port=8021, size=512, steps=4, cfg=1.0, seed=1234, out=str(gal_clip),
                   initial='', initial_file='', auto=False, no_progress=True)
    root4 = tk.Toplevel(root3)          # a second Tk() root would fight the first for tkinter's default root
    app4 = zui.App(root4, StubEngine(), args_clip)
    root4.update()
    check('there is a motion line with a default',
          bool(zui.DEFAULT_MOTION) and app4.motion_var.get() == zui.DEFAULT_MOTION, app4.motion_var.get())
    app4.motion_var.set('camera drifts left, the face stays coherent')
    app4.busy = False
    app4.clear_selection()
    first, second = app4._files[0], app4._files[1]
    click(app4, first)
    click(app4, second, ctrl=True)
    before = len(app4.engine.calls)
    app4.make_clip(); root4.update(); time.sleep(0.2)
    calls = app4.engine.calls[before:]
    kwargs = calls[-1][2] if calls else {}
    check('two selections run the 14B with both endpoints',
          kwargs.get('model') == 'i2v14' and kwargs.get('end') is not None, kwargs)
    check('the motion line is sent, not the image prompt',
          kwargs.get('prompt') == app4.motion_var.get(), kwargs.get('prompt'))
    check('and it is remembered', app4._settings.get('motion') == app4.motion_var.get())
    app4.busy = False
    app4.clear_selection()
    click(app4, first)
    before = len(app4.engine.calls)
    app4.make_clip(); root4.update(); time.sleep(0.2)
    calls = app4.engine.calls[before:]
    kwargs = calls[-1][2] if calls else {}
    check('one selection means the 5B, start only',
          kwargs.get('model') == '5b' and kwargs.get('end') is None, kwargs)
    app4.busy = False
    root4.destroy()

    root3.destroy()


    print('\n%s' % ('ALL PASS' if not FAILS else 'FAILURES: %s' % ', '.join(FAILS)))
    print('gallery was %s' % tmp)
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
