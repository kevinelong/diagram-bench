"""cad_fixtures.py - known scripts for the method-E arm, and a self-test that asserts their verdicts.

The arm is only trustworthy if a script that *runs* and draws the wrong thing is caught, because that is the
failure mode KiCad hides: RESULTS-METHOD-E-LLM.md records a model script that exited 1 and still wrote a correct
board (`print OK` after `SaveBoard`), and the reverse - a clean exit over a wrong drawing - is the one the old
retry loop scored as success.

Fixtures (each is a complete `pcbnew` script in the same helper style the arm's prompt teaches):

  good   four boxes, three connectors, four labels - the `flow-1` specification, exact counts
  bad    three boxes, two connectors, and STORE omitted - must fail struct.boxes, struct.connectors, label.STORE
  crash  the `good` drawing, then `print OK` (py2 syntax, a NameError in py3) - exit 1 over a valid board

Plus two hand-written pool-table SVGs (`POOL_GOOD`, `POOL_BAD`) used to prove that `check_geometry_v2`'s check ids
and measured values really do reach the retry prompt: the bad one puts the side pockets on the short rails, the
exact v1 fault recorded in RESULTS-SHAPE-CHECKS.md, and must fail `pockets_long_rail` and nothing else.

`--selftest` generates the boards with KiCad's Python, exports them through the arm's own post-processing path
(`cad_arm.build`, which imports this module - hence the lazy imports below), and asserts every verdict, including
the known-bad ones. It needs no model and no network.

Usage (cmd.exe):
  .venv\\Scripts\\python.exe cad_fixtures.py --selftest
  .venv\\Scripts\\python.exe cad_arm.py --dry-run --fixture good|bad|crash
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FLOW_LABELS = ['START', 'VALIDATE', 'STORE', 'DONE']
FLOW_EXPECT = {'boxes_exact': 4, 'connectors_exact': 3, 'labels_exact': 4}

HEADER = """import pcbnew
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
"""

GOOD = HEADER + """
# four boxes, top to bottom
rect(35, 4, 20, 8)      # START
rect(35, 16, 20, 8)     # VALIDATE
rect(35, 28, 20, 8)     # STORE
rect(35, 40, 20, 8)     # DONE

# three vertical connectors between them
seg(45, 12, 45, 16)
seg(45, 24, 45, 28)
seg(45, 36, 45, 40)

# the four labels, centred in their boxes
text(45, 8, "START")
text(45, 20, "VALIDATE")
text(45, 32, "STORE")
text(45, 44, "DONE")

pcbnew.SaveBoard(r'{path}', board)
"""

BAD = HEADER + """
# three boxes (STORE is missing), two connectors, three labels
rect(35, 4, 20, 8)      # START
rect(35, 16, 20, 8)     # VALIDATE
rect(35, 40, 20, 8)     # DONE

seg(45, 12, 45, 16)
seg(45, 24, 45, 40)     # one long connector where two were required

text(45, 8, "START")
text(45, 20, "VALIDATE")
text(45, 44, "DONE")

pcbnew.SaveBoard(r'{path}', board)
"""

CRASH = GOOD.rstrip() + """

# the recorded failure mode: the board is already saved, and THEN the script dies
print(OK)
"""

FIXTURES = {
    'good': {'script': GOOD, 'labels': FLOW_LABELS, 'expect': FLOW_EXPECT,
             'note': 'flow-1 exactly: 4 boxes, 3 connectors, 4 labels'},
    'bad': {'script': BAD, 'labels': FLOW_LABELS, 'expect': FLOW_EXPECT,
            'note': '3 boxes, 2 connectors, STORE missing - every structural check must fail'},
    'crash': {'script': CRASH, 'labels': FLOW_LABELS, 'expect': FLOW_EXPECT,
              'note': 'correct drawing, then `print OK` (NameError): exit 1 over a valid board'},
}

# ------------------------------------------------------------------ hand-written pool-table SVGs
# Only these exercise check_geometry_v2, which reads <rect>/<line>/<circle>. A KiCad export writes <path>, so it
# cannot see a KiCad board at all - see cad_arm.evaluate's geometry_note.

def _pool(side_pockets):
    corners = ''.join('<circle cx="%s" cy="%s" r="18"/>' % (x, y)
                      for x, y in ((20, 20), (1004, 20), (20, 492), (1004, 492)))
    sides = ''.join('<circle cx="%s" cy="%s" r="15"/>' % (x, y) for x, y in side_pockets)
    cue = '<circle cx="266" cy="256" r="11"/>'
    rack = ''.join('<circle cx="%s" cy="%s" r="11"/>' % (round(450 + k * 22.4, 1), round(256 + (j - k / 2) * 22.4, 1))
                   for k in range(5) for j in range(k + 1))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="512" viewBox="0 0 1024 512">'
            '<rect x="20" y="20" width="984" height="472" fill="none" stroke="black"/>'
            + corners + sides + cue + rack +
            '<line x1="150" y1="300" x2="250" y2="340" stroke="black"/>'
            '<text x="120" y="80" font-size="28">CUE</text></svg>')


POOL_GOOD = _pool([(512, 20), (512, 492)])      # side pockets on the long rails: correct
POOL_BAD = _pool([(20, 256), (1004, 256)])      # side pockets on the short rails: the v1 fault


def write(path, spec):
    """Materialise a fixture script at `path`. Returns the Path."""
    path = Path(path)
    path.write_text(spec['script'].format(path=str(path.with_suffix('.kicad_pcb'))), encoding='utf-8')
    return path


# ------------------------------------------------------------------ self-test

def counts_of(pcb_path):
    """gr_* element counts straight off the file, so the assertion does not depend on the arm's own parser."""
    text = Path(pcb_path).read_text(encoding='utf-8')
    return {k: len(re.findall(r'\(\s*gr_%s\b' % k, text)) for k in ('rect', 'line', 'text', 'circle')}


def run(pcb_script):
    import cad_arm
    rc, out, err, secs = cad_arm.run_script(pcb_script)
    return rc, err


class Checker:
    def __init__(self):
        self.n = self.bad = 0

    def check(self, name, cond, detail=''):
        self.n += 1
        if not cond:
            self.bad += 1
        print('%s %-58s %s' % ('PASS' if cond else 'FAIL', name, detail))
        return cond


def selftest(keep=None):
    import cad_arm                                    # lazy: cad_arm imports this module for --dry-run

    ck = Checker()
    work = Path(keep) if keep else Path(tempfile.mkdtemp(prefix='cad-fixtures-'))
    work.mkdir(parents=True, exist_ok=True)
    print('work dir: %s' % work)

    # the fixture's frozen expectations must be the same numbers the corpus already froze
    frozen = json.loads((ROOT / 'structure-expectations.json').read_text(encoding='utf-8'))['expect']['flow-1']
    ck.check('fixture expect == structure-expectations.json flow-1', FLOW_EXPECT == frozen,
             json.dumps(frozen))
    corpus = json.loads((ROOT / 'prompts-diagrams.json').read_text(encoding='utf-8'))['prompts']
    flow = [p for p in corpus if p['id'] == 'flow-1'][0]
    ck.check('fixture labels == prompts-diagrams.json flow-1', FLOW_LABELS == flow['labels'], str(flow['labels']))

    # ---- good fixture: element counts, then the arm's verdict
    spec = FIXTURES['good']
    script = write(work / 'good.py', spec)
    pcb = script.with_suffix('.kicad_pcb')
    rc, err = run(script)
    ck.check('good: script exits 0', rc == 0, (err or '').strip().splitlines()[-1:] or 'clean')
    ck.check('good: board written', pcb.exists(), pcb.name)
    cnt = counts_of(pcb)
    ck.check('good: gr_rect == 4', cnt['rect'] == 4, cnt)
    ck.check('good: gr_line == 3', cnt['line'] == 3, cnt)
    ck.check('good: gr_text == 4', cnt['text'] == 4, cnt)

    rec = cad_arm.build(pcb, work / 'good.svg', work / 'good-black.svg', spec['labels'], spec['expect'])
    st = rec['structure']
    ck.check('good: export succeeds', rec['export_rc'] == 0, rec.get('export_error') or 'rc 0')
    ck.check('good: outline recorded', rec['outline_mm'] and rec['outline_mm'][0] > 20 and rec['outline_mm'][1] > 40,
             rec['outline_mm'])
    import post_outline
    vb = [float(v) for v in re.search(r'viewBox="([^"]+)"', (work / 'good.svg').read_text(encoding='utf-8')).group(1).split()]
    ck.check('good: the export fills its viewBox (the drawing is not a speck on a page)',
             abs(vb[2] - rec['outline_mm'][0]) <= 0.05 and abs(vb[3] - rec['outline_mm'][1]) <= 0.05,
             'viewBox %.2f x %.2f mm vs outline %s' % (vb[2], vb[3], rec['outline_mm']))
    box2 = post_outline.add_outline(pcb, pad=1.0)          # a second pass replaces, never doubles, the outline
    edges = sum(1 for it in post_outline.readings(pcb.read_text(encoding='utf-8'))
                if it['layer'] == post_outline.EDGE)
    ck.check('good: add_outline is idempotent', box2['outline_mm'] == rec['outline_mm'] and edges == 1,
             '%d Edge.Cuts drawing(s), %s' % (edges, box2['outline_mm']))
    ck.check('good: struct boxes/connectors/labels = 4/3/4',
             (st['boxes'], st['connectors'], st['labels']) == (4, 3, 4), st)
    ck.check('good: no failed checks', rec['failed'] == {}, json.dumps(rec['failed']))
    ck.check('good: all 7 checks pass',
             all(v['ok'] for v in rec['checks'].values()) and len(rec['checks']) == 7, len(rec['checks']))
    ck.check('good: geometry checker recorded as inapplicable (KiCad writes <path>)',
             rec['geometry_applicable'] is False and rec['geometry_note'] == 'no table found', rec['geometry_note'])
    dark = (work / 'good-black.svg').read_text(encoding='utf-8')
    ck.check('good: CSS recoloured to black', '#F2EDA1' not in dark and 'stroke:#000000' in dark,
             'F2EDA1 present' if '#F2EDA1' in dark else 'black')

    # ---- bad fixture: the exact checks that must fail, and the retry text that carries them
    spec = FIXTURES['bad']
    script = write(work / 'bad.py', spec)
    pcb = script.with_suffix('.kicad_pcb')
    rc, err = run(script)
    cnt = counts_of(pcb)
    ck.check('bad: script also exits 0 (a running script can still be wrong)', rc == 0, cnt)
    ck.check('bad: gr_rect/gr_line/gr_text = 3/2/3',
             (cnt['rect'], cnt['line'], cnt['text']) == (3, 2, 3), cnt)
    rec = cad_arm.build(pcb, work / 'bad.svg', work / 'bad-black.svg', spec['labels'], spec['expect'])
    ck.check('bad: exactly boxes, connectors, labels and label.STORE fail',
             sorted(rec['failed']) == ['label.STORE', 'struct.boxes', 'struct.connectors', 'struct.labels'],
             sorted(rec['failed']))
    fix = cad_arm.feedback(rec['failed'], '')
    ck.check('bad: retry prompt names the failing ids', all(k in fix for k in rec['failed']), fix.splitlines()[2])
    ck.check('bad: retry prompt carries measured values',
             'struct.boxes: required 4, measured 3' in fix and 'label.STORE: required true, measured false' in fix,
             fix.splitlines()[3].strip())

    # ---- crash fixture: exit status is not evidence about the artifact
    spec = FIXTURES['crash']
    script = write(work / 'crash.py', spec)
    pcb = script.with_suffix('.kicad_pcb')
    rc, err = run(script)
    cnt = counts_of(pcb)
    ck.check('crash: exit code is nonzero', rc != 0, err.strip().splitlines()[-1:] or 'exit %s' % rc)
    ck.check('crash: board exists anyway', pcb.exists() and cnt['rect'] == 4, cnt)
    rec = cad_arm.build(pcb, work / 'crash.svg', work / 'crash-black.svg', spec['labels'], spec['expect'])
    ck.check('crash: the drawing still passes its checks', rec['failed'] == {}, json.dumps(rec['failed']))
    fix = cad_arm.feedback({}, 'NameError: name \'OK\' is not defined')
    ck.check('crash: retry prompt carries the error, not a structure list', 'NameError' in fix and 'struct.' not in fix,
             fix.splitlines()[1][:48])

    # ---- geometry checks reaching the retry prompt (check_geometry_v2 ids)
    import check_geometry_v2
    good_svg = work / 'pool-good.svg'
    bad_svg = work / 'pool-bad.svg'
    good_svg.write_text(POOL_GOOD, encoding='utf-8')
    bad_svg.write_text(POOL_BAD, encoding='utf-8')
    g = check_geometry_v2.analyse(str(good_svg))
    b = check_geometry_v2.analyse(str(bad_svg))
    ck.check('pool good: 7/7', g.get('score') == '7/7', g.get('pass'))
    ck.check('pool bad: only pockets_long_rail fails',
             sorted(k for k, v in b['pass'].items() if not v) == ['pockets_long_rail'], b.get('pass'))
    v = cad_arm.evaluate(good_svg, ['CUE'])
    ck.check('pool good: arm reports no failed checks', v['failed'] == {}, json.dumps(v['failed']))
    ck.check('pool good: geometry layer applicable', v['geometry_applicable'] is True, None)
    v = cad_arm.evaluate(bad_svg, ['CUE'])
    ck.check('pool bad: arm reports geom.pockets_long_rail only',
             list(v['failed']) == ['geom.pockets_long_rail'], list(v['failed']))
    fix = cad_arm.feedback(v['failed'], '')
    ck.check('pool bad: retry prompt carries the geometry ids and measured counts',
             'geom.pockets_long_rail' in fix and 'short_rail_side_pockets' in fix, fix.splitlines()[3])

    # ---- `--dry-run` must not contact the model: trip a wire on ask() and run the real dry-run path
    import contextlib
    import io
    def tripwire(*a, **k):
        raise RuntimeError('Ollama was contacted by --dry-run')
    real_ask, cad_arm.ask = cad_arm.ask, tripwire
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = cad_arm.dry_run('good', pad=1.0)
    except RuntimeError as e:
        rc = e
    finally:
        cad_arm.ask = real_ask
    out = buf.getvalue()
    ck.check('dry-run: completes without calling ask()', rc == 0, 'ask() tripwire never tripped')
    ck.check('dry-run: prints the checker verdict for the fixture board',
             'verdict: boxes=4 connectors=3 labels=4 curves=0 -> PASS' in out, out.strip().splitlines()[-2])

    # ---- the retry loop itself: a script that RUNS but draws wrong geometry must now retry, and the retry
    #      prompt must carry the failed ids. This replaces ask() with the two fixture scripts, so no model runs.
    prompts_seen = []
    def fake_ask(model, prompt, timeout=600, seed=31337, temperature=0):
        prompts_seen.append(prompt)
        body = FIXTURES['bad'] if len(prompts_seen) == 1 else FIXTURES['good']
        target = re.search(r"SaveBoard\(r'([^']+)'", prompt).group(1)
        return body['script'].format(path=target), 0.0
    real_ask, cad_arm.ask = cad_arm.ask, fake_ask
    result = work / 'loop-result.json'
    buf = io.StringIO()
    real_argv, sys.argv = sys.argv, ['cad_arm.py', '--prompts', 'flow-1', '--attempts', '2',
                                     '--tag', 'selftest', '--out', str(result)]
    try:
        with contextlib.redirect_stdout(buf):
            cad_arm.main()
    finally:
        cad_arm.ask = real_ask
        sys.argv = real_argv
    rows = json.loads(result.read_text(encoding='utf-8'))['rows']
    row = rows[0]
    ck.check('loop: the wrong-but-running first attempt did not end the run',
             len(row['attempts']) == 2, [a['checks_failed'] for a in row['attempts']])
    ck.check('loop: attempt 1 recorded the failed check ids',
             'struct.boxes' in row['attempts'][0]['checks_failed'], row['attempts'][0]['checks_failed'])
    ck.check('loop: attempt 2 ran clean and passed its checks', row['script_clean'] is True and
             row['attempts'][1]['checks_failed'] == [], row['attempts'][1]['checks_failed'])
    ck.check('loop: the retry prompt carried the ids and measured values',
             'struct.boxes: required 4, measured 3' in prompts_seen[1], 'attempt 2 prompt grew by %d chars'
             % (len(prompts_seen[1]) - len(prompts_seen[0])))
    ck.check('loop: outline_mm recorded per row', row['outline_mm'] and row['outline_mm'][0] > 20, row['outline_mm'])
    for p in ROOT.joinpath('kicad').glob('flow-1-selftest*'):
        p.unlink()

    print('\nSELFTEST %s %d/%d assertions' % ('OK' if ck.bad == 0 else 'FAILED', ck.n - ck.bad, ck.n))
    return 0 if ck.bad == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description='fixture pcbnew scripts for the method-E arm')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--keep', default=None, help='leave the generated boards in this directory')
    ap.add_argument('--list', action='store_true')
    args = ap.parse_args(argv)
    if args.list:
        for k, v in FIXTURES.items():
            print('%-6s %s' % (k, v['note']))
        return 0
    if args.selftest:
        return selftest(args.keep)
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
