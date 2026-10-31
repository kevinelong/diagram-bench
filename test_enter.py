"""test_enter.py - Enter means "the default action", the way it does in a Windows app.

`ttk::button` in Tk 8.6.12 has neither a `-default` option nor a <Return> binding - only <space>, via
`ttk::button::activate`. So pressing Enter after tabbing to a button did nothing at all, which is exactly the kind of gap a
non-engineer notices first. The window now answers Enter itself, and leaves Enter alone where a widget already uses it (the
prompt box generates on its own, the strip loads the picture under the cursor).

    python test_enter.py
"""

import json
import queue
import sys
import tempfile
import tkinter as tk
from argparse import Namespace
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zimage_ui as zui  # noqa: E402

FAILS = []


def check(label, ok, detail=''):
    print('%-64s %s %s' % (label, 'PASS' if ok else 'FAIL', detail))
    if not ok:
        FAILS.append(label)


class Stub:
    def __init__(self):
        self.events = queue.Queue()

    def generate(self, *a, **k):
        pass


def main():
    folder = Path(tempfile.mkdtemp(prefix='enter-'))
    for i in range(3):
        p = folder / ('e%d.png' % i)
        Image.new('RGB', (96, 96), (30 * i, 80, 120)).save(p)
        p.with_suffix('.json').write_text(json.dumps({'prompt': 'enter study'}), encoding='utf-8')
    root = tk.Tk()
    app = zui.App(root, Stub(), Namespace(port=8006, size=1024, steps=9, cfg=1.0, seed=1, out=str(folder),
                                          initial='', initial_file='', auto=False, no_progress=True))
    root.update()

    calls = []
    app.generate = lambda *a, **k: calls.append('generate')

    class Event:
        def __init__(self, widget):
            self.widget = widget

    plain = tk.Button(root, text='some button')
    plain.focus_set()
    root.update()
    calls.clear()
    app._on_default_enter(Event(plain))
    check('Enter with a button focused runs the default action', calls == ['generate'], calls)

    calls.clear()
    app._on_default_enter(Event(app.text))
    check('Enter in the prompt box is left to the box', calls == [], calls)

    calls.clear()
    app._on_default_enter(Event(app.gal_canvas))
    check('Enter on the strip is left to the strip', calls == [], calls)

    calls.clear()
    app._on_default_enter(Event(getattr(app, 'gen_btn')))
    check('Enter on any focused button still means the default, not that button',
          calls == ['generate'], calls)

    check('the handler returns break so the event stops', app._on_default_enter(Event(plain)) == 'break')
    check('the window really is bound to Return and keypad Enter',
          bool(root.bind('<Return>')) and bool(root.bind('<KP_Enter>')), (root.bind('<Return>'), root.bind('<KP_Enter>')))

    app._cancel_pending()
    try:
        root.destroy()
    except Exception:
        pass
    print('\nFAILURES:', FAILS if FAILS else 'none')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(main())
