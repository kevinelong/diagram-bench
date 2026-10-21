"""hard_v2.py - generate with the corrected v2 task, then score with check_geometry_v2."""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from check_geometry_v2 import analyse

ROOT = Path(__file__).resolve().parent
OLLAMA = 'http://127.0.0.1:11434/api/generate'
SYSTEM = 'You are a technical illustrator. You output ONE complete SVG document and nothing else.'

KIT = """

Draw with this kit, and do the arithmetic it implies:
  rect(x, y, w, h); circle(cx, cy, r); text(x, y, s)
  line_at(x, y, degrees, length): a line from (x, y) at `degrees` from the +x axis, downward for positive
      angles: x2 = x + length*cos(radians(degrees)), y2 = y + length*sin(radians(degrees))
Rules: viewBox="0 0 1024 1024"; black strokes, flat fills, white background; every element inside 0..1024."""


def ask(model, prompt, seed=1234, timeout=900):
    body = {'model': model, 'system': SYSTEM, 'prompt': prompt, 'stream': False,
            'options': {'temperature': 0, 'seed': seed, 'num_predict': 8192}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.load(r)
    return payload.get('response', ''), round(time.monotonic() - t0, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='qwen3-coder:30b')
    ap.add_argument('--configs', default='bare,kit')
    args = ap.parse_args()
    task = json.loads((ROOT / 'hard-task-pooltable-v2.json').read_text(encoding='utf-8'))
    out = ROOT / 'hard'
    out.mkdir(exist_ok=True)
    rows = []
    for cfg in [c.strip() for c in args.configs.split(',')]:
        prompt = task['prompt'] + (KIT if cfg == 'kit' else '')
        reply, secs = ask(args.model, prompt)
        m = re.search(r'<svg[\s\S]*?</svg>', reply, re.IGNORECASE)
        row = {'config': cfg, 'gen_s': secs}
        if not m:
            row.update({'error': 'no <svg>', 'score': '0/7'})
        else:
            path = out / ('pooltable-v2-%s.svg' % cfg)
            path.write_text(m.group(0), encoding='utf-8')
            r = analyse(str(path))
            row.update({k: r.get(k) for k in ('aspect', 'corners', 'long_rail_side_pockets',
                                              'short_rail_side_pockets', 'apex_on_centre', 'back_row_parallel',
                                              'cue_ball_on_spot', 'cue_deg', 'label_CUE', 'score')})
            row['pass'] = r.get('pass')
            print('  %-5s -> %s   %s' % (cfg, row['score'], {k: v for k, v in (row.get('pass') or {}).items() if not v}), flush=True)
        rows.append(row)
    (ROOT / 'result-hard-v2.json').write_text(json.dumps(rows, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
