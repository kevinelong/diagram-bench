"""real_session.py - drive the window over a real gallery and write down what it measured.

The suite's gallery is 26 generated pictures; the question "where is the redraw slow" is about a real folder with thousands
of sidecars and 1536 px renders. This opens the real gallery in the real window, performs the actions a person performs -
open a folder, look at a picture, scroll, change the zoom, resize the window - and then writes the profile the window has
been collecting.

    python tools/real_session.py                       # C:/Users/kevin/Pictures/zimage-ui
    python tools/real_session.py "D:/pictures" --folders 12 --out profile.json

No server is contacted: the engine is a stub, so nothing is generated and no ComfyUI is started.
"""

import argparse
import json
import os
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import zimage_ui as zui  # noqa: E402


class StubEngine:
    """No server, no generation: this measures drawing, not sampling."""

    def __init__(self):
        self.host, self.port = '127.0.0.1', 8006
        self.events = __import__('queue').Queue()
        self.autostart = False
        self.ready = True
        self.last_workflow = None

    def generate(self, *a, **kw):
        self.events.put(('error', 'stub engine: nothing is generated in a profiling session'))

    def shutdown(self):
        return 'stub'


def pump(root, seconds=0.25):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('gallery', nargs='?', default=r'C:/Users/kevin/Pictures/zimage-ui')
    ap.add_argument('--folders', type=int, default=8, help='how many folders to open')
    ap.add_argument('--out', default=None, help='where to write the profile (default: beside the settings)')
    args = ap.parse_args(argv)

    gallery = Path(args.gallery)
    if not gallery.is_dir():
        print('not a folder: %s' % gallery)
        return 2
    pictures = [p for p in gallery.iterdir() if p.suffix.lower() in zui.App.IMAGE_SUFFIXES]
    print('gallery %s: %d pictures on disk' % (gallery, len(pictures)))

    os.environ.setdefault('ZIMAGE_SETTINGS_DIR', tempfile.mkdtemp(prefix='profile-session-'))
    root = tk.Tk()
    root.geometry('1600x1000')
    app = zui.App(root, StubEngine(), NS(port=8006, size=1024, steps=4, cfg=1.0, seed=1234,
                                         out=str(gallery), initial='', initial_file='',
                                         auto=False, no_progress=True))
    root.update()
    # wait for the first scan and the grouping plan: that is the cold start users actually pay for
    deadline = time.time() + 120
    while time.time() < deadline and not (app._plan and (app._plan.get('groups') or app._folders is not None)):
        pump(root, 0.05)
    pump(root, 1.0)
    zui.PROFILE.snapshot.update({'session': 'real', 'gallery': gallery.name, 'pictures_on_disk': len(pictures)})
    print('first plan after %.1f s; %d pictures listed, %d folders'
          % (0.0, len(app._files), len(((app._plan or {}).get('groups') or []))))

    groups = list(((app._plan or {}).get('groups') or []))

    # 1. open folders, the commonest action in the window: select_folder takes an index into that list
    opened = 0
    for index in range(min(args.folders, len(groups))):
        try:
            app.select_folder(index)
            pump(root, 0.25)
            opened += 1
        except Exception as e:
            print('  could not open folder %d: %s' % (index, e))
            break
    print('opened %d of %d folders' % (opened, len(groups)))

    # 2. look at pictures, at the sizes the pane really meets them
    shown = 0
    for index in range(min(args.folders, len(groups))):
        items = groups[index].get('items') or []
        for item in items[:2]:
            path = item if not isinstance(item, dict) else item.get('path')
            try:
                app.select_file(path)
                pump(root, 0.2)
                shown += 1
            except Exception as e:
                print('  could not show %s: %s' % (path, e))
                break
        if shown >= 6:
            break
    print('showed %d pictures' % shown)

    # 3. the zoom ladder, which is where a redraw cost hides
    for level in ('100%', '200%', '400%', 'Fit'):
        try:
            app._set_zoom(level)
            pump(root, 0.3)
        except Exception as e:
            print('  zoom %s: %s' % (level, e))
    print('walked the zoom ladder')

    # 4. a resize, which re-clamps every column
    for size in ('1200x800', '1800x1050', '1400x900'):
        root.geometry(size)
        pump(root, 0.4)
    print('resized three times')

    written = app.save_profile(args.out)
    print('profile: %s' % written)
    summary = zui.PROFILE.summary()
    print('\n%-22s %6s %8s %8s %8s %10s' % ('event', 'n', 'p50 ms', 'p90 ms', 'max ms', 'total ms'))
    for ev, s in sorted(summary.items(), key=lambda kv: -kv[1].get('total', 0)):
        print('%-22s %6d %8.1f %8.1f %8.1f %10.0f'
              % (ev, s.get('n', 0), s.get('p50', 0), s.get('p90', 0), s.get('max', 0), s.get('total', 0)))
    try:
        root.destroy()
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
