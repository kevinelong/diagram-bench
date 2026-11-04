"""test_gallery_fullscreen.py - the gallery tab as a picture window: full screen, zoom, pan, slideshow, chrome.

Written because the first version of these keys was wired to handlers that took an `event` and were called with none,
so pressing F11 raised `TypeError: <lambda>() missing 1 required positional argument: 'e'` inside a Tk callback - which
a test that calls the methods directly cannot see. Half of this file therefore drives real key events at the window and
reads the state back.

    python test_gallery_fullscreen.py                 # run it
    python test_gallery_fullscreen.py --shots C:/tmp  # and write a PNG per state

No ComfyUI, no models: the engine is stubbed and the gallery is a temp directory of generated PNGs with sidecars.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

FAILS = []


def check(name, cond, detail=''):
    print('%-52s %s%s' % (name, 'PASS' if cond else 'FAIL', (' ' + str(detail)) if detail else ''))
    if not cond:
        FAILS.append(name)


def settle(root, seconds=0.25):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.02)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--shots', default='', help='write a PNG of each state here')
    a = ap.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix='gallery-fullscreen-'))
    os.environ['LOCALAPPDATA'] = str(tmp / 'appdata')        # never the real profile
    os.environ['ZIMAGE_SETTINGS_DIR'] = str(tmp / 'settings')
    os.environ.setdefault('ZIMAGE_NO_SETTINGS', '1')
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    import tkinter as tk
    import pair_review
    import test_gallery_ui as T
    import zimage_ui as zui
    T.SHOTS = a.shots

    gallery = tmp / 'gallery'
    T.make_gallery(gallery, n=12)
    root = tk.Tk()
    root.geometry('1200x800')
    args = T.NS(port=8031, size=512, steps=4, cfg=1.0, seed=1, out=str(gallery),
                initial='', initial_file='', auto=False, no_progress=True)
    app = zui.App(root, T.StubEngine(), args)
    for _ in range(80):
        app._poll()
        root.update()
        time.sleep(0.02)

    paths = [str(p) for p in app._files[:6]]
    key = pair_review.state_key(paths)
    state = pair_review.load_state(key)
    pair_review.build(paths, state, hash_store=pair_review.STATE_DIR / ('%s-hashes.json' % key))
    pair_review.save_state(state)
    app.review_open('fullscreen test', state)
    app._review_mode.set('gallery')
    app._review_relabel()
    settle(root, 0.6)

    r = app._review
    win = r['win']
    check('gallery window opens', win.winfo_exists())
    check('one picture, not a pair', not r['right_lbl'].winfo_ismapped())
    check('zoom and slideshow chips appear', r['chips'][0].winfo_ismapped())
    drawn = r['left_lbl'].image
    check('a picture is drawn', drawn is not None and type(drawn).__name__ == 'PhotoImage', type(drawn).__name__)
    check('the count is on the picture', '1 / 6' in r['badge']['text'], r['badge']['text'])

    # --- the keys, as real events: this is the class of bug that made this file -------------------------------
    win.focus_force()
    settle(root, 0.2)
    win.event_generate('<KeyPress-z>', when='now')
    settle(root, 0.2)
    check('z cycles the zoom', r['gzoom'] == 'centred', r['gzoom'])
    check('the chip says which rung', 'zoom: centred' in r['chips'][1]['text'], r['chips'][1]['text'])
    win.event_generate('<KeyPress-z>', when='now')
    win.event_generate('<KeyPress-z>', when='now')
    settle(root, 0.2)
    check('z keeps walking', r['gzoom'] == '2x', r['gzoom'])
    win.event_generate('<KeyPress-a>', when='now')
    settle(root, 0.2)
    check('a starts the slideshow', r['gauto'] == 1 and r['auto_job'] is not None, r['gauto'])
    win.event_generate('<KeyPress-F11>', when='now')
    settle(root, 0.5)
    check('F11 goes full screen', bool(win.overrideredirect()), win.geometry())
    if a.shots:
        T.shot(win, 'gallery-fullscreen')
    win.event_generate('<Escape>', when='now')
    settle(root, 0.4)
    check('Escape leaves full screen and not the window',
          win.winfo_exists() and not win.overrideredirect())

    # --- the ladder, measured by what is actually drawn ------------------------------------------------------
    sizes = {}
    for rung in ('centred', 'original', '2x', '4x', 'fit'):
        r['gzoom'] = rung
        r['gpan'] = (0, 0)
        app._gallery_paint()
        root.update()
        sizes[rung] = r['gdraw'][2:4]
    check('the ladder changes the drawn size', len(set(sizes.values())) >= 3, sizes)
    check('2x is twice the original', sizes['2x'] == tuple(v * 2 for v in sizes['original']), sizes)
    check('4x is four times the original', sizes['4x'] == tuple(v * 4 for v in sizes['original']), sizes)

    # --- chrome hides on the clock, returns on motion --------------------------------------------------------
    app._gallery_touch()
    root.update()
    check('chrome arms a hide timer', r['hide_job'] is not None)
    app._gallery_hide_chrome()
    root.update_idletasks()
    # grid_info(), not winfo_ismapped(): a stray <Motion> from the real pointer can re-show the chrome between the
    # hide and the assertion, and the mapping is then true for a reason the code under test did not cause.
    check('chrome hides', not r['bar'].grid_info(), r['bar'].grid_info())
    win.event_generate('<Motion>', when='now')
    settle(root, 0.2)
    check('motion brings the chrome back', bool(r['bar'].grid_info()))
    if a.shots:
        T.shot(win, 'gallery-chrome-hidden' if not r['bar'].winfo_ismapped() else 'gallery-chrome')

    # --- a pan needs a picture bigger than its cell ----------------------------------------------------------
    win.overrideredirect(False)
    win.state('normal')
    win.geometry('700x500')
    settle(root, 0.8)                     # let the window manager finish resizing before the cell is measured
    r['gzoom'] = '4x'
    r['gpan'] = (0, 0)
    app._gallery_paint()
    root.update_idletasks()
    ox, oy, tw, th, cw, ch = r['gdraw']
    check('4x overflows a small window', tw > cw or th > ch, (tw, th, cw, ch))

    class Ev:
        pass
    down = Ev()
    down.x, down.y = 100, 100
    move = Ev()
    move.x, move.y = 160, 140
    app._gallery_press(down)              # pressed and dragged before any repaint can shrink the cell again
    app._gallery_drag(move)
    root.update_idletasks()
    check('a drag pans the picture', r['gpan'] != (0, 0), r['gpan'])
    app._gallery_release(move)
    r['gzoom'] = 'fit'
    r['gpan'] = (0, 0)
    app._gallery_paint()
    root.update()
    ox, oy, tw, th, cw, ch = r['gdraw']
    check('fit never overflows', tw <= cw and th <= ch, (tw, th, cw, ch))
    app._gallery_press(down)
    check('a picture that fits offers no pan', r['gdrag'] is None)
    check('the pan cursor is cleared after a release', str(r['left_lbl'].cget('cursor')) in ('', 'arrow'))

    # --- the other two modes are untouched -------------------------------------------------------------------
    app._review_mode.set('rank')
    app._review_relabel()
    settle(root, 0.3)
    check('rank mode hides the gallery chips', not r['chips'][0].winfo_ismapped())
    check('rank mode shows the pair', r['right_lbl'].winfo_ismapped())
    check('rank mode restores the bar', r['bar'].winfo_ismapped())
    check('rank mode stops the slideshow', r['auto_job'] is None and r['gauto'] == 0 or r['auto_job'] is None,
          (r['gauto'], r['auto_job']))

    # --- stopping leaves nothing behind ----------------------------------------------------------------------
    app._review_mode.set('gallery')
    app._review_relabel()
    settle(root, 0.3)
    app._gallery_cycle_auto()
    root.update()
    app.review_stop()
    root.update()
    check('Stop cancels both timers', r['auto_job'] is None and r['hide_job'] is None,
          (r['auto_job'], r['hide_job']))
    check('Stop closes the window', not win.winfo_exists())

    print('\n%s' % ('ALL PASS' if not FAILS else 'FAILURES: %s' % ', '.join(FAILS)))
    print('gallery was %s' % gallery)
    root.destroy()
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
