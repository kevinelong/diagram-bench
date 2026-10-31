"""repro_folders.py - does the strip come back to folders after a render, without toggling?

A diagnostic tool, not a test: it prints the state that decides the strip (busy, _grouping, _plan,
_active_folder, _folders_view_active(), children) instead of asserting, so it can be read while the real
app misbehaves. Run it from anywhere.

Reported 2026-10-31: "seems like we need to turn folders off then back on again to see folder groups".

Builds the real App against a temp gallery exactly as the suite does, turns folders on, waits for the
plan, delivers one finished render the way the engine does, waits past the 1.2 s regroup debounce, and
reports every piece of state that decides whether the strip draws folders:

    busy, _grouping, _plan is not None, group_var, _folders_view_active(), and the strip's children

Run: python repro_folders.py
"""
from __future__ import annotations

import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent          # tools/ -> the repo, where the app and the suite live
sys.path.insert(0, str(ROOT))
import test_gallery_ui as T
import zimage_ui as zui


def state(app, label):
    tiles = len(app.recent.winfo_children())
    print(f'  {label:34s} busy={getattr(app, "busy", None)!s:5s} grouping={app._grouping!s:5s} '
          f'plan={"yes" if app._plan else "no":3s} folders_var={app.group_var.get()!s:5s} '
          f'active={app._active_folder!s:4s} view_active={app._folders_view_active()!s:5s} strip_children={tiles}')


def settle(app, root, seconds=2.6):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app._poll()
        root.update()
        time.sleep(0.05)


def main():
    tmp = Path(tempfile.mkdtemp(prefix='folders-repro-'))
    import os
    os.environ['LOCALAPPDATA'] = str(tmp / 'appdata')
    files = T.make_gallery(tmp, 30)
    root = tk.Tk()
    args = T.NS(port=8019, size=512, steps=4, cfg=1.0, seed=1234, out=str(tmp),
                initial='', initial_file='', auto=False, no_progress=True)
    app = zui.App(root, T.StubEngine(), args)
    root.update()

    print('-- startup')
    settle(app, root, 2.6)
    state(app, 'after first regroup')

    print('\n-- folders on (what the user does first)')
    app.toggle_groups(True)
    settle(app, root, 2.6)
    state(app, 'after toggle_groups(True)')

    print('\n-- one render lands, the way the engine delivers it (busy for the whole run)')
    fresh = tmp / 'landed.png'
    T.Image.new('RGB', (32, 32), 'teal').save(fresh)
    app.busy = True                      # generate() does this; feed_image() does not
    app.engine.events.put(('tile', (fresh.read_bytes(), str(fresh), 1, 1)))
    app._poll(); root.update()
    state(app, 'mid-run (tile arrived)')
    T.feed_image(app, fresh)
    root.update()
    state(app, 'immediately after feed_image')
    settle(app, root, 3.2)                 # past the 1.2 s debounce that _regroup_soon uses
    state(app, 'after regroup debounce')

    print('\n-- and after a manual toggle, for comparison')
    app.toggle_groups(False)
    settle(app, root, 0.4)
    state(app, 'toggle_groups(False)')
    app.toggle_groups(True)
    settle(app, root, 2.6)
    state(app, 'toggle_groups(True) again')

    root.destroy()
    print(f'\ngallery {tmp}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
