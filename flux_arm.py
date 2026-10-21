"""flux_arm.py - run a frozen-arm diffusion sample set through ComfyUI and measure it properly.

Records, per image: wall time, peak VRAM (sampled at 250 ms), the workflow parameters, and the output path.
Runs a determinism check (same prompt + seed twice, PNG sha256 compared) and never retries a failed job
silently - a failure is recorded.

Usage (cmd.exe):
  .venv\\Scripts\\python.exe flux_arm.py --steps 4 --cfg 1.0 --seeds 1234,5678 --check-determinism
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = os.environ.get('COMFY_HOST', 'http://127.0.0.1:8188')  # desktop app serves on 8000
OUTPUT = Path(r'C:\Users\kevin\Documents\ComfyUI\output')
RENDER = ROOT / 'render' / 'flux'


def post(path, obj):
    req = urllib.request.Request(HOST + path, data=json.dumps(obj).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=60))


def get(path):
    return json.load(urllib.request.urlopen(HOST + path, timeout=30))


def vram():
    try:
        return int(subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader'],
                                  capture_output=True, text=True, timeout=10).stdout.strip().split()[0])
    except Exception:
        return None


def workflow(prompt, steps, cfg, seed, prefix, size=1024):
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "flux1-schnell-Q4_K_S.gguf"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "t5xxl_fp8_e4m3fn.safetensors",
                                                        "clip_name2": "clip_l.safetensors", "type": "flux"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux1-ae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
        "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": size, "height": size, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0],
                                                  "latent_image": ["6", 0], "seed": seed, "steps": steps, "cfg": cfg,
                                                  "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
    }


def run_one(prompt, steps, cfg, seed, prefix, timeout=600, size=1024):
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
    result = None
    try:
        r = post('/prompt', {"prompt": workflow(prompt, steps, cfg, seed, prefix, size)})
        pid = r['prompt_id']
        while True:
            h = get('/history/' + pid)
            if pid in h:
                entry = h[pid]
                images = [i for out in entry.get('outputs', {}).values() for i in out.get('images', [])]
                result = {'ok': bool(images), 'images': images,
                          'error': None if images else 'history reported success but carried no images',
                          'wall_s': round(time.monotonic() - started, 1),
                          'status': entry.get('status', {}).get('status_str')}
                return result
            time.sleep(1.0)
            if time.monotonic() - started > timeout:
                result = {'ok': False, 'error': 'timeout', 'wall_s': round(time.monotonic() - started, 1)}
                return result
    except Exception as e:
        result = {'ok': False, 'error': type(e).__name__ + ': ' + str(e),
                  'wall_s': round(time.monotonic() - started, 1)}
        return result
    finally:
        stop.set()
        t.join(timeout=2)
        if result is not None:
            result['peak'] = peak['v'] or None


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--steps', type=int, default=4)
    ap.add_argument('--cfg', type=float, default=1.0)
    ap.add_argument('--seeds', default='1234,5678')
    ap.add_argument('--only', default=None)
    ap.add_argument('--check-determinism', action='store_true')
    ap.add_argument('--prompts', default='prompts-diagrams.json',
                    help='corpus file: the frozen corpus or prompts-diagrams-grounded.json')
    ap.add_argument('--tag', default='flux-arm', help='output filename prefix')
    ap.add_argument('--size', type=int, default=1024, help='square canvas size for EmptySD3LatentImage')
    args = ap.parse_args()

    corpus = json.loads((ROOT / args.prompts).read_text(encoding='utf-8'))
    prompts = [p for p in corpus['prompts'] if not args.only or p['id'] == args.only]
    # a derived corpus (e.g. the grounded revision) may omit params: take the frozen ones, so
    # the style suffix and seeds are identical and the only difference is the prompt text.
    params = corpus.get('params') or json.loads(
        (ROOT / 'prompts-diagrams.json').read_text(encoding='utf-8'))['params']
    suffix = params['style_suffix']
    seeds = [int(s) for s in args.seeds.split(',')]
    RENDER.mkdir(parents=True, exist_ok=True)
    idle = vram()

    rows = []
    for p in prompts:
        for seed in seeds:
            result = run_one(p['prompt'] + suffix, args.steps, args.cfg, seed, args.tag, size=args.size)
            row = {'id': p['id'], 'kind': p.get('kind'), 'seed': seed, 'steps': args.steps, 'cfg': args.cfg,
                   'ok': result['ok'], 'wall_s': result['wall_s'], 'vram_peak_mib': result.get('peak'),
                   'status': result.get('status'), 'error': result.get('error')}
            if result['ok']:
                for img in result['images']:
                    src = OUTPUT / img.get('subfolder', '') / img['filename']
                    if src.exists():
                        dest = RENDER / ('%s-%s-s%d.png' % (args.tag, p['id'], seed))
                        shutil.copy2(src, dest)
                        row['file'] = dest.name
                        row['sha256'] = sha(dest)
                        break
            rows.append(row)
            print('  %-13s seed %-5d %-5s %5.1fs vram_peak=%s MiB'
                  % (p['id'], seed, 'OK' if row['ok'] else 'FAIL', row['wall_s'], row.get('vram_peak_mib')),
                  flush=True)

    determinism = None
    if args.check_determinism:
        p = prompts[0]
        a = run_one(p['prompt'] + suffix, args.steps, args.cfg, seeds[0], 'flux-det-a')
        b = run_one(p['prompt'] + suffix, args.steps, args.cfg, seeds[0], 'flux-det-b')
        pa = OUTPUT / (a['images'][0]['filename'] if a['ok'] else '')
        pb = OUTPUT / (b['images'][0]['filename'] if b['ok'] else '')
        determinism = {'prompt': p['id'], 'seed': seeds[0],
                       'identical': bool(a['ok'] and b['ok'] and sha(pa) == sha(pb)),
                       'sha_a': sha(pa) if a['ok'] else None, 'sha_b': sha(pb) if b['ok'] else None}
        print('  determinism:', determinism['identical'])

    payload = {'arm': 'A-flux1-schnell-Q4_K_S', 'steps': args.steps, 'cfg': args.cfg, 'seeds': seeds,
               'sampler': 'euler/simple', 'resolution': 1024, 'idle_vram_mib': idle,
               'determinism': determinism, 'rows': rows}
    # the output name follows the tag: a derived run must never overwrite the arm's own artifact
    out = ROOT / ('result-arm-flux.json' if args.tag == 'flux-arm'
                  else 'result-arm-%s.json' % args.tag)
    out.write_text(json.dumps(payload, indent=1), encoding='utf-8')
    ok = sum(1 for r in rows if r['ok'])
    walls = [r['wall_s'] for r in rows if r['ok']]
    print('\n%d/%d generated; median wall %.1fs; peak VRAM %s MiB; wrote %s'
          % (ok, len(rows), sorted(walls)[len(walls) // 2] if walls else -1,
             max((r.get('vram_peak_mib') or 0) for r in rows), out.name))


if __name__ == '__main__':
    sys.exit(main())
