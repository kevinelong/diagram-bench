"""hard_v4.py - generate with the v4 task (lattice rack) and score against the app's own constants."""
import argparse, json, re, sys, time, urllib.request
from pathlib import Path
from check_ace_geometry import analyse

ROOT = Path(__file__).resolve().parent
OLLAMA = 'http://127.0.0.1:11434/api/generate'
SYSTEM = 'You are a technical illustrator. You output ONE complete SVG document and nothing else.'


def ask(model, prompt, seed=1234, timeout=900):
    body = {'model': model, 'system': SYSTEM, 'prompt': prompt, 'stream': False,
            'options': {'temperature': 0, 'seed': seed, 'num_predict': 8192}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get('response', ''), round(time.monotonic() - t0, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='qwen3-coder:30b')
    args = ap.parse_args()
    task_file = ROOT / 'hard-task-pooltable-v4.json'
    task = json.loads(task_file.read_text(encoding='utf-8'))
    slug = task_file.stem.replace('hard-task-pooltable-', '')     # v4 -> names derive from the task, not from us
    reply, secs = ask(args.model, task['prompt'])
    m = re.search(r'<svg[\s\S]*?</svg>', reply, re.IGNORECASE)
    out = ROOT / 'hard'
    out.mkdir(exist_ok=True)
    row = {'gen_s': secs}
    if not m:
        row.update({'error': 'no <svg>', 'score': '0/10'})
    else:
        path = out / ('pooltable-%s-bare.svg' % slug)
        path.write_text(m.group(0), encoding='utf-8')
        r = analyse(str(path))
        row.update({k: v for k, v in r.items() if k != 'file'})
        print('  v4 -> %s' % r['score'])
        for k, ok in r['pass'].items():
            print('    %-18s %s' % (k, 'ok' if ok else 'FAIL'))
    (ROOT / ('result-hard-%s.json' % slug)).write_text(json.dumps(row, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
