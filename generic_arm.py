"""generic_arm.py - run any of the four arms at a chosen resolution, with one code path.

The 1024 corpus is the default; `--size 512` produces the declared variant where FLUX.1-dev actually works.
Records wall time, peak VRAM (250 ms sampling), and the output path per image.

Usage (cmd.exe):
  .venv\\Scripts\\python.exe generic_arm.py --profile dev --size 512 --seeds 1234
"""
import argparse
import json
import shutil
import subprocess

from winproc import popen, run
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = 'http://127.0.0.1:8188'
OUTPUT = Path(r'C:\Users\kevin\Documents\ComfyUI\output')

PROFILES = {
    'klein': dict(steps=4, cfg=1.0, guidance=None, prefix='g-klein',
                  nodes={
                      "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "flux-2-klein-4b-Q4_K_M.gguf"}},
                      "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b_fp4_flux2.safetensors",
                                                                   "type": "flux2"}},
                      "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
                      "clip": ["2", 0], "vae": ["3", 0], "model": ["1", 0]}),
    'zimage': dict(steps=9, cfg=1.0, guidance=None, prefix='g-zimage',
                   nodes={
                       "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "z_image_turbo_nvfp4.safetensors",
                                                                    "weight_dtype": "default"}},
                       "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b_fp4_mixed.safetensors",
                                                                    "type": "lumina2"}},
                       "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
                       "clip": ["2", 0], "vae": ["3", 0], "model": ["1", 0]}),
    # same diffusion weights as 'klein', encoder swapped for the uncensored Qwen3-4B GGUF (2.33 GiB)
    'klein_unc': dict(steps=4, cfg=1.0, guidance=None, prefix='g-klein-unc',
                      nodes={
                          "1": {"class_type": "UnetLoaderGGUF",
                                "inputs": {"unet_name": "flux-2-klein-4b-Q4_K_M.gguf"}},
                          "2": {"class_type": "CLIPLoaderGGUF",
                                "inputs": {"clip_name": "flux2-klein-4b-uncensored-q4_k_m.gguf", "type": "flux2"}},
                          "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
                          "clip": ["2", 0], "vae": ["3", 0], "model": ["1", 0]}),
    # the 9B diffusion, also with an uncensored encoder (Qwen3-8B, Q5_K_M)
    'klein9_unc': dict(steps=4, cfg=1.0, guidance=None, prefix='g-klein9-unc',
                       nodes={
                           "1": {"class_type": "UnetLoaderGGUF",
                                 "inputs": {"unet_name": "flux-2-klein-9b-Q4_K_M.gguf"}},
                           "2": {"class_type": "CLIPLoaderGGUF",
                                 "inputs": {"clip_name": "flux2-klein-9b-uncensored-te-q5_k_m.gguf", "type": "flux2"}},
                           "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
                           "clip": ["2", 0], "vae": ["3", 0], "model": ["1", 0]}),
}


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


def build(profile, prompt, size, seed, cfg=None, sampler=None, scheduler=None, negative='', steps=None):
    p = PROFILES[profile]
    wf = dict(p['nodes'])
    wf = {k: (v if isinstance(v, dict) else v) for k, v in wf.items() if isinstance(v, dict)}
    wf["4"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": PROFILES[profile]['nodes']['clip']}}
    wf["5"] = {"class_type": "CLIPTextEncode", "inputs": {"text": negative or "",
                                                          "clip": PROFILES[profile]['nodes']['clip']}}
    if p['guidance'] is not None:
        wf["4g"] = {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": p['guidance']}}
        positive = ["4g", 0]
    else:
        positive = ["4", 0]
    wf["6"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": size, "height": size, "batch_size": 1}}
    wf["7"] = {"class_type": "KSampler", "inputs": {"model": PROFILES[profile]['nodes']['model'],
                                                    "positive": positive, "negative": ["5", 0],
                                                    "latent_image": ["6", 0], "seed": seed,
                                                    "steps": p['steps'] if steps is None else steps,
                                                    "cfg": p['cfg'] if cfg is None else cfg,
                                                    "sampler_name": sampler or "euler",
                                                    "scheduler": scheduler or "simple", "denoise": 1.0}}
    wf["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": PROFILES[profile]['nodes']['vae']}}
    wf["9"] = {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": p['prefix']}}
    return wf


def main():
    global HOST
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', required=True, choices=sorted(PROFILES))
    ap.add_argument('--size', type=int, default=1024)
    ap.add_argument('--seeds', default='1234')
    ap.add_argument('--only', default=None)
    ap.add_argument('--host', default=HOST,
                    help='ComfyUI base URL (default %s); the window may be serving on another port' % HOST)
    ap.add_argument('--steps', type=int, default=None, help="override the profile's step count")
    ap.add_argument('--cfg', type=float, default=None, help="override the profile's cfg")
    ap.add_argument('--sampler', default=None, help="override the sampler (euler, uni_pc, dpmpp_2m, res_multistep)")
    ap.add_argument('--scheduler', default=None, help="override the scheduler (simple, karras, normal, beta)")
    ap.add_argument('--negative', default=None, help='negative prompt; the default is empty, as every past arm used')
    ap.add_argument('--tag', default=None, help='suffix for the render folder and result file')
    ap.add_argument('--force', action='store_true',
                    help='overwrite an existing result-<profile>-<size>.json (default: refuse, because that file is '
                         'evidence from a previous session - copy it to a dated name instead)')
    ap.add_argument('--out-name', default=None,
                    help='write this result file instead (e.g. result-klein-512-20261025.json)')
    args = ap.parse_args()

    HOST = args.host.rstrip('/')
    arm_name = args.profile + (('-' + args.tag) if args.tag else '')
    existing = ROOT / (args.out_name or ('result-%s-%d.json' % (arm_name, args.size)))
    if existing.exists() and not args.force:
        print('%s already exists and holds an earlier session\'s measurement.\n'
              'Move it aside (e.g. %s) or pass --force. Naming is how this lab keeps its evidence.'
              % (existing.name, existing.with_name(existing.stem + '-%s.json' % datetime.now().strftime('%Y%m%d')).name))
        return 2

    corpus = json.loads((ROOT / 'prompts-diagrams.json').read_text(encoding='utf-8'))
    prompts = [p for p in corpus['prompts'] if not args.only or p['id'] == args.only]
    suffix = corpus['params']['style_suffix']
    seeds = [int(s) for s in args.seeds.split(',')]
    outdir = ROOT / 'render' / ('%s-%d' % (arm_name, args.size))
    outdir.mkdir(parents=True, exist_ok=True)
    idle = vram()

    rows = []
    for p in prompts:
        for seed in seeds:
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
            ok, images, err = False, [], None
            try:
                r = post('/prompt', {"prompt": build(args.profile, p['prompt'] + suffix, args.size, seed,
                                                     cfg=args.cfg, sampler=args.sampler,
                                                     scheduler=args.scheduler,
                                                     negative=args.negative or '', steps=args.steps)})
                pid = r['prompt_id']
                while True:
                    h = get('/history/' + pid)
                    if pid in h:
                        e = h[pid]
                        images = [i for o in e.get('outputs', {}).values() for i in o.get('images', [])]
                        ok = bool(images) and e.get('status', {}).get('status_str') == 'success'
                        if not ok:
                            msgs = e.get('status', {}).get('messages', [])
                            detail = ''
                            for m in msgs:
                                if isinstance(m, (list, tuple)) and m and m[0] == 'execution_error':
                                    d = m[1] if len(m) > 1 else {}
                                    detail = '%s at node %s %s: %s' % (d.get('exception_type'),
                                                                       d.get('node_id'), d.get('node_type'),
                                                                       str(d.get('exception_message'))[:160])
                                    break
                            err = detail or str(msgs)[:200]
                        break
                    time.sleep(1)
                    if time.monotonic() - started > 1800:
                        err = 'timeout'
                        break
            except Exception as ex:
                err = type(ex).__name__ + ': ' + str(ex)
            finally:
                stop.set()
                t.join(timeout=2)

            wall = round(time.monotonic() - started, 1)
            row = {'id': p['id'], 'kind': p['kind'], 'seed': seed, 'size': args.size, 'profile': arm_name,
                   'steps': PROFILES[args.profile]['steps'] if args.steps is None else args.steps,
                   'cfg': PROFILES[args.profile]['cfg'] if args.cfg is None else args.cfg,
                   'sampler': args.sampler or 'euler', 'scheduler': args.scheduler or 'simple',
                   'negative': (args.negative or '')[:60],
                   'ok': ok, 'wall_s': wall, 'vram_peak_mib': peak['v'] or None, 'error': err}
            if ok:
                src = OUTPUT / images[0]['filename']
                if src.exists():
                    dest = outdir / ('%s-s%d.png' % (p['id'], seed))
                    shutil.copy2(src, dest)
                    row['file'] = dest.name
            rows.append(row)
            print('  %-13s s%-5d %-4s %5.1fs vram %s' % (p['id'], seed, 'OK' if ok else 'FAIL', wall,
                                                         row['vram_peak_mib']), flush=True)

    out = ROOT / (args.out_name or ('result-%s-%d.json' % (arm_name, args.size)))
    out.write_text(json.dumps({'profile': args.profile, 'size': args.size, 'seeds': seeds,
                               'idle_vram_mib': idle, 'rows': rows}, indent=1), encoding='utf-8')
    walls = sorted(r['wall_s'] for r in rows if r['ok'])
    print('\n%d/%d ok; median %.1fs; peak %s MiB; -> %s' % (len(walls), len(rows),
          walls[len(walls) // 2] if walls else -1, max((r['vram_peak_mib'] or 0) for r in rows), out.name))


if __name__ == '__main__':
    main()
