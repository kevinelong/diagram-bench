"""a3_vram_floor.py - measure Z-Image's free-VRAM headroom under four ComfyUI scheduling flags.

Declared in DECLARATION-20261021-a3-vram-floor.md. Each variant starts its own server (so the flags are real),
runs one fixed image through the Z-Image arm, reads the arm's sampled peak, and stops the server.

    python a3_vram_floor.py
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent
PY = r'C:\Users\kevin\Documents\ComfyUI\.venv\Scripts\python.exe'
MAIN = r'C:\Users\kevin\AppData\Local\Programs\ComfyUI\resources\ComfyUI\main.py'
PORT = 8002

VARIANTS = {
    'v0-baseline': [],
    'v1-reserve-1g': ['--reserve-vram', '1.0'],
    'v2-cache-none': ['--cache-none'],
    'v3-lowvram': ['--lowvram'],
}


def server_args(extra):
    return [PY, MAIN,
            '--user-directory', r'C:\Users\kevin\Documents\ComfyUI\user',
            '--output-directory', r'C:\Users\kevin\Documents\ComfyUI\output',
            '--base-directory', r'C:\Users\kevin\Documents\ComfyUI',
            '--extra-model-paths-config', r'C:\Users\kevin\AppData\Roaming\ComfyUI\extra_models_config.yaml',
            '--listen', '127.0.0.1', '--port', str(PORT)] + extra


def ready(timeout=180):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/system_stats' % PORT, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def free_now():
    out = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.total', '--format=csv,noheader'],
                         capture_output=True, text=True, timeout=10).stdout.strip()
    used, total = [int(x.split()[0]) for x in out.split(',')]
    return total - used, total


def run_variant(name, extra):
    log = REPO / 'render' / ('a3-%s.log' % name)
    log.parent.mkdir(exist_ok=True)
    with open(log, 'w', encoding='utf-8', errors='replace') as fh:
        proc = subprocess.Popen(server_args(extra), stdout=fh, stderr=subprocess.STDOUT, cwd=r'C:\Users\kevin\Documents\ComfyUI')
        try:
            if not ready():
                return {'variant': name, 'flags': extra, 'status': 'server did not become ready'}
            free_before, total = free_now()
            started = time.monotonic()
            run = subprocess.run([PY, str(REPO / 'zimage_arm.py'), '--prompts', 'prompts-density.json',
                                  '--tag', 'a3-%s' % name, '--only', 'net-1-words',
                                  '--seeds', '1234', '--steps', '4'],
                                 capture_output=True, text=True, timeout=1800,
                                 env=dict(__import__('os').environ, COMFY_HOST='http://127.0.0.1:%d' % PORT))
            wall = round(time.monotonic() - started, 1)
            res = REPO / ('result-arm-a3-%s.json' % name)
            row = {}
            if res.exists():
                data = json.loads(res.read_text(encoding='utf-8'))
                if data.get('rows'):
                    row = data['rows'][0]
            return {'variant': name, 'flags': extra, 'status': 'ran',
                    'free_before_mib': free_before, 'vram_total_mib': total,
                    'vram_peak_mib': row.get('vram_peak_mib'),
                    'free_at_peak_mib': (total - row['vram_peak_mib']) if row.get('vram_peak_mib') else None,
                    'wall_s': wall, 'image_wall_s': row.get('wall_s'), 'sha256': row.get('sha256'),
                    'ok': row.get('ok'), 'arm_stdout_tail': run.stdout.strip().splitlines()[-2:]}
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
            time.sleep(3)


def main():
    out = []
    for name, extra in VARIANTS.items():
        print('\n=== %s %s' % (name, ' '.join(extra) or '(no flags)'), flush=True)
        row = run_variant(name, extra)
        print('  %s' % json.dumps({k: v for k, v in row.items() if k != 'arm_stdout_tail'}), flush=True)
        out.append(row)
    path = REPO / 'result-a3-vram-floor.json'
    path.write_text(json.dumps({'declaration': 'DECLARATION-20261021-a3-vram-floor.md',
                                'prompt': 'net-1-words', 'size': 1024, 'seed': 1234, 'variants': out}, indent=1),
                    encoding='utf-8')
    print('\nwrote %s' % path)
    for r in out:
        print('%-16s free at peak %-6s MiB  image %-6ss  sha %s' % (
            r['variant'], r.get('free_at_peak_mib'), r.get('image_wall_s'), (r.get('sha256') or '-')[:12]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
