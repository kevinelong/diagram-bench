"""final_v6.py - the single end-to-end check after the merges: one generation, one score.

Loads the frozen v5 prompt through the merged prompt suite, asks the model once, and scores with the merged
checker. Deliberately one call: the lab's one-workload rule applies to integration too.
"""
import json, re, sys, time, urllib.request
from pathlib import Path
from prompts.suite import load
from check_ace_geometry import analyse

ROOT = Path(__file__).resolve().parent
SYSTEM = 'You are a technical illustrator. Output one complete SVG document, no fences, no commentary.'
NOFENCE = ('\n\nOutput the raw SVG text itself, starting directly with <svg and ending with </svg>. '
           'Do not use markdown code fences.')


def main():
    task = load(6)
    body = {'model': 'qwen3-coder:30b', 'system': SYSTEM, 'prompt': task.prompt + NOFENCE,
            'stream': False, 'options': {'temperature': 0, 'seed': 20261021, 'num_predict': 8192}}
    req = urllib.request.Request('http://127.0.0.1:11434/api/generate', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.monotonic()
    reply = json.load(urllib.request.urlopen(req, timeout=900)).get('response', '')
    secs = round(time.monotonic() - t0, 1)
    m = re.search(r'<svg[\s\S]*?</svg>', reply, re.I)
    out = ROOT / 'hard'
    out.mkdir(exist_ok=True)
    if not m:
        print('no <svg> in %d chars after %.1fs' % (len(reply), secs))
        return 1
    path = out / 'pooltable-v6-bare.svg'
    path.write_text(m.group(0), encoding='utf-8')
    r = analyse(str(path))
    (ROOT / 'result-hard-v6.json').write_text(json.dumps(dict(r, gen_s=secs), indent=1), encoding='utf-8')
    print('generated in %.1fs -> %s' % (secs, r['score']))
    for k, ok in r['pass'].items():
        print('  %-18s %s' % (k, 'ok' if ok else 'FAIL'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
