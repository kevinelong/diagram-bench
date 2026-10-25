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
import threading
import time
import urllib.request
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
    'schnell': dict(steps=4, cfg=1.0, guidance=None, prefix='g-schnell',
                    nodes={
                        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "flux1-schnell-Q4_K_S.gguf"}},
                        "2": {"class_type": "DualCLIPLoader", "inputs": {
                            "clip_name1": "t5xxl_fp8_e4m3fn.safetensors",
                            "clip_name2": "clip_l.safetensors", "type": "flux"}},
                        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux1-ae.safetensors"}},
                        "clip": ["2", 0], "vae": ["3", 0], "model": ["1", 0]}),
    'dev': dict(steps=20, cfg=1.0, guidance=3.5, prefix='g-dev',
                nodes={
                    "1": {"class_type": "CheckpointLoaderSimple",
                          "inputs": {"ckpt_name": "flux1-dev-fp8.safetensors"}},
                    "clip": ["1", 1], "vae": ["1", 2], "model": ["1", 0]}),
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
        return int(subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader'],
                                  capture_output=True, text=True, timeout=10).stdout.strip().split()[0])
    except Exception:
        return None


def build(profile, prompt, size, seed):
    p = PROFILES[profile]
    wf = dict(p['nodes'])
    wf = {k: (v if isinstance(v, dict) else v) for k, v in wf.items() if isinstance(v, dict)}
    wf["4"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": PROFILES[profile]['nodes']['clip']}}
    wf["5"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": PROFILES[profile]['nodes']['clip']}}
    if p['guidance'] is not None:
        wf["4g"] = {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": p['guidance']}}
        positive = ["4g", 0]
    else:
        positive = ["4", 0]
    wf["6"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": size, "height": size, "batch_size": 1}}
    wf["7"] = {"class_type": "KSampler", "inputs": {"model": PROFILES[profile]['nodes']['model'],
                                                    "positive": positive, "negative": ["5", 0],
                                                    "latent_image": ["6", 0], "seed": seed,
                                                    "steps": p['steps'], "cfg": p['cfg'],
                                                    "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}}
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
    args = ap.parse_args()

    HOST = args.host.rstrip('/')

    corpus = json.loads((ROOT / 'prompts-diagrams.json').read_text(encoding='utf-8'))
    prompts = [p for p in corpus['prompts'] if not args.only or p['id'] == args.only]
    suffix = corpus['params']['style_suffix']
    seeds = [int(s) for s in args.seeds.split(',')]
    outdir = ROOT / 'render' / ('%s-%d' % (args.profile, args.size))
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
                r = post('/prompt', {"prompt": build(args.profile, p['prompt'] + suffix, args.size, seed)})
                pid = r['prompt_id']
                while True:
                    h = get('/history/' + pid)
                    if pid in h:
                        e = h[pid]
                        images = [i for o in e.get('outputs', {}).values() for i in o.get('images', [])]
                        ok = bool(images) and e.get('status', {}).get('status_str') == 'success'
                        if not ok:
                            err = str(e.get('status', {}).get('messages', ''))[:200]
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
            row = {'id': p['id'], 'kind': p['kind'], 'seed': seed, 'size': args.size, 'profile': args.profile,
                   'steps': PROFILES[args.profile]['steps'], 'cfg': PROFILES[args.profile]['cfg'],
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

    out = ROOT / ('result-%s-%d.json' % (args.profile, args.size))
    out.write_text(json.dumps({'profile': args.profile, 'size': args.size, 'seeds': seeds,
                               'idle_vram_mib': idle, 'rows': rows}, indent=1), encoding='utf-8')
    walls = sorted(r['wall_s'] for r in rows if r['ok'])
    print('\n%d/%d ok; median %.1fs; peak %s MiB; -> %s' % (len(walls), len(rows),
          walls[len(walls) // 2] if walls else -1, max((r['vram_peak_mib'] or 0) for r in rows), out.name))


if __name__ == '__main__':
    main()
