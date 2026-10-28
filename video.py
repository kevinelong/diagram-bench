"""video.py - a few seconds of motion from a finished still, locally, with Wan 2.2 TI2V-5B.

Why this model: it is the smallest first-party image-to-video that runs on a 12 GB card (5B, apache-2.0), its ComfyUI
nodes are already in this build (`Wan22ImageToVideoLatent`, `SaveAnimatedWEBP`), and it takes a start image directly -
no separate encoder pass, no ControlNet. Weights are GGUF Q4_K_M (3.20 GiB) + umt5-xxl fp8 (6.27 GiB) + the 2.2 VAE
(1.31 GiB); the encoder and the diffusion model are loaded one after the other, so the peak is the larger of the two,
which is why the image model is unloaded first.

Two rules this file enforces rather than documents:
  * the still is cropped to a grid the model was trained on (704x704 or 1280x704, multiples of 32). Stretching
    instead of cropping is what produces the warped-motion look;
  * `length` is 4k+1 frames (49, 81, 121), which is what the model's temporal compression accepts.

Usage (cmd.exe):
  .venv\\Scripts\\python.exe video.py --still render/klein9_unc-512/flow-1-s1234.png --frames 49
  .venv\\Scripts\\python.exe video.py --still <path> --grid 16:9 --frames 81 --prompt "the camera drifts left"
"""
import argparse
import json
import subprocess

from winproc import popen, run
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

import zimage_gen as zg

ROOT = Path(__file__).resolve().parent
HOST = 'http://127.0.0.1:8006'
OUTPUT = Path(r'C:\Users\kevin\Documents\ComfyUI\output')
CLIP_OUT = Path(r'C:\Users\kevin\Pictures\zimage-ui')

WAN = {
    'unet': 'Wan2.2-TI2V-5B-Q4_K_M.gguf',
    'clip': 'umt5_xxl_fp8_e4m3fn_scaled.safetensors',
    'vae': 'wan2.2_vae.safetensors',
    'clip_type': 'wan',
    'steps': 30,
    'cfg': 5.0,
    'shift': 8.0,
    'sampler': 'uni_pc',
    'scheduler': 'simple',
}

# Wan 2.1 I2V-14B (480p). Different VAE, different conditioning node, and a CLIP-vision tower the 5B does not use, so
# this is a second profile rather than a flag on the first: `Wan22ImageToVideoLatent` packs a start image into a
# 16x16x4 latent, while `WanFirstLastFrameToVideo` builds *conditioning* for the 8x8x4 Wan 2.1 path - and only that path
# can be given an end image at all.
WAN_I2V14 = {
    'unet': 'wan2.1-i2v-14b-480p-Q3_K_M.gguf',
    'clip': 'umt5_xxl_fp8_e4m3fn_scaled.safetensors',
    'vae': 'wan_2.1_vae.safetensors',
    'clip_vision': 'clip_vision_h.safetensors',
    'clip_type': 'wan',
    'steps': 30,
    'cfg': 5.0,
    'shift': 8.0,
    'sampler': 'uni_pc',
    'scheduler': 'simple',
}

PROFILES = {'5b': WAN, 'i2v14': WAN_I2V14}

GRIDS = {'1:1': (704, 704), '16:9': (1280, 704), '4:3': (1024, 704)}
# 480p is what this model was trained for; the app can upscale the result, but a 704 grid would be off-distribution
GRIDS_480 = {'1:1': (480, 480), '16:9': (832, 480), '4:3': (640, 480)}


def grids_for(profile):
    return GRIDS_480 if PROFILES[profile].get('clip_vision') else GRIDS
VRAM_FLOOR_MIB = 512          # the lab's rule: never leave less than half a gigabyte free on the card
DEFAULT_NEGATIVE = ('blurry, low quality, distorted, deformed, extra limbs, watermark, text, static, '
                    'jitter, warping, morphing')


def post(path, obj, host=None, timeout=120):
    req = urllib.request.Request((host or HOST) + path, data=json.dumps(obj).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def get(path, host=None, timeout=60):
    return json.load(urllib.request.urlopen((host or HOST) + path, timeout=timeout))


def vram_total():
    try:
        out = run(['nvidia-smi', '--query-gpu=memory.total', '--format=csv,noheader'],
                  capture_output=True, text=True, timeout=10).stdout.strip()
        return int(out.split()[0])
    except Exception:
        return None


def vram():
    try:
        return int(run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader'],
                                  capture_output=True, text=True, timeout=10).stdout.strip().split()[0])
    except Exception:
        return None


def unload(host=None):
    """Drop whatever the server holds in VRAM. Returns the HTTP status, and never raises on a refused unload."""
    try:
        return post('/free', {'unload_models': True, 'free_memory': True}, host=host, timeout=60)
    except Exception as e:
        return '%s: %s' % (type(e).__name__, e)


def prepare_still(src, width, height, out=None):
    """Cover-resize then centre-crop to exactly width x height, so the model sees the grid it was trained on."""
    src = Path(src)
    with Image.open(src) as im:
        im = im.convert('RGB')
        scale = max(width / im.width, height / im.height)
        resized = im.resize((max(width, round(im.width * scale)), max(height, round(im.height * scale))),
                            Image.LANCZOS)
        left = (resized.width - width) // 2
        top = (resized.height - height) // 2
        frame = resized.crop((left, top, left + width, top + height))
    dest = Path(out) if out else src.with_name('%s-%dx%d.png' % (src.stem, width, height))
    frame.save(dest)
    return dest




_CODEC_TAGS = (b'avc1', b'avc3', b'hvc1', b'hev1', b'av01', b'vp09', b'mp4v')

def mp4_codec(path):
    """The sample-entry fourcc of an MP4's video track, or None.

    Windows' own player refuses some of these outright - HEVC without the paid extension and AV1 without the AV1
    extension both surface as 0xC00D36C4 "unsupported or corrupt", which reads like a broken file and is not one. The
    app opens clips in VLC, which ships its own decoders, and this lets the hint say which case it is.
    """
    try:
        blob = Path(path).read_bytes()[:262144]
    except OSError:
        return None
    head = blob.find(b'ftyp')
    if head < 0:
        return None
    for tag in _CODEC_TAGS:
        if blob.find(tag, head) > 0:
            return tag.decode('latin-1')
    return None

def _wsl_path(path):
    """C:\\Users\\kevin\\x -> /mnt/c/Users/kevin/x, for the ffmpeg that lives in WSL."""
    p = Path(path).resolve()
    return '/mnt/' + p.drive.rstrip(':').lower() + p.as_posix()[2:]


def to_mp4(webp_path, fps=24, on_event=None):
    """Re-encode the animated WebP as H.264 MP4, through the ffmpeg that lives in WSL.

    The WebP is decoded by PIL and written out as a PNG sequence first: ffmpeg's own WebP decoder rejects the animated
    file ComfyUI writes ("Invalid data found when processing input"), while PIL reads all of its frames correctly. The
    extra step costs a couple of seconds and removes the one dependency that did not work. The WebP stays beside the
    MP4 as the direct model output.
    """
    from PIL import Image, ImageSequence
    say = on_event or (lambda msg: None)
    src = Path(webp_path)
    dest = src.with_suffix('.mp4')
    started = time.monotonic()
    frames_dir = src.with_name(src.stem + '-frames')
    try:
        frames = [f.convert('RGB') for f in ImageSequence.Iterator(Image.open(src))]
        frames_dir.mkdir(exist_ok=True)
        for i, frame in enumerate(frames):
            frame.save(frames_dir / ('f%05d.png' % i))
        say('encoding %d frames to mp4' % len(frames))
        # winproc.run, so the WSL console does not flash on screen while the clip is encoded
        run(['wsl', '-e', 'ffmpeg', '-y', '-loglevel', 'error',
             '-framerate', str(fps), '-i', _wsl_path(frames_dir / 'f%05d.png'),
             '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20',
             '-movflags', '+faststart', _wsl_path(dest)],
            capture_output=True, text=True, timeout=600, check=True)
    except Exception as e:
        detail = getattr(e, 'stderr', '') or str(e)
        try:
            dest.unlink(missing_ok=True)      # a 0-byte mp4 is what a media player calls "corrupt"
        except OSError:
            pass
        return None, '%s: %s' % (type(e).__name__, str(detail)[-160:])
    finally:
        for f in frames_dir.glob('*.png'):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            frames_dir.rmdir()
        except OSError:
            pass
    if not dest.exists() or dest.stat().st_size < 10240:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        return None, 'encode produced no usable file'
    return dest, round(time.monotonic() - started, 1)


def graph(image_name, width, height, frames, prompt, negative, seed, steps, cfg, shift, sampler, scheduler, prefix):
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": WAN['unet']}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": WAN['clip'], "type": WAN['clip_type']}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": WAN['vae']}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 0]}},
        "6": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "7": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": shift}},
        "8": {"class_type": "Wan22ImageToVideoLatent",
              "inputs": {"vae": ["3", 0], "width": width, "height": height, "length": frames,
                         "batch_size": 1, "start_image": ["6", 0]}},
        "9": {"class_type": "KSampler",
              "inputs": {"model": ["7", 0], "positive": ["4", 0], "negative": ["5", 0],
                         "latent_image": ["8", 0], "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
        "11": {"class_type": "SaveAnimatedWEBP",
               "inputs": {"images": ["10", 0], "filename_prefix": prefix, "fps": 24.0,
                          "lossless": False, "quality": 88, "method": "default"}},
    }


def graph_first_last(start_name, end_name, width, height, frames, prompt, negative, seed, steps, cfg, shift,
                     sampler, scheduler, prefix, profile=None):
    """Wan 2.1 I2V-14B with a start *and* an end frame: only the middle is invented, so nothing can drift.

    The endpoints are encoded three ways and all three matter: `WanFirstLastFrameToVideo` takes the two images as pixels
    (VAE-encoded inside the node), as CLIP-vision embeddings, and as the conditioning that says where the clip must
    arrive. That is why this profile needs `clip_vision_h` and the 2.1 VAE, and why it cannot be bolted onto the 5B.
    """
    wan = profile or WAN_I2V14
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": wan['unet']}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": wan['clip'], "type": wan['clip_type']}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": wan['vae']}},
        "4": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": wan['clip_vision']}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 0]}},
        "7": {"class_type": "LoadImage", "inputs": {"image": start_name}},
        "8": {"class_type": "LoadImage", "inputs": {"image": end_name}},
        "9": {"class_type": "CLIPVisionEncode",
              "inputs": {"clip_vision": ["4", 0], "image": ["7", 0], "crop": "none"}},
        "10": {"class_type": "CLIPVisionEncode",
               "inputs": {"clip_vision": ["4", 0], "image": ["8", 0], "crop": "none"}},
        "11": {"class_type": "WanFirstLastFrameToVideo",
               "inputs": {"positive": ["5", 0], "negative": ["6", 0], "vae": ["3", 0],
                          "width": width, "height": height, "length": frames, "batch_size": 1,
                          "clip_vision_start_image": ["9", 0], "clip_vision_end_image": ["10", 0],
                          "start_image": ["7", 0], "end_image": ["8", 0]}},
        "12": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": shift}},
        "13": {"class_type": "KSampler",
               "inputs": {"model": ["12", 0], "positive": ["11", 0], "negative": ["11", 1],
                          "latent_image": ["11", 2], "seed": seed, "steps": steps, "cfg": cfg,
                          "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0}},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["3", 0]}},
        "15": {"class_type": "SaveAnimatedWEBP",
               "inputs": {"images": ["14", 0], "filename_prefix": prefix, "fps": 24.0,
                          "lossless": False, "quality": 88, "method": "default"}},
    }


def make_clip(still, grid='1:1', frames=49, prompt='', negative=DEFAULT_NEGATIVE, seed=1234,
              steps=None, cfg=None, host=None, out_dir=None, free_first=True, on_event=None,
              end=None, model='5b'):
    """Render a clip from `still`, or from `still` to `end`.

    Two modes, because they are two different models in this build: `model='5b'` is Wan 2.2 TI2V-5B and takes only a
    start image; `model='i2v14'` is Wan 2.1 I2V-14B at 480p and takes a start *and* an end image, which is the only way
    to make the last frame the one you drew. Returns a record; the file lands beside the still unless out_dir says so.
    """
    if model not in PROFILES:
        raise ValueError('unknown model %r, expected one of %s' % (model, sorted(PROFILES)))
    wan = PROFILES[model]
    grids = grids_for(model)
    if grid not in grids:
        raise ValueError('grid %r is not available for %s (choose %s)' % (grid, model, sorted(grids)))
    host = host or HOST
    width, height = grids[grid]
    frames = int(frames)
    if frames < 5 or (frames - 1) % 4:
        raise ValueError('frames must be 4k+1 (49, 81, 121 ...), got %d' % frames)
    steps = int(steps or wan['steps'])
    cfg = float(cfg if cfg is not None else wan['cfg'])
    still = Path(still)
    end = Path(end) if end else None
    if end is not None and model == '5b':
        raise ValueError('Wan 2.2 TI2V-5B has no end-image input; use --model i2v14 for first/last-frame clips')

    say = on_event or (lambda msg: None)
    if free_first:
        say('freeing video memory for the video model')
        unload(host)

    prepared = prepare_still(still, width, height,
                             out=(Path(out_dir) / ('%s-%s-%dx%d.png' % (still.stem, grid.replace(':', 'x'), width, height))
                                  if out_dir else None))
    say('still prepared at %dx%d: %s' % (width, height, prepared.name))
    uploaded = zg.upload_image(host, prepared)
    uploaded_end = None
    if end is not None:
        prepared_end = prepare_still(end, width, height,
                                     out=(Path(out_dir) / ('%s-%s-%dx%d.png'
                                                           % (end.stem, grid.replace(':', 'x'), width, height))
                                          if out_dir else None))
        say('end frame prepared at %dx%d: %s' % (width, height, prepared_end.name))
        uploaded_end = zg.upload_image(host, prepared_end)

    peak = {'v': 0}
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            v = vram()
            if v and v > peak['v']:
                peak['v'] = v
            stop.wait(0.5)

    t = threading.Thread(target=sampler, daemon=True)
    t.start()
    started = time.monotonic()
    try:
        prefix = 'wan-clip-%d' % int(time.time())
        if uploaded_end is not None:
            wf = graph_first_last(uploaded, uploaded_end, width, height, frames, prompt, negative, seed,
                                  steps, cfg, wan['shift'], wan['sampler'], wan['scheduler'], prefix, wan)
        else:
            wf = graph(uploaded, width, height, frames, prompt, negative, seed, steps, cfg,
                       wan['shift'], wan['sampler'], wan['scheduler'], prefix)
        pid = post('/prompt', {'prompt': wf}, host=host)['prompt_id']
        out = None
        while time.monotonic() - started < 3600:
            h = get('/history/' + pid, host=host)
            if pid in h:
                entry = h[pid]
                if entry.get('status', {}).get('status_str') == 'error':
                    raise RuntimeError('the clip failed: %s' % str(entry.get('status', {}).get('messages'))[:240])
                files = [f for o in entry.get('outputs', {}).values() for f in o.get('images', [])]
                if files:
                    out = files[0]
                    break
            time.sleep(1)
        if out is None:
            raise RuntimeError('no clip after %.0fs' % (time.monotonic() - started))
    finally:
        stop.set()
        t.join(timeout=3)
    wall = round(time.monotonic() - started, 1)

    q = urllib.parse.urlencode({'filename': out['filename'], 'subfolder': out.get('subfolder', ''),
                                'type': out.get('type', 'output')})
    data = urllib.request.urlopen(host + '/view?' + q, timeout=300).read()
    dest = Path(out_dir) / out['filename'] if out_dir else (CLIP_OUT / out['filename'])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)

    mp4, mp4_secs = to_mp4(dest, on_event=say)
    if mp4 is None:
        say('mp4 conversion failed (%s) - the animated WebP is still there' % mp4_secs)

    total = vram_total()
    free = (total - peak['v']) if (total and peak['v']) else None
    record = {'vram_free_mib': free, 'floor_breached': (free is not None and free < VRAM_FLOOR_MIB),
              'clip': str(dest.resolve()), 'mp4': (str(mp4.resolve()) if mp4 else None),
              'mp4_seconds': (mp4_secs if mp4 else None), 'still': str(still.resolve()), 'grid': grid,
              'width': width, 'height': height, 'frames': frames, 'fps': 24,
              'seconds': round(frames / 24.0, 2), 'steps': steps, 'cfg': cfg, 'seed': seed,
              'model': model, 'mode': 'first-last' if uploaded_end else 'start-only',
              'end': str(end.resolve()) if end else None,
              'wall_s': wall, 'vram_peak_mib': peak['v'] or None,
              'bytes': len(data), 'created': time.strftime('%Y-%m-%dT%H:%M:%S')}
    dest.with_suffix('.json').write_text(json.dumps(record, indent=1), encoding='utf-8')
    return record


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--still', required=True, help='finished PNG to animate')
    ap.add_argument('--grid', choices=sorted(set(GRIDS) | set(GRIDS_480)), default='1:1')
    ap.add_argument('--model', choices=sorted(PROFILES), default='5b',
                    help='5b = Wan 2.2 TI2V-5B (start image only); i2v14 = Wan 2.1 I2V-14B 480p (start and end)')
    ap.add_argument('--end', default='', help='last frame; needs --model i2v14')
    ap.add_argument('--frames', type=int, default=49, help='4k+1: 49 (2s), 81 (3.4s), 121 (5s) at 24 fps')
    ap.add_argument('--prompt', default='', help='what should happen; empty means a gentle natural continuation')
    ap.add_argument('--seed', type=int, default=1234)
    ap.add_argument('--steps', type=int, default=None)
    ap.add_argument('--cfg', type=float, default=None)
    ap.add_argument('--host', default=HOST)
    ap.add_argument('--out-dir', default=None)
    ap.add_argument('--keep-model', action='store_true', help='do not unload the image model first')
    args = ap.parse_args(argv)

    rec = make_clip(args.still, grid=args.grid, frames=args.frames, prompt=args.prompt, seed=args.seed,
                    steps=args.steps, cfg=args.cfg, host=args.host, out_dir=args.out_dir,
                    free_first=not args.keep_model, end=args.end or None, model=args.model)
    print('wrote %s' % rec['clip'])
    print('  %dx%d, %d frames, %.1f s at 24 fps, %d steps, cfg %s, seed %d'
          % (rec['width'], rec['height'], rec['frames'], rec['seconds'], rec['steps'], rec['cfg'], rec['seed']))
    print('  wall %ss, peak VRAM %s MiB (%s MiB free), %.1f MiB file'
          % (rec['wall_s'], rec['vram_peak_mib'], rec['vram_free_mib'], rec['bytes'] / 2**20))
    if rec.get('floor_breached'):
        print('  WARNING: %s MiB free is under the %s MiB floor - use a smaller grid or fewer frames'
              % (rec['vram_free_mib'], VRAM_FLOOR_MIB))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
