"""cad_arm.py - method E, with a checker-driven retry loop and a fitted, recoloured export.

Per prompt, one attempt is:

  1. ask the local coder model for a `pcbnew` script and run it with KiCad's Python;
  2. **add an Edge.Cuts outline** around what the script drew (`post_outline.add_outline`) - without it
     `--page-size-mode 2` has no board area to fit and the drawing exports as a speck on a page;
  3. export with `--mode-single --exclude-drawing-sheet --page-size-mode 2` (a directory `-o` silently writes
     nothing, and the drawing sheet adds ~23 stray `<text>` elements to every count);
  4. recolour the **CSS** colours KiCad writes inside `style` (`stroke:#F2EDA1`), not SVG attributes;
  5. **run the checker on the result** and, if anything fails, put the failed check ids and measured values into
     the next prompt.

Step 5 is the point of this version. The old loop only retried when the script crashed, so a script that ran
cleanly and drew the wrong diagram was recorded as a success. A retry also has to change *information*, not just
temperature: at temperature 0 the same seed reproduces the same sample, so every attempt varies the seed **and**
appends what was measured wrong.

Checks applied to each export (`evaluate`):
  * `struct.<field>` - boxes / connectors / labels counted from the vector, against `structure-expectations.json`
    where a frozen expectation exists for the prompt;
  * `label.<NAME>`  - every required label present as real text;
  * `geom.<check>`  - `check_geometry_v2.analyse()` on the exported SVG. That checker reads `<rect>`, `<line>` and
    `<circle>`; a KiCad export writes primitives as `<path>`, so it usually answers "no table found". That is
    recorded as `geometry_applicable: false` with the reason and is **not** fed back as a failure - inventing
    retry pressure from a checker that cannot see the file is exactly the instrument fault this lab keeps
    recording (see RESULTS-HARD-TASK.md, "four instrument faults").

Also writes a recoloured copy of each SVG (strokes to black) so a raster/OCR/VLM pass is possible at all; the
recolouring is recorded as a rendering step, never as part of the artifact.

`--dry-run --fixture` runs a known fixture script from `cad_fixtures.py` instead of the model, through the same
outline/export/recolour/check path, and prints the verdict. It never contacts Ollama.

Usage (cmd.exe):
  .venv\\Scripts\\python.exe cad_arm.py --model qwen3-coder:30b --prompts flow-1,circuit-1,blueprint-1
  .venv\\Scripts\\python.exe cad_arm.py --dry-run --fixture
  .venv\\Scripts\\python.exe cad_arm.py --dry-run --fixture bad
"""
import argparse
import json
import re
import subprocess

from winproc import popen, run
import sys
import time
import urllib.request
from pathlib import Path

import check_geometry_v2
import post_outline

ROOT = Path(__file__).resolve().parent
KICAD_PY = r'C:\Users\kevin\AppData\Local\Programs\KiCad\10.0\bin\python.exe'
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
        p = run([KICAD_PY, str(script_path)], capture_output=True, text=True, timeout=240)
        return p.returncode, (p.stdout or '')[-400:], (p.stderr or '')[-800:], round(time.monotonic() - t0, 1)
    except subprocess.TimeoutExpired:
        return 124, '', 'timeout', round(time.monotonic() - t0, 1)


def export_svg(pcb, svg_out):
    """Delegates to post_outline so the CLI flags exist in exactly one place."""
    return post_outline.export_svg(pcb, svg_out)


# ------------------------------------------------------------------ measured structure of a KiCad export

# KiCad 10 writes text twice: an invisible <text> (opacity="0", for selection/accessibility) and the real
# stroke-font glyphs inside <g class="stroked-text"><desc>START</desc>...</g>. Those glyph paths are 2-point
# strokes just like a connector, so they must be removed before anything is counted.
STROKED = re.compile(r'<g class="stroked-text">.*?</g>', re.S)
PAIR = re.compile(r'(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)')


def structure_of(svg_text):
    """Count boxes / connectors / labels in a KiCad SVG export.

    boxes      = a closed path with four axis-aligned edges (KiCad writes a rect as `M .. L .. L .. L .. Z`)
    connectors = an open path with exactly two points
    curves     = anything with an arc/bezier command (a circle or an arc - balls, pockets)
    labels     = <text> elements with non-empty content
    """
    body = STROKED.sub('', svg_text)
    boxes = connectors = curves = other = 0
    for attrs in re.findall(r'<path([^>]*)>', body, re.S):
        m = re.search(r'\bd="([^"]*)"', attrs, re.S)
        if not m:
            continue
        d = m.group(1)
        pts = [(float(x), float(y)) for x, y in PAIR.findall(d)]
        if re.search(r'[ACQacq]', d):
            curves += 1
            continue
        if d.strip().endswith('Z') and len(pts) >= 4:
            if pts[0] == pts[-1]:
                pts = pts[:-1]
            edges = list(zip(pts, pts[1:] + pts[:1]))
            if len(pts) == 4 and all(abs(a[0] - b[0]) < 0.01 or abs(a[1] - b[1]) < 0.01 for a, b in edges):
                boxes += 1
            else:
                other += 1
        elif len(pts) == 2:
            connectors += 1
        else:
            other += 1
    texts = [' '.join(t.split()) for t in re.findall(r'<text[^>]*>(.*?)</text>', body, re.S)]
    texts = [t for t in texts if t]
    return {'boxes': boxes, 'connectors': connectors, 'curves': curves, 'other': other,
            'labels': len(texts), 'text_values': texts}


# which measured field explains each check_geometry_v2 failing id, so feedback can carry the number
GEOM_MEASURED = {
    'table_2to1': ('aspect',),
    'pockets_long_rail': ('long_rail_side_pockets', 'corners', 'short_rail_side_pockets'),
    'rack_apex': ('apex_on_centre',),
    'rack_back_row': ('back_row_parallel',),
    'cue_ball_spot': ('cue_ball', 'cue_ball_on_spot'),
    'cue_angle': ('cue_deg',),
    'label_CUE': ('label_CUE',),
}


def evaluate(svg_path, labels, expect=None):
    """Every check the export can be held to, as id -> {ok, measured, expected}. `failed` is the retry trigger."""
    svg_path = Path(svg_path)
    st = structure_of(svg_path.read_text(encoding='utf-8'))
    checks = {}

    def add(cid, ok, measured, expected):
        checks[cid] = {'ok': bool(ok), 'measured': measured, 'expected': expected}

    for field in ('boxes', 'connectors', 'labels'):
        exact = (expect or {}).get(field + '_exact')
        least = (expect or {}).get(field + '_at_least')
        if exact is not None:
            add('struct.' + field, st[field] == exact, st[field], exact)
        elif least is not None:
            add('struct.' + field, st[field] >= least, st[field], 'at least %d' % least)

    for label in labels:
        hit = any(label == t or label in t for t in st['text_values'])
        add('label.' + label, hit, hit, True)

    geom = check_geometry_v2.analyse(str(svg_path))
    applicable = 'error' not in geom
    if applicable:
        for cid, ok in (geom.get('pass') or {}).items():
            measured = {k: geom[k] for k in GEOM_MEASURED.get(cid, ()) if k in geom}
            add('geom.' + cid, ok, measured, True)
    return {'structure': st, 'checks': checks, 'failed': {k: v for k, v in checks.items() if not v['ok']},
            'geometry_applicable': applicable,
            'geometry_note': None if applicable else geom.get('error')}


def feedback(failed, crashed):
    """The text appended to the next prompt. A crash and a wrong drawing need different information."""
    if crashed:
        return ('\n\nYour previous attempt failed when run. Fix it and return the complete corrected script. '
                'Error was:\n' + crashed)
    if not failed:
        return ''
    lines = ['\n\nYour previous script RAN and wrote a board, so the syntax is fine and the drawing is not.',
             'The exported SVG was measured against the required output and these checks failed:']
    for cid, v in failed.items():
        lines.append('  - %s: required %s, measured %s' % (cid, json.dumps(v['expected']),
                                                           json.dumps(v['measured'])))
    lines.append('Return the complete script again, changing only what is needed so each of those measured '
                 'values matches. Keep the helpers and every part that already passed.')
    return '\n'.join(lines)


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


def build(pcb, txt, dark, labels, expect=None, pad=1.0):
    """Outline -> export -> recolour -> measure. This is the whole post-processing path, shared by the model loop
    and by `--dry-run`, so a fixture exercises exactly what a model script goes through."""
    box = post_outline.add_outline(pcb, pad=pad)
    rc, err = post_outline.export_svg(pcb, txt)
    rec = {'outline_mm': box.get('outline_mm'), 'drawings': box.get('drawings'), 'export_rc': rc,
           'export_error': (err or '').strip()[-200:]}
    if rc == 0 and txt.exists():
        recolour(txt, dark)
        v = evaluate(txt, labels, expect)
        rec.update({'structure': v['structure'], 'checks': v['checks'], 'failed': v['failed'],
                    'geometry_applicable': v['geometry_applicable'],
                    'geometry_note': v['geometry_note'], 'source_score': score_svg(txt, labels),
                    'rendered_for_judging': dark.name})
    return rec


def verdict_line(rec):
    """One line a human can read: the structure counts, then which checks failed."""
    st = rec.get('structure') or {}
    head = 'boxes=%s connectors=%s labels=%s curves=%s' % (st.get('boxes'), st.get('connectors'),
                                                           st.get('labels'), st.get('curves'))
    failed = rec.get('failed') or {}
    tail = 'PASS' if not failed else 'FAIL ' + ', '.join('%s (%s vs %s)' % (k, json.dumps(v['measured']),
                                                                           v['expected'])
                                                         for k, v in failed.items())
    geom = '' if rec.get('geometry_applicable') else '  [geometry checker: %s]' % rec.get('geometry_note')
    return '%s -> %s%s' % (head, tail, geom)


def dry_run(fixture, pad=1.0):
    """Run a known fixture script through the real pipeline. No model, no network."""
    import cad_fixtures                                   # lazy: cad_fixtures imports this module back

    WORK.mkdir(exist_ok=True)
    spec = cad_fixtures.FIXTURES[fixture]
    script = cad_fixtures.write(WORK / ('fixture_%s.py' % fixture), spec)
    pcb = WORK / ('fixture_%s.kicad_pcb' % fixture)
    for p in (pcb, WORK / ('fixture_%s.svg' % fixture), WORK / ('fixture_%s-black.svg' % fixture)):
        p.unlink(missing_ok=True)
    rc, out, err, run_s = run_script(script)
    row = {'fixture': fixture, 'model': None, 'model_contacted': False, 'script': str(script.name),
           'exit': rc, 'board_written': pcb.exists(), 'run_s': run_s,
           'stderr_tail': (err or '').strip()[-300:]}
    if pcb.exists():
        row.update(build(pcb, WORK / ('fixture_%s.svg' % fixture),
                         WORK / ('fixture_%s-black.svg' % fixture), spec['labels'], spec['expect'], pad=pad))
        row['rendered_for_judging'] = 'fixture_%s-black.svg' % fixture
    row['verdict'] = verdict_line(row)
    (WORK / 'fixture-report.json').write_text(json.dumps(row, indent=1), encoding='utf-8')
    print(json.dumps({'fixture': fixture, 'script': script.name, 'exit': rc,
                      'board_written': row['board_written'], 'outline_mm': row.get('outline_mm'),
                      'geometry_applicable': row.get('geometry_applicable'),
                      'geometry_note': row.get('geometry_note')}, indent=1))
    print('checks: %s' % json.dumps(row.get('checks'), indent=1))
    print('verdict: %s' % row['verdict'])
    print('model contacted: no (fixture script only); report -> %s' % (WORK / 'fixture-report.json'))
    return 0 if not (row.get('failed') or row.get('export_rc')) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='qwen3-coder:30b')
    ap.add_argument('--prompts', default='flow-1,circuit-1,blueprint-1')
    ap.add_argument('--task', default=None, help='JSON file with prompt+labels instead of corpus ids')
    ap.add_argument('--attempts', type=int, default=3)
    ap.add_argument('--no-example', action='store_true', help='bare spec: no worked pcbnew template')
    ap.add_argument('--pad', type=float, default=1.0, help='mm of Edge.Cuts padding around the drawing')
    ap.add_argument('--tag', default='', help='suffix for the artifact names, so a re-run does not clobber a '
                                              'recorded one (the old arm always overwrote kicad/<id>*)')
    ap.add_argument('--out', default='result-method-e-cad.json', help='where to write the result rows')
    ap.add_argument('--dry-run', action='store_true', help='no model: run a cad_fixtures script through the pipeline')
    ap.add_argument('--fixture', nargs='?', const='good', default=None,
                    help='which cad_fixtures script to dry-run (default: good)')
    args = ap.parse_args()

    if args.dry_run or args.fixture:
        return dry_run(args.fixture or 'good', pad=args.pad)

    expect_all = json.loads((ROOT / 'structure-expectations.json').read_text(encoding='utf-8'))['expect']
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
        expect = expect_all.get(pid)
        stem = pid + ('-' + args.tag if args.tag else '') + ('-bare' if args.no_example else '')
        pcb = WORK / ('%s.kicad_pcb' % stem)
        txt = WORK / ('%s.svg' % stem)
        dark = WORK / ('%s-black.svg' % stem)
        attempts = []
        clean = False
        fix = ''
        rec = {}
        for attempt in range(1, args.attempts + 1):
            if args.no_example:
                task = ('Write a python script that builds a KiCad board and draws this diagram on the silkscreen '
                        'layer (F.SilkS) using pcbnew:\n\n' + prompt['prompt'] +
                        '\n\nRequired text labels, each as its own pcbnew.PCB_TEXT: ' + ', '.join(labels) +
                        '.\nFinish with pcbnew.SaveBoard(r\'' + str(pcb) + '\', board). Output only the script.')
            else:
                task = TASK.format(prompt=prompt['prompt'], labels=', '.join(labels), path=str(pcb), version='10.0.6')
            if fix:
                task += fix
            # a retry must change the INFORMATION, not just the temperature: at temperature 0 one seed
            # reproduces the same sample exactly (RESULTS-METHOD-E-LLM.md, harness B: 0/9 twice)
            reply, gen_s = ask(args.model, task, seed=31337 + 101 * attempt, temperature=0.0 if attempt == 1 else 0.4)
            script = WORK / ('%s_try%d.py' % (stem, attempt))
            script.write_text(code_of(reply), encoding='utf-8')
            for p in (pcb, txt):                              # so `wrote` describes THIS attempt only
                p.unlink(missing_ok=True)
            rc, out, err, run_s = run_script(script)
            wrote = pcb.exists()
            rec = build(pcb, txt, dark, labels, expect, pad=args.pad) if wrote else {}
            failed = rec.get('failed') or {}
            crash = (err or '').strip()[-600:]
            if rec.get('export_rc'):
                crash = (crash + '\nthe SVG export failed: ' + str(rec.get('export_error')))[-600:]
            attempts.append({'attempt': attempt, 'gen_s': gen_s, 'run_s': run_s, 'exit': rc,
                             'board_written': wrote, 'outline_mm': rec.get('outline_mm'),
                             'checks_failed': list(failed),
                             'geometry_applicable': rec.get('geometry_applicable'),
                             'error': crash.splitlines()[-1] if crash else ''})
            print('  %-13s attempt %d: exit %s board %s outline %s %s' % (
                pid, attempt, rc, wrote, rec.get('outline_mm'),
                verdict_line(rec) if rec else 'no board'), flush=True)
            clean = rc == 0 and wrote and not failed and not rec.get('export_rc')
            if clean:
                break
            fix = feedback(failed, crash)
        row = {'id': pid, 'attempts': attempts, 'script_clean': clean}
        row.update({k: v for k, v in (rec or {}).items() if k != 'checks'})
        row['checks'] = (rec or {}).get('checks')
        rows.append(row)
        print('  %-13s -> %s' % (pid, json.dumps({k: v for k, v in row.items() if k not in ('attempts', 'checks')})[:260]),
              flush=True)

    out = Path(args.out)
    out = out if out.is_absolute() else ROOT / out
    out.write_text(json.dumps({'arm': 'E-kicad', 'model': args.model, 'rows': rows}, indent=1), encoding='utf-8')
    clean = sum(1 for r in rows if r['script_clean'])
    labelled = sum(1 for r in rows if r.get('source_score') and
                   r['source_score']['labels_present'] == r['source_score']['labels_total'])
    print('\nscripts running cleanly AND passing their checks: %d/%d; all labels present in source: %d/%d'
          % (clean, len(rows), labelled, len(rows)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
