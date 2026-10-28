#!/usr/bin/env python
"""A controlled sweep: one base, one prompt, one seed, only the denoise varies.

    python sweep_vary.py --base C:/.../20261028-xxxx_1024x1024_seedN.png \
                         --out C:/Users/kevin/AppData/Local/Temp/vary-sweep \
                         --strengths 0.4,0.5,0.6,0.7,0.8,0.9,0.95

`vary_curve.py` measures the population - many bases, prompts and models mixed in each bin. This measures the thing
itself: with everything else held still, how much does the picture move at each strength? Output goes to a temp directory,
never to the gallery, and the renders are one at a time.
"""

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vary_curve  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--prompt', default='')
    ap.add_argument('--out', default='')
    ap.add_argument('--port', type=int, default=8006)
    ap.add_argument('--steps', type=int, default=9, help='the turbo ceiling; more buys nothing')
    ap.add_argument('--size', type=int, default=1024)
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('--strengths', default='0.4,0.5,0.6,0.7,0.8,0.9,0.95')
    a = ap.parse_args()

    base = Path(a.base)
    sidecar = base.with_suffix('.json')
    meta = json.loads(sidecar.read_text(encoding='utf-8')) if sidecar.is_file() else {}
    prompt = a.prompt or (meta.get('prompt') or '')
    if not prompt.strip():
        raise SystemExit('no prompt: pass --prompt or use a base that has one in its sidecar')
    seed = a.seed if a.seed is not None else int(meta.get('seed') or 1234)
    out = Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix='vary-sweep-'))
    out.mkdir(parents=True, exist_ok=True)

    import zimage_ui
    engine = zimage_ui.Engine(port=a.port, progress=False)
    if engine._answers():
        print('using the server already on port %d' % a.port)
    else:
        # start() is thread-based and reports through the event queue, so wait on the event rather than a sleep
        import time
        engine.start()
        deadline = time.time() + 240
        while time.time() < deadline:
            try:
                kind, payload = engine.events.get(timeout=2)
            except Exception:
                continue
            print('  %s: %s' % (kind, payload))
            if kind == 'ready':
                break
            if kind == 'error':
                raise SystemExit('server did not come up: %s' % payload)
    print('engine ready; base %s\nprompt %r\nseed %d, %d steps, %d px, out %s' % (
        base.name, prompt[:80], seed, a.steps, a.size, out))

    rows = []
    import time
    for text in a.strengths.split(','):
        strength = float(text.strip())
        print('\n-- denoise %.2f' % strength)
        # generate() is fire-and-forget: the result arrives as ('image', (bytes, path)) on the event queue, which is how
        # the window itself consumes it. Drain anything stale first so one render cannot be mistaken for the next.
        while not engine.events.empty():
            try:
                engine.events.get_nowait()
            except Exception:
                break
        engine.generate(prompt, a.size, a.size, a.steps, 1.0, seed, str(out),
                        base_path=str(base), denoise=strength)
        child = None
        deadline = time.time() + 600
        while time.time() < deadline:
            try:
                kind, payload = engine.events.get(timeout=5)
            except Exception:
                continue
            if kind == 'image':
                child = Path(payload[1])
                break
            if kind in ('error', 'cancelled'):
                print('   %s: %s' % (kind, payload))
                break
        if child is None:
            print('   no image for denoise %.2f' % strength)
            continue
        d = vary_curve.distance_pair(child, base)
        # identity is a *pixel* hash: ComfyUI embeds the workflow in the PNG, so two renders of the same picture have
        # different file bytes (measured: 0.84 and 0.90 differed by one byte and by zero pixels)
        from PIL import Image as _Image
        import numpy as _np
        with _Image.open(child) as im:
            d['pixels'] = hashlib.sha256(_np.asarray(im.convert('RGB')).tobytes()).hexdigest()
        rows.append((strength, d, child.name))
        print('   %s -> grey %.4f blocks %.4f colour %.4f dhash %.4f' % (
            child.name, d['grey'], d['blocks'], d['colour'], d['dhash']))

    if not rows:
        raise SystemExit('nothing measured')
    print('\n| denoise | grey | blocks | colour | dhash | file |')
    print('|---|---|---|---|---|---|')
    for strength, d, name in rows:
        print('| %.2f | %.4f | %.4f | %.4f | %.4f | %s |' % (
            strength, d['grey'], d['blocks'], d['colour'], d['dhash'], name))
    groups = {}
    for strength, d, _n in rows:
        groups.setdefault(d['pixels'], []).append(strength)
    repeats = [v for v in groups.values() if len(v) > 1]
    if repeats:
        print('\n**Identical pictures from different strengths:** ' + '; '.join(
            ', '.join('%.2f' % s for s in v) for v in repeats))
    (out / 'sweep.json').write_text(json.dumps(
        [{'denoise': s, 'file': n, **d} for s, d, n in rows], indent=1), encoding='utf-8')
    print('\nsaved %s' % (out / 'sweep.json'))
    try:
        engine.shutdown()
    except Exception:
        pass


if __name__ == '__main__':
    main()
