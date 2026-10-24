"""pose_probe.py - which model and denoise best follows a scene/pose instruction without losing the character?

The question this answers: "take this standing figure and make them sit" - which model keeps the face and clothing while
actually changing the pose? Two things have to be true at once, and img2img trades them against each other: too little
denoise keeps the character and leaves the pose alone, too much changes the pose and invents a new person.

Method

  1. one standing figure is generated and used as the base for every trial,
  2. each trial is img2img from it with the same instruction, varying model and denoise,
  3. two measurements per trial: pixel distance from the base (how much changed), and a vision judge answering two
     binary questions - same character? now sitting? - with the base and the result in the same message.

A trial "works" only when the judge answers yes to both. Judged by a cloud model (DEEPSEEK_API_KEY, deepseek-flash),
the same judge for every arm, so its bias cancels between arms.

    python pose_probe.py                       # full sweep, writes pose-probe/result.json + sheet.jpg
    python pose_probe.py --denoise 0.4 0.7     # narrower sweep
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zimage_gen as zg  # noqa: E402
import zimage_ui as zui  # noqa: E402

API = 'https://api.deepseek.com/v1/chat/completions'
JUDGE_MODEL = 'deepseek-flash'
KEY = os.environ.get('DEEPSEEK_API_KEY')

CHARACTER = ('full body studio photograph of a young woman with short red hair and freckles, wearing a green wool '
             'coat and black boots, standing upright facing the camera, plain white background')
INSTRUCTION = ('the same young woman with short red hair and freckles, wearing the same green wool coat and black boots, '
               'now sitting on a wooden chair, legs bent, same face, plain white background')

JUDGE = ('Two images, 1 then 2. Answer JSON only, no other text: '
         '{"same_character": "yes" or "no", "sitting": "yes" or "no"}. '
         'same_character: image 2 still shows the same person (face, hair, clothing). '
         'sitting: the figure in image 2 is seated, not standing.')


def as_bool(verdict, keys):
    """Accept whatever shape the judge returned - true, "yes", "y" - for any of these keys."""
    for key in keys:
        if key not in verdict:
            continue
        value = verdict[key]
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        return text.startswith('y') or text in ('true', '1', 'same', 'yes')
    return False


def b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode('ascii')


def judge(base_path, edit_path):
    content = [{'type': 'text', 'text': JUDGE + '\n\nFIRST image (original):'},
               {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64(base_path)}},
               {'type': 'text', 'text': 'SECOND image (edit):'},
               {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64(edit_path)}}]
    last = ''
    # the judge spends tokens on reasoning before it answers, so the budget escalates until text comes back
    for budget in (1500, 3000, 6000):
        body = {'model': JUDGE_MODEL, 'temperature': 0, 'max_tokens': budget,
                'messages': [{'role': 'user', 'content': content}]}
        req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json',
                                              'Authorization': 'Bearer ' + KEY})
        try:
            raw = json.loads(urllib.request.urlopen(req, timeout=120).read())
            text = raw['choices'][0]['message']['content'] or ''
            start, end = text.find('{'), text.rfind('}')
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1])
            last = 'empty content (finish=%s)' % raw['choices'][0].get('finish_reason')
        except Exception as e:
            last = '%s: %s' % (type(e).__name__, str(e)[:60])
        if 'finish=length' not in last:
            time.sleep(1.0)
    return {'error': last}


def diff(a, b):
    return round(sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3, 2)


def run(port, prompt, out_dir, base_path, denoise, profile, steps, cfg, hires=0.0):
    engine = zui.Engine(port, autostart=False, progress=False)
    engine.ready = True
    t0 = time.time()
    engine.generate(prompt, 512, 512, steps, cfg, 4242, str(out_dir), base_path=base_path,
                    denoise=denoise, batch=1, sequential=True, profile=profile)
    while time.time() - t0 < 600:
        try:
            kind, payload = engine.events.get(timeout=1)
        except Exception:
            continue
        if kind == 'image':
            return Path(payload[1]), round(time.time() - t0, 1)
        if kind == 'error':
            raise SystemExit('%s @ %.2f failed: %s' % (profile, denoise, str(payload)[:200]))
    raise SystemExit('%s @ %.2f timed out' % (profile, denoise))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profiles', nargs='*', default=list(zg.MODELS))
    ap.add_argument('--denoise', nargs='*', type=float, default=[0.4, 0.55, 0.7, 0.85])
    ap.add_argument('--port', type=int, default=8006)
    ap.add_argument('--out', default='pose-probe')
    ap.add_argument('--seed', type=int, default=4242)
    args = ap.parse_args()

    if not KEY:
        raise SystemExit('DEEPSEEK_API_KEY is not set, so nothing can be judged')

    out = Path(args.out)
    base_dir = out / 'base'
    base_dir.mkdir(parents=True, exist_ok=True)
    base_path = next(iter(sorted(base_dir.glob('*.png'))), None)

    print('starting server...', flush=True)
    zg.start_server(args.port, timeout=300)
    rows = []
    try:
        if base_path is None:
            print('generating the standing character...', flush=True)
            base_path, secs = run(args.port, CHARACTER, base_dir, None, 1.0, 'z-image turbo', 4, 1.0)
            print('  base %s (%.1f s)' % (base_path.name, secs), flush=True)
        base_img = Image.open(base_path).convert('RGB')

        for profile in args.profiles:
            steps = zg.MODELS[profile]['steps']
            cfg = zg.MODELS[profile]['cfg']
            for d in args.denoise:
                cell = out / ('%s_d%.2f' % (profile.replace(' ', '-'), d))
                cell.mkdir(parents=True, exist_ok=True)
                for f in cell.glob('*'):
                    f.unlink()
                edit, secs = run(args.port, INSTRUCTION, cell, base_path, d, profile, steps, cfg)
                verdict = judge(base_path, edit)
                delta = diff(base_img, Image.open(edit).convert('RGB'))
                same = as_bool(verdict, ('same_character', 'same_person', 'same', 'identical'))
                posed = as_bool(verdict, ('sitting', 'seated', 'pose_changed'))
                rows.append({'profile': profile, 'denoise': d, 'seconds': secs, 'pixel_delta': delta,
                             'same_character': same, 'sitting': posed, 'works': same and posed,
                             'raw': verdict, 'image': str(edit)})
                print('%-14s d=%.2f  %5.1fs  delta %6.2f  same %-3s  sitting %-3s  %s  %s'
                      % (profile, d, secs, delta, 'yes' if same else 'no', 'yes' if posed else 'no',
                         'WORKS' if same and posed else '     ', str(verdict.get('error', ''))[:40]), flush=True)
    finally:
        print('server:', zg.stop_server(args.port), flush=True)

    (out / 'result.json').write_text(json.dumps({'character': CHARACTER, 'instruction': INSTRUCTION,
                                                 'seed': args.seed, 'judge': JUDGE_MODEL,
                                                 'created': datetime.now().isoformat(timespec='seconds'),
                                                 'rows': rows}, indent=1), encoding='utf-8')

    # contact sheet: one row per model, one column per denoise, base first
    if rows:
        cols = len(args.denoise) + 1
        profiles = [p for p in args.profiles if any(r['profile'] == p for r in rows)]
        sheet = Image.new('RGB', (512 * cols, 512 * len(profiles)), 'white')
        for r_i, profile in enumerate(profiles):
            sheet.paste(base_img, (0, r_i * 512))
            for c_i, d in enumerate(args.denoise):
                match = [r for r in rows if r['profile'] == profile and r['denoise'] == d]
                if match:
                    sheet.paste(Image.open(match[0]['image']).convert('RGB'), ((c_i + 1) * 512, r_i * 512))
        sheet.resize((1024, int(1024 * sheet.height / sheet.width)), Image.LANCZOS).save(
            out / 'sheet.jpg', quality=88)
        print('\nsheet:', out / 'sheet.jpg')

    print('\n%-14s %s' % ('model', ' '.join('d=%.2f' % d for d in args.denoise)))
    for profile in args.profiles:
        cells = []
        for d in args.denoise:
            match = [r for r in rows if r['profile'] == profile and r['denoise'] == d]
            if not match:
                cells.append('  -  ')
                continue
            r = match[0]
            cells.append('%s%s ' % ('S' if r['same_character'] else '.', 'P' if r['sitting'] else '.'))
        wins = sum(1 for r in rows if r['profile'] == profile and r['works'])
        print('%-14s %s  works %d/%d' % (profile, ' '.join(cells), wins, sum(1 for r in rows if r['profile'] == profile)))
    print('\nlegend: S = judge says same character, P = judge says sitting; "SP" is the only success')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
