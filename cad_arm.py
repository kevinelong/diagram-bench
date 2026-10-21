"""cad_arm.py - method E across the corpus, with a run-read-fix-rerun loop and source-level scoring.

For each prompt: ask the local coder model for a `pcbnew` script, run it with KiCad's Python, and if it fails feed
the error back for up to `--attempts` rounds. Then export SVG and score **from the source** - paths, text nodes,
required labels - because raster proxies are invalid for a vector-native arm (F.SilkS exports pale yellow).

Also writes a recoloured copy of each SVG (strokes to black) so a raster/OCR/VLM pass is possible at all; the
recolouring is recorded as a rendering step, never as part of the artifact.

Usage (cmd.exe):
  .venv\\Scripts\\python.exe cad_arm.py --model qwen3-coder:30b --prompts flow-1,circuit-1,blueprint-1
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KICAD_PY = r'C:\Users\kevin\AppData\Local\Programs\KiCad\10.0\bin\python.exe'
KICAD_CLI = r'C:\Users\kevin\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli.exe'
WORK = ROOT / 'kicad'
OLLAMA = 'http://127.0.0.1:11434/api/generate'

SYSTEM = 'You write Python scripts for KiCad 10 pcbnew. Output ONE python script and nothing else.'

TASK = """Write a python script that builds a KiCad board and draws this diagram on the silkscreen layer
(F.SilkS) using pcbnew:

{prompt}

Required text labels, each as its own pcbnew.PCB_TEXT: {labels}.

Use exactly this pattern (it is verified to work on pcbnew {version}):

```python
import pcbnew
MM = 1_000_000
board = pcbnew.CreateEmptyBoard()          # there is NO board size to set
def seg(x1, y1, x2, y2, w=0.4):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetLayer(pcbnew.F_SilkS)
    s.SetStart(pcbnew.VECTOR2I(int(x1*MM), int(y1*MM)))
    s.SetEnd(pcbnew.VECTOR2I(int(x2*MM), int(y2*MM)))
    s.SetWidth(int(w*MM))
    board.Add(s)
def circ(cx, cy, r, filled=False, lw=0.3):
    c = pcbnew.PCB_SHAPE(board)
    c.SetShape(pcbnew.SHAPE_T_CIRCLE)
    c.SetLayer(pcbnew.F_SilkS)
    c.SetCenter(pcbnew.VECTOR2I(int(cx*MM), int(cy*MM)))
    c.SetEnd(pcbnew.VECTOR2I(int((cx+r)*MM), int(cy*MM)))
    c.SetWidth(int(lw*MM))
    c.SetFilled(filled)
    board.Add(c)
def rect(x, y, w, h, lw=0.4):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_RECT)
    s.SetLayer(pcbnew.F_SilkS)
    s.SetStart(pcbnew.VECTOR2I(int(x*MM), int(y*MM)))
    s.SetEnd(pcbnew.VECTOR2I(int((x+w)*MM), int((y+h)*MM)))
    s.SetWidth(int(lw*MM))
    board.Add(s)
def text(x, y, value, size=2.5):
    tx = pcbnew.PCB_TEXT(board)
    tx.SetText(value)
    tx.SetLayer(pcbnew.F_SilkS)
    tx.SetPosition(pcbnew.VECTOR2I(int(x*MM), int(y*MM)))
    tx.SetTextSize(pcbnew.VECTOR2I(int(size*MM), int(size*MM)))
    tx.SetTextThickness(int(0.3*MM))
    board.Add(tx)
# ... draw here using seg(), rect(), circ() and text(); rotate a shape with SetOrientation(degrees) ...
pcbnew.SaveBoard(r'{path}', board)
```

Rules: never call SetWidth or SetHeight on `board` - those methods do not exist; call SetWidth only on shape
objects. Keep everything inside 0..90 mm horizontally and 0..60 mm vertically. Use Python 3 print syntax. Do not
read files or use argparse. Output only the script."""


def ask(model, prompt, timeout=600, seed=31337, temperature=0):
    body = {'model': model, 'system': SYSTEM, 'prompt': prompt, 'stream': False,
            'options': {'temperature': temperature, 'seed': seed, 'num_predict': 8192}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get('response', ''), round(time.monotonic() - t0, 1)


def code_of(reply):
    m = re.search(r'```(?:python)?\s*([\s\S]*?)```', reply)
    return m.group(1) if m else reply


def run_script(script_path):
    t0 = time.monotonic()
    try:
        p = subprocess.run([KICAD_PY, str(script_path)], capture_output=True, text=True, timeout=240)
        return p.returncode, (p.stdout or '')[-400:], (p.stderr or '')[-800:], round(time.monotonic() - t0, 1)
    except subprocess.TimeoutExpired:
        return 124, '', 'timeout', round(time.monotonic() - t0, 1)


def export_svg(pcb, svg_out):
    p = subprocess.run([KICAD_CLI, 'pcb', 'export', 'svg', '--mode-single', '--layers', 'F.SilkS',
                        '--exclude-drawing-sheet', '--page-size-mode', '2', '-o', str(svg_out), str(pcb)],
                       capture_output=True, text=True, timeout=240)
    return p.returncode, (p.stderr or '')[-300:]


def fit_viewbox(src, dst, margin=3.0):
    """KiCad exports the page/board area; crop to what was actually drawn."""
    text = Path(src).read_text(encoding='utf-8')
    xs, ys = [], []
    for m in re.finditer(r'[ML]\s*(-?[\d.]+)[ ,](-?[\d.]+)', text):
        xs.append(float(m.group(1))); ys.append(float(m.group(2)))
    if not xs:
        Path(dst).write_text(text, encoding='utf-8')
        return None
    x0, x1, y0, y1 = min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin
    vb = 'viewBox="%s %s %s %s"' % (round(x0, 3), round(y0, 3), round(x1 - x0, 3), round(y1 - y0, 3))
    text = re.sub(r'viewBox="[^"]*"', vb, text, count=1)
    text = re.sub(r'width="[^"]*"', 'width="%s"' % round(x1 - x0, 3), text, count=1)
    text = re.sub(r'height="[^"]*"', 'height="%s"' % round(y1 - y0, 3), text, count=1)
    Path(dst).write_text(text, encoding='utf-8')
    return {'w': round(x1 - x0, 1), 'h': round(y1 - y0, 1), 'aspect': round((x1 - x0) / (y1 - y0), 2) if y1 > y0 else None}


def recolour(src, dst):
    """KiCad encodes colour as CSS inside style attributes (`stroke:#F2EDA1`), not as SVG attributes."""
    text = Path(src).read_text(encoding='utf-8')
    text = re.sub(r'stroke:\s*#[0-9A-Fa-f]{6}', 'stroke:#000000', text)
    text = re.sub(r'fill:\s*#[0-9A-Fa-f]{6}', 'fill:#000000', text)
    text = re.sub(r'stroke="(?!none)[^"]*"', 'stroke="#000000"', text)
    Path(dst).write_text(text, encoding='utf-8')


def score_svg(svg_path, labels):
    text = Path(svg_path).read_text(encoding='utf-8')
    paths = text.count('<path')
    nodes = sum(text.count(c) for c in ('C', 'L', 'M', 'Q'))
    texts = re.findall(r'<text[^>]*>(.*?)</text>', text, re.S)
    texts = [' '.join(t.split()) for t in texts]
    hits = {l: any(l == t or l in t for t in texts) for l in labels}
    return {'paths': paths, 'nodes': nodes, 'svg_bytes': len(text.encode()),
            'text_nodes': len(texts), 'text_values': texts[:12],
            'labels_present': sum(hits.values()), 'labels_total': len(labels), 'label_hits': hits}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='qwen3-coder:30b')
    ap.add_argument('--prompts', default='flow-1,circuit-1,blueprint-1')
    ap.add_argument('--task', default=None, help='JSON file with prompt+labels instead of corpus ids')
    ap.add_argument('--attempts', type=int, default=3)
    ap.add_argument('--no-example', action='store_true', help='bare spec: no worked pcbnew template')
    args = ap.parse_args()

    corpus = json.loads((ROOT / 'prompts-diagrams.json').read_text(encoding='utf-8'))
    by_id = {p['id']: p for p in corpus['prompts']}
    if args.task:
        task = json.loads((ROOT / args.task).read_text(encoding='utf-8'))
        by_id = {'hard-task': {'id': 'hard-task', 'prompt': task['prompt'], 'labels': ['CUE']}}
        args.prompts = 'hard-task'
    WORK.mkdir(exist_ok=True)
    rows = []
    for pid in [p.strip() for p in args.prompts.split(',')]:
        prompt = by_id[pid]
        labels = prompt.get('labels', [])
        pcb = WORK / ('%s%s.kicad_pcb' % (pid, '-bare' if args.no_example else ''))
        txt = WORK / ('%s%s.svg' % (pid, '-bare' if args.no_example else ''))
        dark = WORK / ('%s%s-black.svg' % (pid, '-bare' if args.no_example else ''))
        attempts = []
        ok = False
        feedback = ''
        for attempt in range(1, args.attempts + 1):
            if args.no_example:
                task = ('Write a python script that builds a KiCad board and draws this diagram on the silkscreen '
                        'layer (F.SilkS) using pcbnew:\n\n' + prompt['prompt'] +
                        '\n\nRequired text labels, each as its own pcbnew.PCB_TEXT: ' + ', '.join(labels) +
                        '.\nFinish with pcbnew.SaveBoard(r\'' + str(pcb) + '\', board). Output only the script.')
            else:
                task = TASK.format(prompt=prompt['prompt'], labels=', '.join(labels), path=str(pcb), version='10.0.6')
            if feedback:
                task += ('\n\nYour previous attempt failed when run. Fix it and return the complete corrected '
                         'script. Error was:\n' + feedback)
            # vary the seed per attempt: at temperature 0 a retry reproduces the same sample exactly
            reply, gen_s = ask(args.model, task, seed=31337 + 101 * attempt, temperature=0.0 if attempt == 1 else 0.4)
            script = WORK / ('%s_try%d.py' % (pid, attempt))
            script.write_text(code_of(reply), encoding='utf-8')
            rc, out, err, run_s = run_script(script)
            wrote = pcb.exists()
            attempts.append({'attempt': attempt, 'gen_s': gen_s, 'run_s': run_s, 'exit': rc,
                             'board_written': wrote, 'error': err.strip().splitlines()[-1] if err.strip() else ''})
            print('  %-13s attempt %d: exit %s board %s (%s)' % (pid, attempt, rc, wrote,
                                                                 (err.strip().splitlines()[-1][:60] if err.strip() else 'no stderr')),
                  flush=True)
            if rc == 0 and wrote:
                ok = True
                break
            feedback = err.strip()[-600:]
            if wrote:                     # a board from a failed script: keep it, but try to fix the script
                wrote = False
        row = {'id': pid, 'attempts': attempts, 'script_clean': ok}
        if pcb.exists():
            src = export_svg(pcb, txt)
            row['export_rc'] = src[0]
            if txt.exists():
                # no XML-level cropping: KiCad path data uses mixed relative/absolute commands, so the box is
                # measured from the raster instead (see crop_render.py)
                recolour(txt, dark)
                row['source_score'] = score_svg(txt, labels)
                row['rendered_for_judging'] = dark.name
        rows.append(row)
        print('  %-13s -> %s' % (pid, json.dumps({k: v for k, v in row.items() if k != 'attempts'})[:220]), flush=True)

    out = ROOT / 'result-method-e-cad.json'
    out.write_text(json.dumps({'arm': 'E-kicad', 'model': args.model, 'rows': rows}, indent=1), encoding='utf-8')
    clean = sum(1 for r in rows if r['script_clean'])
    labelled = sum(1 for r in rows if r.get('source_score') and
                   r['source_score']['labels_present'] == r['source_score']['labels_total'])
    print('\nscripts running cleanly: %d/%d; all labels present in source: %d/%d' % (clean, len(rows), labelled, len(rows)))


if __name__ == '__main__':
    sys.exit(main())
