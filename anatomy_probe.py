"""anatomy_probe.py - does a model draw a human body that is physically possible?

The frozen diagram battery cannot see this: OCR and geometry measure boxes and labels, and a figure with three
arms scores perfectly on both. This probe asks the biological questions instead, on classical life-drawing poses,
because a figure study puts hands, feet, joints and proportions in view at once.

Scope, stated so the instrument cannot drift: these are **life-drawing studies** - standing, seated, reclining,
two figures side by side. No sexual or intimate framing is in the prompt set and none belongs in it; what is being
measured is anatomy, and a clinical pose exposes anatomy better than any other.

Two kinds of number, and they are never collapsed:
  * measured here, no judge: wall time, peak VRAM, edge energy (Laplacian variance) - the model's own behaviour;
  * judged by a vision model (`--judge cloud`, one yes/no per check, same rubric for every arm): limb counts,
    digit counts, fused or duplicated parts, head-to-body proportion, framing.

Usage (cmd.exe):
  .venv\\Scripts\\python.exe anatomy_probe.py --profile "flux2 klein 9b uncensored" --size 1024 --judge none
"""
import argparse
import json
import statistics
import subprocess

from winproc import popen, run
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import cv2

import zimage_gen as zg

ROOT = Path(__file__).resolve().parent
OUTPUT = Path(r'C:\Users\kevin\Documents\ComfyUI\output')
HOST = 'http://127.0.0.1:8006'

POSES = [
    {'id': 'standing', 'figures': 1,
     'prompt': 'charcoal life drawing of a standing female nude in contrapposto, full body from head to feet, '
               'three-quarter view, arms relaxed at her sides, plain grey studio background, single figure'},
    {'id': 'seated', 'figures': 1,
     'prompt': 'pencil life drawing of a seated male nude on a wooden stool, hands resting on his knees, full body '
               'from head to feet, side view, plain grey studio background, single figure'},
    {'id': 'reclining', 'figures': 1,
     'prompt': 'ink life drawing of a reclining nude figure on a chaise longue, one arm behind the head, legs '
               'extended, full body, plain grey studio background, single figure'},
    {'id': 'pair', 'figures': 2,
     'prompt': 'life drawing study of two nude figures side by side, one standing in profile and one kneeling, '
               'both complete from head to feet, plain grey studio background'},
]

# one question per line, answered yes/no; they are the anatomy a life-drawing tutor would check
CHECKS = [
    ('limbs', 'Does every figure have exactly two arms and two legs, with no extra, missing or fused limbs?'),
    ('hands', 'Does every visible hand have exactly five separate fingers, none merged into a paddle?'),
    ('feet', 'Does every visible foot have toes that read as feet rather than stumps or extra digits?'),
    ('joints', 'Do the elbows and knees bend the way human joints bend, with no backwards or broken joints?'),
    ('proportion', 'Is the head about one seventh to one eighth of the total body height?'),
    ('torso', 'Is there exactly one head and one torso per figure, with no duplicated or overlapping bodies?'),
    ('hands_in_frame', 'Are the hands fully inside the image, not cropped off at the edge?'),
    ('feet_in_frame', 'Are the feet fully inside the image, not cropped off at the edge?'),
]


def post(path, obj):
    req = urllib.request.Request(HOST + path, data=json.dumps(obj).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=60))


def get(path):
    return json.load(urllib.request.urlopen(HOST + path, timeout=30))


def vram():
    try:
        return int(run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader'],
                                  capture_output=True, text=True, timeout=10).stdout.strip().split()[0])
    except Exception:
        return None


def edge_energy(png):
    img = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
    return round(float(cv2.Laplacian(img, cv2.CV_64F).var()), 1) if img is not None else None


def run_one(profile, pose, size, seed, steps, cfg, outdir, judge):
    peak = {'v': 0}
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            v = vram()
            if v and v > peak['v']:
                peak['v'] = v
            stop.wait(0.25)

    t = threading.Thread(target=sampler, daemon=True)
    t.start()
    started = time.monotonic()
    ok, image, err = False, None, None
    try:
        wf = zg.workflow(pose['prompt'], size, size, steps, cfg, seed,
                         'anatomy-%s-%d' % (pose['id'], seed), profile=profile)
        pid = post('/prompt', {'prompt': wf})['prompt_id']
        while time.monotonic() - started < 1800:
            h = get('/history/' + pid)
            if pid in h:
                e = h[pid]
                images = [i for o in e.get('outputs', {}).values() for i in o.get('images', [])]
                ok = bool(images) and e.get('status', {}).get('status_str') == 'success'
                if images:
                    image = OUTPUT / images[0]['filename']
                if not ok:
                    err = str(e.get('status', {}).get('messages', ''))[:200]
                break
            time.sleep(1)
    except Exception as ex:
        err = type(ex).__name__ + ': ' + str(ex)
    finally:
        stop.set()
        t.join(timeout=2)
    wall = round(time.monotonic() - started, 1)

    row = {'id': pose['id'], 'figures': pose['figures'], 'seed': seed, 'size': size, 'ok': ok,
           'wall_s': wall, 'vram_peak_mib': peak['v'] or None, 'error': err}
    if ok and image and image.exists():
        dest = outdir / ('%s-s%d.png' % (pose['id'], seed))
        dest.write_bytes(image.read_bytes())
        row['file'] = dest.name
        row['edge_energy'] = edge_energy(dest)
        if judge == 'cloud':
            import vlm_score
            b64 = __import__('base64').b64encode(dest.read_bytes()).decode()
            verdicts = {}
            for key, question in CHECKS:
                try:
                    verdicts[key] = 'yes' in str(vlm_score.ask(b64, question)).lower()
                except Exception as ex:
                    verdicts[key] = None
                    row.setdefault('judge_errors', []).append('%s: %s' % (key, type(ex).__name__))
            row['checks'] = verdicts
            answered = [v for v in verdicts.values() if v is not None]
            row['passed'] = sum(1 for v in answered if v)
            row['answered'] = len(answered)
    return row


def main():
    global HOST
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--profile', default='flux2 klein 9b uncensored', choices=sorted(zg.MODELS))
    ap.add_argument('--size', type=int, default=1024)
    ap.add_argument('--seeds', default='1234')
    ap.add_argument('--steps', type=int, default=None, help='default: the profile\'s own step count')
    ap.add_argument('--cfg', type=float, default=None, help='default: the profile\'s own cfg')
    ap.add_argument('--only', default=None, help='one pose id from the frozen four')
    ap.add_argument('--judge', choices=['none', 'cloud'], default='none',
                    help="'cloud' sends each render to the DeepSeek vision API for the anatomy rubric; "
                         "'none' records only the measured numbers and leaves the images unjudged")
    ap.add_argument('--host', default=HOST)
    ap.add_argument('--out-name', default=None)
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()

    HOST = args.host.rstrip('/')
    prof = zg.MODELS[args.profile]
    steps = args.steps if args.steps is not None else prof['steps']
    cfg = args.cfg if args.cfg is not None else prof['cfg']
    seeds = [int(s) for s in args.seeds.split(',')]
    poses = [p for p in POSES if not args.only or p['id'] == args.only]
    if not poses:
        print('no such pose: %s (choose %s)' % (args.only, ', '.join(p['id'] for p in POSES)))
        return 2

    outdir = ROOT / 'render' / ('anatomy-%s-%d' % (args.profile.replace(' ', '_'), args.size))
    outdir.mkdir(parents=True, exist_ok=True)
    out = ROOT / (args.out_name or 'result-anatomy-%s-%d.json'
                  % (args.profile.replace(' ', '_'), args.size))
    if out.exists() and not args.force:
        print('%s exists. Name this one with --out-name (e.g. %s) or pass --force.'
              % (out.name, out.with_name(out.stem + '-%s.json' % datetime.now().strftime('%Y%m%d')).name))
        return 2

    idle = vram()
    print('profile %s  size %d  steps %d  cfg %s  judge %s  idle VRAM %s MiB'
          % (args.profile, args.size, steps, cfg, args.judge, idle))
    rows = []
    for pose in poses:
        for seed in seeds:
            row = run_one(args.profile, pose, args.size, seed, steps, cfg, outdir, args.judge)
            rows.append(row)
            judged = (' checks %d/%d' % (row['passed'], row['answered'])) if 'passed' in row else ''
            print('  %-10s s%-5d %-4s %5.1fs vram %-5s edge %-7s%s'
                  % (pose['id'], seed, 'OK' if row['ok'] else 'FAIL', row['wall_s'],
                     row['vram_peak_mib'], row['edge_energy'], judged), flush=True)

    ok = [r for r in rows if r['ok']]
    summary = {'profile': args.profile, 'size': args.size, 'steps': steps, 'cfg': cfg, 'judge': args.judge,
               'idle_vram_mib': idle, 'n': len(rows), 'ok': len(ok),
               'wall_median_s': statistics.median([r['wall_s'] for r in ok]) if ok else None,
               'peak_mib': max((r['vram_peak_mib'] or 0) for r in rows),
               'edge_energy_median': statistics.median([r['edge_energy'] for r in ok
                                                        if r.get('edge_energy')]) if ok else None}
    if any('passed' in r for r in rows):
        summary['checks_passed'] = sum(r.get('passed', 0) for r in rows)
        summary['checks_answered'] = sum(r.get('answered', 0) for r in rows)
    out.write_text(json.dumps({'summary': summary, 'rows': rows}, indent=1), encoding='utf-8')
    print('\n%d/%d ok; median %ss; peak %s MiB; -> %s'
          % (len(ok), len(rows), summary['wall_median_s'], summary['peak_mib'], out.name))
    return 0 if len(ok) == len(rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
