#!/usr/bin/env python
"""Where gallery mode starts, and that it stays non-destructive.

    python test_review_modes.py

Builds its own window and gallery: the review window is modal-ish and zoomed, so it must not share state with another
suite's fixture.
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

os.environ.setdefault('ZIMAGE_SETTINGS_DIR', tempfile.mkdtemp(prefix='modes-set-'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pair_rank  # noqa: E402
import pair_review  # noqa: E402
import pair_review as _pr
import zimage_ui as zui  # noqa: E402

# The review saves its decisions under pair_review.STATE_DIR keyed by the member names, so a second run of this test would
# load the first run's decisions and fail "nothing was decided" - and inflate the tie counters. Its own state, every run.
pair_review.STATE_DIR = Path(tempfile.mkdtemp(prefix='modes-state-'))

FAILS = []


def check(label, ok, detail=''):
    print('%-62s %s %s' % (label, 'PASS' if ok else 'FAIL', detail))
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


def main():
    folder = Path(tempfile.mkdtemp(prefix='modes-'))
    for i in range(5):
        p = folder / ('m%d.png' % i)
        Image.new('RGB', (128, 128), (40 * i, 90, 180)).save(p)
        p.with_suffix('.json').write_text(json.dumps({'prompt': 'modes study', 'seed': i, 'steps': 4,
                                                      'cfg': 1.0, 'size': 512}), encoding='utf-8')
    root = tk.Tk()
    app = zui.App(root, Stub(), NS(port=8099, size=512, steps=4, cfg=1.0, seed=1, out=str(folder),
                                    initial='', initial_file='', auto=False, no_progress=True))
    root.update()

    print('\n-- Gallery… is an entry point of its own, not a radio inside the A/B window')
    row = [str(w.cget('text')) for w in app.contents_btns.winfo_children() if w.winfo_class() == 'TButton']
    check('the folder pane carries a Gallery… button', any(t.startswith('Gallery') for t in row), str(row))

    app.gallery_start()
    for _ in range(400):
        app._poll(); root.update(); time.sleep(0.02)
        if getattr(app, '_review', None):
            break
    r = getattr(app, '_review', None)
    check('it opens a window', bool(r))
    if not r:
        try:
            root.destroy()
        except Exception:
            pass
        print('\nFAILURES:', FAILS)
        return 1
    check('straight into gallery mode', app._review_mode.get() == 'gallery', app._review_mode.get())
    check('one picture, the pair half put away', r['right_lbl'].grid_info() == {}, str(r['right_lbl'].grid_info()))
    check('numbered from one of five', str(r['badge']['text']).startswith('1 / 5'), str(r['badge']['text']))
    check('the decision buttons are not offered',
          all(not b.winfo_ismapped() for b in r['buttons']), str([b.winfo_ismapped() for b in r['buttons']]))
    check('Prev and Next are', all(b.winfo_ismapped() for b in r['gallery_buttons'][:2]),
          str([b.winfo_ismapped() for b in r['gallery_buttons']]))

    print('\n-- walking it changes nothing but the position')
    app.gallery_step(+1); root.update()
    check('right moves forward', str(r['badge']['text']).startswith('2 / 5'), str(r['badge']['text']))
    app._review_arrow('down'); root.update()
    check('down moves forward too', str(r['badge']['text']).startswith('3 / 5'), str(r['badge']['text']))
    app._review_arrow('up'); root.update()
    check('up is back', str(r['badge']['text']).startswith('2 / 5'), str(r['badge']['text']))
    r['cur'] = 4; app.gallery_show(); root.update()
    app.gallery_step(+1); root.update()
    check('and the last one wraps to the first', str(r['badge']['text']).startswith('1 / 5'), str(r['badge']['text']))
    r['cur'] = 4; app.gallery_show(); root.update()
    check('FIN is on the last picture', 'FIN' in str(r['badge']['text']), str(r['badge']['text']))
    check('nothing was decided', not (r['state'].get('decisions') or {}), str(r['state'].get('decisions')))
    wins = [pair_rank.record_of(str(q))['wins'] for q in folder.glob('*.png')]
    check('nothing was ranked', all(w == 0 for w in wins), str(wins))

    app.review_stop()
    root.update()
    check('stopping closes it', app._review is None, str(app._review))

    print('\n-- and in rank mode Down is still a tie')
    app.review_start(paths=[str(p) for p in sorted(folder.glob('*.png'))], mode='rank')
    for _ in range(400):
        app._poll(); root.update(); time.sleep(0.02)
        if getattr(app, '_review', None):
            break
    r2 = getattr(app, '_review', None)
    check('the pair window opens', bool(r2) and r2.get('pair') is not None)
    if r2:
        import pair_review
        a, b = r2['pair']
        app._review_arrow('down'); root.update()
        chosen = (r2['state'].get('decisions') or {}).get(pair_review._pair_id(a, b), {}).get('choice')
        check('Down records a tie', chosen == 'tie', str(chosen))
        check('and both pictures count the tie',
              [pair_rank.record_of(p)['ties'] for p in (a, b)] == [1, 1],
              str([pair_rank.record_of(p)['ties'] for p in (a, b)]))
        app.review_stop(); root.update()
    try:
        root.destroy()
    except Exception:
        pass
    # Reported 2026-10-30: a ranking run ended with "N deleted", which a ranking can never do. The wording is one
    # function now, so both modes are checked directly.
    import re as _re
    import zimage_ui as _ui
    rank_done = _ui.review_tally(True, 24, 24, 0)
    check('a finished ranking claims no deletions', 'nothing deleted' in rank_done and not _re.search(r'\d+ deleted', rank_done),
          rank_done)
    rank_stop = _ui.review_tally(True, 9, 24, 0, stopped=True)
    check('and neither does a stopped one',
          'nothing deleted' in rank_stop and not _re.search(r'\d+ deleted', rank_stop), rank_stop)
    rank_pair = _ui.review_tally(True, 4, 24, 0, left=('round 2 of 5', '1 min'))
    check('nor the running tally', 'nothing deleted' in rank_pair and not _re.search(r'\d+ deleted', rank_pair), rank_pair)
    del_done = _ui.review_tally(False, 24, 24, 11)
    check('delete mode still reports its count', _re.search(r'11 deleted', del_done) is not None, del_done)
    check('and never says nothing was deleted', 'nothing deleted' not in del_done, del_done)

    print('\n-- a second folder whose pictures share the names does not inherit the session')
    import tempfile as _tf, pathlib as _pl
    root = _pl.Path(_tf.mkdtemp(prefix='rv-clash-'))
    for folder in ('one', 'two'):
        d = root / folder
        d.mkdir()
        for name in 'abcd':
            (d / ('%s.png' % name)).write_bytes(b'\x89PNG\r\n\x1a\n')
    one = [str(root / 'one' / ('%s.png' % n)) for n in 'abcd']
    two = [str(root / 'two' / ('%s.png' % n)) for n in 'abcd']
    check('the two folders really do collide on one key', _pr.state_key(one) == _pr.state_key(two), _pr.state_key(one))
    clash = _pr.load_state(_pr.state_key(one), root)
    _pr.build(one, clash, root)
    clash['decisions']['pair-1'] = {'choice': 'tie'}
    _pr.build(two, clash, root)
    check('the second folder gets its own paths', clash.get('paths') == two, str(clash.get('paths'))[:90])
    check('and none of the first folder\'s decisions', not clash.get('decisions'), str(clash.get('decisions'))[:90])
    check('and its own pairs', bool(clash.get('pairs')), type(clash.get('pairs')).__name__)

    print('\nFAILURES:', FAILS if FAILS else 'none')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(main())
