#!/usr/bin/env python
"""Tests for the folder-captioning path: no prompt -> caption -> topic folders.

    python test_caption_folder.py

Self-contained on purpose: it builds its own gallery and its own window, so it cannot be affected by - or damage - the
shared fixture the gallery suite uses. The vision model is stubbed; nothing here touches the network.
"""

import json
import os
import queue
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace as NS

from PIL import Image

os.environ.setdefault('ZIMAGE_SETTINGS_DIR', tempfile.mkdtemp(prefix='capfolder-set-'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import caption as capmod  # noqa: E402
import zimage_ui as zui  # noqa: E402

FAILS = []


def check(label, ok, detail=''):
    print('%-64s %s %s' % (label, 'PASS' if ok else 'FAIL', detail))
    if not ok:
        FAILS.append(label)


class Stub:
    def __init__(self):
        self.events = queue.Queue()
        self.events.put(('ready', None))

    def generate(self, *a, **k):
        return True

    def cancel(self):
        return 'x'

    def shutdown(self):
        return 'x'


def build_gallery(n=21):
    """Pictures with no prompt at all, which is what a folder from another pipeline looks like."""
    folder = Path(tempfile.mkdtemp(prefix='capfolder-'))
    base = 1_700_000_000
    for i in range(n):
        p = folder / ('shot%02d.png' % i)
        Image.new('RGB', (64, 64), (90, 60 + i, 200 - i)).save(p)
        os.utime(p, (base + i, base + i))      # the newest `RECENT_N` cannot be grouped: order matters
    return folder, base


def pump(app, root, seconds=8.0, until=None):
    end = time.time() + seconds
    while time.time() < end:
        app._poll()
        root.update()
        time.sleep(0.02)
        if until and until():
            return True
    return False


def main():
    folder, _base = build_gallery()
    root = tk.Tk()
    app = zui.App(root, Stub(), NS(port=8099, size=512, steps=4, cfg=1.0, seed=1, out=str(folder),
                                    initial='', initial_file='', auto=False, no_progress=True))
    root.update()
    pump(app, root, 6, until=lambda: app._plan is not None)
    before = [g for g in ((app._plan or {}).get('groups') or []) if not g.get('synthetic')]
    check('a folder of prompt-less pictures has no real group yet', not before,
          str([g.get('label') for g in (app._plan or {}).get('groups') or []]))

    print('\n-- with no vision model, it refuses and sends nothing')
    real_available = capmod.available
    capmod.available = lambda timeout=2.0: False
    try:
        sent = app.caption_folder()
    finally:
        capmod.available = real_available
    check('it sends nothing without a model', sent == 0, str(sent))
    check('and says where the model should be', 'no vision model' in app.hint['text'], app.hint['text'][:70])
    check('and writes no sidecar', not list(folder.glob('*.json')), str(list(folder.glob('*.json'))))

    print('\n-- with a model, every prompt-less picture is captioned and filed by topic')
    calls = []

    def fake_caption(path, timeout=90):
        calls.append(Path(path).name)
        m = ''.join(ch for ch in Path(path).stem if ch.isdigit())
        idx = int(m) if m else 0
        topic = 'driftwood study' if idx < 6 else ['lighthouse', 'harbour wall', 'ferry'][idx % 3]
        return topic, 'A picture of %s.' % topic

    real_caption = capmod.caption
    capmod.available = lambda timeout=2.0: True
    capmod.caption = fake_caption
    from tkinter import messagebox as mb
    real_ask = mb.askyesno
    mb.askyesno = lambda *a, **k: True
    try:
        sent = app.caption_folder()
        pump(app, root, 12, until=lambda: len(list(folder.glob('*.json'))) >= len(app._files))
    finally:
        capmod.available, capmod.caption, mb.askyesno = real_available, real_caption, real_ask
    check('every picture without a prompt is sent', sent == len(app._files), '%d of %d' % (sent, len(app._files)))
    side = next(iter(sorted(folder.glob('*.json'))), None)
    meta = json.loads(side.read_text(encoding='utf-8')) if side else {}
    check('the caption is written beside the picture', bool(meta.get('caption')), str(meta.get('caption'))[:36])
    check('the topic is written too', bool(meta.get('topic')), str(meta.get('topic')))
    check('it is marked as imported, never as a prompt', meta.get('imported') == 'caption' and not meta.get('prompt'),
          '%s / prompt=%r' % (meta.get('imported'), meta.get('prompt')))

    app._grouping = False
    app._plan = None                      # the pre-caption plan held only the "unfiled" bucket
    app._regroup_soon = zui.App._regroup_soon.__get__(app, zui.App)
    app.group_var.set(True)
    app._regroup_now()
    pump(app, root, 20, until=lambda: any((g.get('label') or '').startswith('caption: ')
                                          for g in ((app._plan or {}).get('groups') or [])))
    labels = [g.get('label') or '' for g in ((app._plan or {}).get('groups') or [])]
    check('the pictures are filed by caption', any(lab.startswith('caption: ') for lab in labels), str(labels)[:110])
    check('and the newest ones stay in the recency window, as anywhere else',
          len((app._plan or {}).get('recent') or []) >= 1, str(len((app._plan or {}).get('recent') or [])))

    try:
        root.destroy()
    except Exception:
        pass
    print('\nFAILURES:', FAILS if FAILS else 'none')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(main())
