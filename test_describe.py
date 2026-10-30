"""test_describe.py - turning a foreign folder (pictures with no prompts) into groupable pictures.

Grouping runs on prompts. A folder made by something else has none, and Describe alone only fills the prompt box for the
picture in front of you, so such a folder stayed one unnamed block forever. `describe_missing` runs the local model over
every picture without a prompt and writes the result into its sidecar, marked `prompt_from: describe`.

    python test_describe.py
"""

import json
import queue
import sys
import tempfile
import time
import tkinter as tk
from argparse import Namespace
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zimage_ui as zui  # noqa: E402

FAILS = []


def check(label, ok, detail=''):
    print('%-66s %s %s' % (label, 'PASS' if ok else 'FAIL', detail))
    if not ok:
        FAILS.append(label)


class Stub:
    def __init__(self):
        self.events = queue.Queue()

    def generate(self, *a, **k):
        pass


def main():
    folder = Path(tempfile.mkdtemp(prefix='foreign-'))
    paths = []
    for i in range(3):                          # a foreign folder: pictures, no sidecars at all
        p = folder / ('shot%d.png' % i)
        Image.new('RGB', (96, 96), (50 * i, 80, 120)).save(p)
        paths.append(str(p))
    # one picture that is already labelled: it must not be touched
    labelled = folder / 'labelled.png'
    Image.new('RGB', (96, 96), (10, 200, 60)).save(labelled)
    labelled.with_suffix('.json').write_text(json.dumps({'prompt': 'my own words'}), encoding='utf-8')
    paths.append(str(labelled))

    root = tk.Tk()
    app = zui.App(root, Stub(), Namespace(port=8006, size=1024, steps=9, cfg=1.0, seed=1, out=str(folder),
                                          initial='', initial_file='', auto=False, no_progress=True))
    root.update()

    todo = app._uncaptioned(paths)
    check('a picture with no sidecar is uncaptioned', str(folder / 'shot0.png') in todo, todo)
    check('a picture with its own prompt is left out', str(labelled) not in todo, todo)

    app._local_llm_url = lambda silent=False: 'http://127.0.0.1:8080/v1'
    app.caption_picture = lambda path, url: 'a photo of %s' % Path(path).stem
    zui.messagebox.askyesno = lambda *a, **k: True
    app.describe_missing(paths)
    end = time.time() + 8
    while time.time() < end:
        app._poll()
        root.update()
        time.sleep(0.03)
        if not getattr(app, '_captions_running', False):
            break

    meta = json.loads((folder / 'shot0.json').read_text(encoding='utf-8'))
    check('the caption is saved as the prompt', meta.get('prompt') == 'a photo of shot0', meta.get('prompt'))
    check('the same text is kept as the caption', meta.get('caption') == 'a photo of shot0', meta.get('caption'))
    check('and it says where the prompt came from', meta.get('prompt_from') == 'describe', meta.get('prompt_from'))
    kept = json.loads(labelled.with_suffix('.json').read_text(encoding='utf-8'))
    check('a picture with a prompt is never overwritten', kept.get('prompt') == 'my own words', kept.get('prompt'))
    # every picture now has a prompt - the three described, and the one that already had its own
    check('nothing is left uncaptioned', app._uncaptioned(paths) == [], app._uncaptioned(paths))
    check('a resumed run finds nothing to do', app._uncaptioned([]) == [])

    # an empty gallery is not an error
    empty = Path(tempfile.mkdtemp(prefix='foreign-empty-'))
    check('an empty folder has nothing to caption', app._uncaptioned([str(p) for p in empty.glob('*.png')]) == [])
    try:
        root.destroy()
    except Exception:
        pass
    print('\nFAILURES:', FAILS if FAILS else 'none')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(main())
