"""ref_probe.py - can this engine take more than one reference image?

The question is asked with a picture, not a doc: two reference pictures that cannot be confused (a red circle on paper,
a blue square on paper) are fed to the same prompt at 0, 1 and 2 references. An engine that ignores a reference returns
the same picture for 1 and 2 - the difference between the runs is the measurement.

    python tools/ref_probe.py --profile "z-image turbo"
    python tools/ref_probe.py --profile "flux2 klein 9b uncensored" --steps 4

The graph is `zimage_gen.workflow()` unchanged, except that the positive conditioning is passed through one
`ReferenceLatent` node per reference - the node that sets a guiding latent for an edit model, and whose own description
says chaining several of them sets several references.
"""

import argparse
import io
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import zimage_gen as zg                                          # noqa: E402
from PIL import Image, ImageDraw                                 # noqa: E402

PROMPT = ('two objects from the reference pictures arranged side by side on white paper, '
          'a red ring and a blue square, flat lay, photograph')


def make_references(folder):
    """Two references no model could confuse, and neither of which is the requested output."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    red = folder / 'ref-red-circle.png'
    im = Image.new('RGB', (512, 512), 'white')
    ImageDraw.Draw(im).ellipse((110, 110, 402, 402), outline=(200, 20, 20), width=34)
    im.save(red)
    blue = folder / 'ref-blue-square.png'
    im = Image.new('RGB', (512, 512), 'white')
    ImageDraw.Draw(im).rectangle((120, 120, 392, 392), outline=(20, 40, 200), width=34)
    im.save(blue)
    return red, blue


def graph_with_refs(profile, prompt, refs, width=512, height=512, steps=4, seed=1234):
    """The normal text-to-image graph, with the positive conditioning passing through one ReferenceLatent per image."""
    prof = zg.MODELS[profile]
    graph = zg.workflow(prompt, width, height, steps, prof.get('cfg', 1.0), seed, 'refprobe',
                        negative='', profile=profile)
    positive = ['4', 0]
    for i, name in enumerate(refs):
        load, enc, ref = str(20 + 3 * i), str(21 + 3 * i), str(22 + 3 * i)
        graph[load] = {'class_type': 'LoadImage', 'inputs': {'image': name}}
        graph[enc] = {'class_type': 'VAEEncode', 'inputs': {'pixels': [load, 0], 'vae': ['3', 0]}}
        graph[ref] = {'class_type': 'ReferenceLatent', 'inputs': {'conditioning': positive, 'latent': [enc, 0]}}
        positive = [ref, 0]
    graph['7']['inputs']['positive'] = positive
    if '15' in graph:
        graph['15']['inputs']['positive'] = positive
    return graph


def post(host, path, payload):
    req = urllib.request.Request(host + path, data=json.dumps(payload).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


def run(host, graph, timeout=900):
    """Submit and wait. Returns (ok, detail, image_bytes|None)."""
    try:
        pid = post(host, '/prompt', {'prompt': graph})['prompt_id']
    except urllib.error.HTTPError as e:
        return False, 'rejected: %s' % e.read().decode('utf-8', 'replace')[:400], None
    started = time.time()
    while time.time() - started < timeout:
        hist = json.loads(urllib.request.urlopen(host + '/history/' + pid, timeout=60).read())
        if pid in hist:
            entry = hist[pid]
            status = entry.get('status') or {}
            if status.get('status_str') == 'error' or status.get('completed') is False:
                msgs = [m for m in (status.get('messages') or []) if m[0] == 'execution_error']
                detail = msgs[-1][1].get('exception_message', '')[:300] if msgs else str(status)[:300]
                return False, 'workflow failed: %s' % detail, None
            outputs = entry.get('outputs') or {}
            for node in outputs.values():
                for img in node.get('images') or []:
                    q = urllib.parse.urlencode({'filename': img['filename'], 'subfolder': img.get('subfolder', ''),
                                                'type': img.get('type', 'output')})
                    return True, 'ok', urllib.request.urlopen(host + '/view?' + q, timeout=120).read()
            return True, 'ok (no image in outputs)', None
        time.sleep(0.5)
    return False, 'timeout after %d s' % timeout, None


def difference(a, b):
    """Share of pixels that differ by more than a visible margin - 0 means the second run is the same picture."""
    if not a or not b:
        return None
    ia, ib = Image.open(io.BytesIO(a)).convert('RGB'), Image.open(io.BytesIO(b)).convert('RGB')
    if ia.size != ib.size:
        return 1.0
    pa, pb = ia.load(), ib.load()
    w, h = ia.size
    changed = sum(1 for y in range(0, h, 4) for x in range(0, w, 4)
                  if max(abs(pa[x, y][c] - pb[x, y][c]) for c in range(3)) > 12)
    return changed / float(len(range(0, h, 4)) * len(range(0, w, 4)))


def main(argv=None):
    ap = argparse.ArgumentParser(description='Does this engine accept more than one reference image?')
    ap.add_argument('--profile', default='flux2 klein 9b uncensored', choices=sorted(zg.MODELS))
    ap.add_argument('--host', default='http://127.0.0.1:8006')
    ap.add_argument('--steps', type=int, default=0, help='0 uses the profile ceiling')
    ap.add_argument('--seed', type=int, default=1234)
    ap.add_argument('--out', default=str(Path(HERE.parent / 'refprobe-out')))
    args = ap.parse_args(argv)

    host = args.host.rstrip('/')
    steps = args.steps or zg.MODELS[args.profile]['max_steps']
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    red, blue = make_references(out)
    refs = [zg.upload_image(host, red), zg.upload_image(host, blue)]
    print('references uploaded: %s' % refs)

    shots = {}
    for label, chosen in (('none', []), ('one', refs[:1]), ('two', refs[:2])):
        graph = graph_with_refs(args.profile, PROMPT, chosen, steps=steps, seed=args.seed)
        ok, detail, image = run(host, graph)
        print('%-5s %-34s %s' % (label, 'ok' if ok else 'FAILED', '' if ok else detail[:220]))
        if ok and image:
            path = out / ('%s-%s.png' % (args.profile.replace(' ', '_').replace('/', '_'), label))
            path.write_bytes(image)
            shots[label] = image
    if len(shots) == 3:
        d1 = difference(shots['none'], shots['one'])
        d2 = difference(shots['none'], shots['two'])
        d12 = difference(shots['one'], shots['two'])
        print('\ndifference vs no reference: one %.1f%%, two %.1f%%' % (100 * d1, 100 * d2))
        print('difference one vs two     : %.1f%%' % (100 * d12))
        print('verdict: %s' % ('the second reference changes the picture' if d12 > 0.05 else
                               'the second reference changes nothing - this engine takes one image'
                               if d1 > 0.02 else 'references are ignored entirely'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
