"""pose_probe.py - which model and denoise best follows a scene/pose instruction without losing the character?

The question this answers: "take this standing figure and make them sit" - which model keeps the face and clothing while
actually changing the pose? Two things have to be true at once, and img2img trades them against each other: too little
denoise keeps the character and leaves the pose alone, too much changes the pose and invents a new person.

Method

  1. one standing figure is generated and used as the base for every trial,
  2. each trial is img2img from it with the same instruction, varying model and denoise,
  3. measurements per trial: pixel distance from the base (how much changed), how long the render took, peak VRAM,
     and a vision judge answering three binary questions - same character? seated? standing? - one question per call,
     same rubric for every arm. The pose is asked twice, direct and inverted, and a contradiction is recorded as such.

A trial "works" only when the judge answers yes to both "same character" and "seated".

Judges

  --judge local (default)  `qwen3-vl:4b` on this machine, so **no figure study leaves the box**. Ollama's generate API
                           takes one image per call, so "same character" becomes the question of whether the person's
                           defining attributes are still there (short red hair, long green coat) on the edit alone,
                           plus - recorded apart, as `identity_pair` - a two-panel comparison with the base on the
                           left. Calibration is not inherited here, it is re-run and stored with the results:
                           `--controls` (on by default) asks the three questions of a standing base, a generated
                           seated reference and a generated different character. 9/9 on 2026-10-27.
                           A first phrasing of the pose question ("sitting on a chair with bent legs, rather than
                           standing upright on both feet") was 5/6 and failed on a plainly seated figure, which is
                           why the questions are short and the answer is cross-checked.
  --judge cloud            `deepseek-flash` (DEEPSEEK_API_KEY) shown both images in one message. Keeps uploading the
                           figure study to api.deepseek.com - use it only to compare against the historic 2026-10-24 run.
  --judge none             generate and measure, judge nothing.

VRAM: the judge costs ~3.3 GiB and the 9B ~8.8 GiB, and together they peak near the card's 12.2 GiB, so the judge is
unloaded (`local_vlm.unload()`) before every generation and reloaded by Ollama when the first question is asked.

    python pose_probe.py --profiles "flux2 klein 9b uncensored" --out pose-ladder-9b
    python pose_probe.py --judge cloud                  # the historic comparison
    python pose_probe.py --denoise 0.4 0.7
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zimage_gen as zg  # noqa: E402
import zimage_ui as zui  # noqa: E402
from winproc import run as winrun  # noqa: E402

API = 'https://api.deepseek.com/v1/chat/completions'
JUDGE_MODEL = 'deepseek-flash'
KEY = os.environ.get('DEEPSEEK_API_KEY')

CHARACTER = ('full body studio photograph of a young woman with short red hair and freckles, wearing a green wool '
             'coat and black boots, standing upright facing the camera, plain white background')
INSTRUCTION = ('the same young woman with short red hair and freckles, wearing the same green wool coat and black boots, '
               'now sitting on a wooden chair, legs bent, same face, plain white background')
# a deliberately different character, for the negative controls: nothing about it overlaps the base's attributes
OTHER = ('full body studio photograph of an elderly bearded man in a navy business suit and brown shoes, wearing '
         'glasses, standing upright facing the camera, plain white background')
# a positive control for the pose question: if the judge cannot call this one seated, it is not measuring the pose
SEATED = ('full body studio photograph of a young woman with short red hair and freckles, wearing a green wool coat, '
          'sitting on a wooden chair, legs bent, plain white background')

JUDGE = ('Two images, 1 then 2. Answer JSON only, no other text: '
         '{"same_character": "yes" or "no", "sitting": "yes" or "no"}. '
         'same_character: image 2 still shows the same person (face, hair, clothing). '
         'sitting: the figure in image 2 is seated, not standing.')

# -- the local judge's questions, one image per call ---------------------------------------------------------------
LOCAL_MODEL = 'qwen3-vl:4b'
# Deliberately the attributes that survive a 512 px render. Asking the same question with "freckles" and "black boots"
# added made the model think past its token budget and return an empty `response` with the reasoning still running
# (measured 2026-10-27: two of six control questions unanswered, both of them the positive ones) - a judge that
# cannot finish is not a stricter judge.
Q_SAME = 'Does this photograph show a young woman with short red hair, wearing a long green coat?'
# The pose question is asked twice, in opposite directions, and a contradiction is recorded rather than resolved.
# A stricter phrasing failed: "sitting on a chair with bent legs, rather than standing upright on both feet" answered
# *no* to a figure that is plainly seated on a wooden chair (4B d=0.70, 2026-10-27), because one boot rests on the
# floor. Both direct forms below scored 6/6 on six hand-labelled renders - the base and the two small denoises
# standing, d=0.70 and d=0.85 seated, and a different character standing.
Q_SIT = 'Is the person in this photograph seated on a chair?'
Q_STAND = 'Is the person in this photograph standing upright on both feet?'
Q_PAIR = ('This image has two panels side by side, the left panel is the original and the right panel is the edit. '
          'Is the person in the right panel the same individual as the person in the left panel - same face and the '
          'same green wool coat?')


def pick(verdict, keys):
    """Read a yes/no out of whatever shape the judge returned. None means it did not answer."""
    for key in keys:
        if key not in verdict:
            continue
        value = verdict[key]
        if isinstance(value, bool) or value is None:
            return value
        text = str(value).strip().lower()
        return text.startswith('y') or text in ('true', '1', 'same', 'yes')
    return None


def b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode('ascii')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def judge_cloud(base_path, edit_path):
    content = [{'type': 'text', 'text': JUDGE + '\n\nFIRST image (original):'},
               {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64(base_path)}},
               {'type': 'text', 'text': 'SECOND image (edit):'},
               {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64(edit_path)}}]
    last = ''
    # the judge spends tokens on reasoning before it answers, so the budget escalates until text comes back
    for budget in (1500, 3000, 6000):
        body = {'model': JUDGE_MODEL, 'temperature': 0, 'max_tokens': budget,
                'messages': [{'role': 'user', 'content': content}]}
        req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json',
                                              'Authorization': 'Bearer ' + KEY})
        try:
            raw = json.loads(urllib.request.urlopen(req, timeout=120).read())
            text = raw['choices'][0]['message']['content'] or ''
            start, end = text.find('{'), text.rfind('}')
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1])
            last = 'empty content (finish=%s)' % raw['choices'][0].get('finish_reason')
        except Exception as e:
            last = '%s: %s' % (type(e).__name__, str(e)[:60])
        if 'finish=length' not in last:
            time.sleep(1.0)
    return {'error': last}


def parse_yes_no(text):
    """First yes/no token in the answer. None means the model did not commit either way."""
    low = (text or '').strip().lower()
    if not low:
        return None
    first = re.search(r'\b(yes|no)\b', low[:60])
    return None if first is None else first.group(1) == 'yes'


def parse_thinking(thinking):
    """The answer stranded in `thinking` when the token budget ran out before the response channel opened.

    A thinking model shares one budget between reasoning and answer, so a long question can end with an empty
    `response` while the reasoning has already concluded - measured on the pair question, where `num_predict` 220 gave
    "" and 400 gave a clean "yes" for the same image. This is a fallback, counted apart from real answers.
    """
    low = (thinking or '').lower()
    last = None
    for m in re.finditer(r'answer (?:should be|is|would be) (yes|no)\b', low):
        last = m.group(1)
    if last is None:
        tail = re.findall(r'\b(yes|no)\b', low[-300:])
        last = tail[-1] if tail else None
    return None if last is None else last == 'yes'


def local_answer(image, question, budgets=(500, 900)):
    """Ask the local judge one binary question. Returns (verdict, seconds, raw, channel).

    `local_vlm.yes_no` is the documented wrapper but its fixed budget of 220 returns None on the longer questions, so
    the budget escalates here and an answer recovered from the reasoning is reported as `channel='thinking'`.
    """
    import local_vlm

    prompt = question.rstrip() + ' Answer with one word: yes or no.'
    spent, raw, thinking = 0.0, '', ''
    for budget in budgets:
        try:
            raw, secs, thinking = local_vlm.ask(image, prompt, num_predict=budget)
        except Exception as e:
            return None, spent, '%s: %s' % (type(e).__name__, str(e)[:80]), 'error'
        spent += secs
        verdict = parse_yes_no(raw)
        if verdict is not None:
            return verdict, round(spent, 1), raw, 'response'
    verdict = parse_thinking(thinking)
    if verdict is not None:
        return verdict, round(spent, 1), (raw or thinking)[-300:], 'thinking'
    return None, round(spent, 1), (raw or thinking)[-300:], 'none'


def pair_image(base_path, edit_path, dest):
    """Base on the left, edit on the right, a black divider between - the one-image form of 'are these the same?'."""
    a, b = Image.open(base_path).convert('RGB'), Image.open(edit_path).convert('RGB')
    h = max(a.height, b.height)
    divider = 6
    sheet = Image.new('RGB', (a.width + divider + b.width, h), 'black')
    sheet.paste(a, (0, 0))
    sheet.paste(b, (a.width + divider, 0))
    sheet.save(dest)
    return dest


def judge_local(base_path, edit_path, cell_dir=None):
    """Two single-image yes/no questions on the edit, plus the two-panel identity comparison, recorded apart."""
    out = {'same_character': None, 'sitting': None, 'standing_check': None, 'identity_pair': None,
           'pose_checks_agree': None, 'seconds': {}, 'raw': {}, 'channel': {}, 'judge': 'local:%s' % LOCAL_MODEL}
    for key, question, image in (('same_character', Q_SAME, edit_path), ('sitting', Q_SIT, edit_path),
                                 ('standing_check', Q_STAND, edit_path)):
        verdict, secs, raw, channel = local_answer(image, question)
        out[key], out['seconds'][key], out['raw'][key], out['channel'][key] = verdict, secs, raw, channel
    if out['sitting'] is not None and out['standing_check'] is not None:
        out['pose_checks_agree'] = out['sitting'] is not out['standing_check']
        if not out['pose_checks_agree']:
            out['sitting'] = None
    if cell_dir is not None:
        pair = pair_image(base_path, edit_path, Path(cell_dir) / 'pair-base-left-edit-right.png')
        verdict, secs, raw, channel = local_answer(pair, Q_PAIR)
        out['identity_pair'], out['seconds']['identity_pair'] = verdict, secs
        out['raw']['identity_pair'], out['channel']['identity_pair'] = raw, channel
    out['total_seconds'] = round(sum(v for v in out['seconds'].values() if v), 1)
    return out


def controls(base_path, other_path, seated_path, out_dir):
    """The calibration, re-run on the questions this probe asks: every question has to be right in both directions."""
    cases = [
        ('sitting_on_standing_base', Q_SIT, base_path, False),
        ('sitting_on_standing_other', Q_SIT, other_path, False),
        ('sitting_on_seated_control', Q_SIT, seated_path, True),
        ('standing_on_standing_base', Q_STAND, base_path, True),
        ('standing_on_seated_control', Q_STAND, seated_path, False),
        ('same_attributes_on_base', Q_SAME, base_path, True),
        ('same_attributes_on_other', Q_SAME, other_path, False),
        ('pair_base_vs_base', Q_PAIR, pair_image(base_path, base_path, Path(out_dir) / 'pair-base-vs-base.png'), True),
        ('pair_base_vs_other', Q_PAIR, pair_image(base_path, other_path, Path(out_dir) / 'pair-base-vs-other.png'),
         False),
    ]
    rows, passed = [], 0
    for name, question, image, expected in cases:
        answer, secs, raw, channel = local_answer(image, question)
        ok = answer is expected
        passed += 1 if ok else 0
        rows.append({'id': name, 'expected': expected, 'answer': answer, 'ok': ok, 'seconds': secs,
                     'question': question, 'raw': raw, 'channel': channel, 'image': Path(image).name})
        print('  control %-26s expected %-5s got %-5s %5.1fs %-8s %s'
              % (name, expected, answer, secs or 0, channel, 'OK' if ok else 'MISS'), flush=True)
    return {'passed': passed, 'n': len(cases), 'rows': rows}


def diff(a, b):
    return round(sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3, 2)


def vram_used():
    try:
        return int(winrun(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader'],
                          capture_output=True, text=True, timeout=10).stdout.strip().split()[0])
    except Exception:
        return None


class VramPeak:
    """Peak VRAM in MiB while the block runs - the model's own behaviour, measured, not requested."""

    def __init__(self, interval=0.25):
        self.interval = interval
        self.peak = None
        self._stop = threading.Event()
        self._thread = None

    def _sample(self):
        while not self._stop.is_set():
            v = vram_used()
            if v and (self.peak is None or v > self.peak):
                self.peak = v
            self._stop.wait(self.interval)

    def __enter__(self):
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)
        return False


def run(port, prompt, out_dir, base_path, denoise, profile, steps, cfg, size=512, seed=4242):
    """One img2img render. Returns (path, seconds)."""
    engine = zui.Engine(port, autostart=False, progress=False)
    engine.ready = True
    t0 = time.time()
    engine.generate(prompt, size, size, steps, cfg, seed, str(out_dir), base_path=base_path,
                    denoise=denoise, batch=1, sequential=True, profile=profile)
    while time.time() - t0 < 600:
        try:
            kind, payload = engine.events.get(timeout=1)
        except Exception:
            continue
        if kind == 'image':
            return Path(payload[1]), round(time.time() - t0, 1)
        if kind == 'error':
            raise SystemExit('%s @ %.2f failed: %s' % (profile, denoise, str(payload)[:200]))
    raise SystemExit('%s @ %.2f timed out' % (profile, denoise))


def server_up(port):
    try:
        urllib.request.urlopen('http://127.0.0.1:%d/system_stats' % port, timeout=3)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profiles', nargs='*', default=list(zg.MODELS))
    ap.add_argument('--denoise', nargs='*', type=float, default=[0.4, 0.55, 0.7, 0.85])
    ap.add_argument('--judge', choices=['local', 'cloud', 'none'], default='local',
                    help="'local' keeps every image on this machine (qwen3-vl:4b), 'cloud' uploads each one to "
                         "api.deepseek.com (needs DEEPSEEK_API_KEY), 'none' measures without judging")
    ap.add_argument('--size', type=int, default=512)
    ap.add_argument('--port', type=int, default=8006)
    ap.add_argument('--out', default='pose-probe')
    ap.add_argument('--seed', type=int, default=4242)
    ap.add_argument('--keep-server', action='store_true', help='leave ComfyUI running when the sweep ends')
    ap.add_argument('--no-controls', action='store_true', help='skip the judge calibration block')
    args = ap.parse_args()

    if args.judge == 'cloud' and not KEY:
        raise SystemExit('DEEPSEEK_API_KEY is not set, so nothing can be judged - or use --judge local')
    if args.judge == 'local':
        import local_vlm
        if not local_vlm.available():
            raise SystemExit('no Ollama on 127.0.0.1:11434, so the local judge cannot answer - or use --judge cloud')

    out = Path(args.out)
    base_dir = out / 'base'
    base_dir.mkdir(parents=True, exist_ok=True)
    base_path = next(iter(sorted(base_dir.glob('*.png'))), None)

    idle = None
    started_server = False
    rows, control_report = [], None
    try:
        if not server_up(args.port):
            print('starting server...', flush=True)
            zg.start_server(args.port, timeout=300)
            started_server = True
        else:
            print('using the server already answering on port %d' % args.port, flush=True)
        # the idle number is only meaningful with the judge out of the way: a warm Ollama holds ~3.3 GiB
        if args.judge == 'local':
            print('judge unload -> %s' % local_vlm.unload(), flush=True)
        idle = vram_used()
        print('idle VRAM (judge unloaded, server up) %s MiB' % idle, flush=True)

        if base_path is None:
            print('generating the standing character...', flush=True)
            with VramPeak() as peak:
                base_path, secs = run(args.port, CHARACTER, base_dir, None, 1.0, 'z-image turbo', 4, 1.0,
                                      args.size, args.seed)
            print('  base %s (%.1f s, peak %s MiB)' % (base_path.name, secs, peak.peak), flush=True)

        if args.judge == 'local' and not args.no_controls:
            other_dir = out / 'controls'
            other_dir.mkdir(parents=True, exist_ok=True)
            other_path = other_dir / ('other-character_%dx%d_seed777.png' % (args.size, args.size))
            seated_path = other_dir / ('seated-control_%dx%d_seed778.png' % (args.size, args.size))
            for path, prompt, seed, label in ((other_path, OTHER, 777, 'different character'),
                                              (seated_path, SEATED, 778, 'seated reference')):
                if path.exists():
                    continue
                local_vlm.unload()
                with VramPeak() as peak:
                    made, secs = run(args.port, prompt, other_dir, None, 1.0, 'z-image turbo', 4, 1.0,
                                     args.size, seed)
                if not path.exists():
                    made.replace(path)
                print('  control %s %s (%.1f s, peak %s MiB)' % (label, path.name, secs, peak.peak), flush=True)
            print('calibrating the judge (3 questions, standing base, seated reference, different character)...',
                  flush=True)
            control_report = controls(base_path, other_path, seated_path, other_dir)
            print('  controls %d/%d' % (control_report['passed'], control_report['n']), flush=True)

        base_img = Image.open(base_path).convert('RGB')

        for profile in args.profiles:
            meta = zg.MODELS[profile]
            steps, cfg = meta['steps'], meta['cfg']
            for d in args.denoise:
                cell = out / ('%s_d%.2f' % (profile.replace(' ', '-'), d))
                cell.mkdir(parents=True, exist_ok=True)
                for f in cell.glob('*'):
                    if f.is_file():
                        f.unlink()
                if args.judge == 'local':
                    local_vlm.unload()
                with VramPeak() as gen_peak:
                    edit, secs = run(args.port, INSTRUCTION, cell, base_path, d, profile, steps, cfg,
                                     args.size, args.seed)
                verdict, judge_secs, judge_peak = {}, 0.0, None
                if args.judge != 'none':
                    with VramPeak() as vp:
                        if args.judge == 'local':
                            verdict = judge_local(base_path, edit, cell)
                            judge_secs = verdict.get('total_seconds', 0.0)
                        else:
                            judge_secs = None
                            verdict = judge_cloud(base_path, edit)
                    judge_peak = vp.peak
                delta = diff(base_img, Image.open(edit).convert('RGB'))
                same = pick(verdict, ('same_character', 'same_person', 'same', 'identical'))
                posed = pick(verdict, ('sitting', 'seated', 'pose_changed'))
                standing = verdict.get('standing_check') if args.judge == 'local' else None
                pair = pick(verdict, ('identity_pair',)) if args.judge == 'local' else None
                works = same is True and posed is True
                row = {'profile': profile, 'denoise': d, 'size': args.size, 'seed': args.seed,
                       'seconds': secs, 'gen_seconds': secs, 'pixel_delta': delta,
                       'same_character': same, 'sitting': posed, 'standing_check': standing,
                       'pose_checks_agree': verdict.get('pose_checks_agree'),
                       'works': works,
                       'identity_pair': pair, 'works_strict': works and pair is True,
                       'judge': verdict.get('judge', JUDGE_MODEL if args.judge == 'cloud' else 'none'),
                       'judge_seconds': judge_secs, 'judge_raw': verdict,
                       'vram_gen_peak_mib': gen_peak.peak, 'vram_judge_peak_mib': judge_peak,
                       'vram_peak_mib': max([v for v in (gen_peak.peak, judge_peak) if v], default=None),
                       'image': str(edit)}
                rows.append(row)
                print('%-26s d=%.2f %5.1fs delta %6.2f  same %-4s sitting %-4s stand %-4s pair %-4s %s  '
                      'gen %s / judge %s MiB'
                      % (profile, d, secs, delta, same, posed, standing, pair,
                         'WORKS' if works else '     ', gen_peak.peak, judge_peak), flush=True)
    finally:
        if args.judge == 'local':
            print('judge unload -> %s' % local_vlm.unload(), flush=True)
        after = vram_used()
        print('VRAM after the sweep (judge unloaded): %s MiB' % after, flush=True)
        if started_server and not args.keep_server:
            print('server:', zg.stop_server(args.port), flush=True)
        elif started_server:
            print('server: left running (--keep-server)', flush=True)

    summary = {'judge': ('local:%s' % LOCAL_MODEL) if args.judge == 'local' else
                        (JUDGE_MODEL if args.judge == 'cloud' else 'none'),
               'idle_vram_mib': idle,
               'vram_after_mib': after,
               'peak_vram_mib': max([r['vram_peak_mib'] for r in rows if r['vram_peak_mib']], default=None),
               'controls': control_report}
    (out / 'result.json').write_text(json.dumps({'character': CHARACTER, 'instruction': INSTRUCTION,
                                                 'other_character': OTHER,
                                                 'seed': args.seed, 'size': args.size,
                                                 'base': {'file': base_path.name, 'sha256_16': sha256(base_path),
                                                          'prompt': CHARACTER, 'model': 'z-image turbo'},
                                                 'judge': summary['judge'],
                                                 'judge_questions': ({'same_character': Q_SAME, 'sitting': Q_SIT,
                                                                      'standing_check': Q_STAND,
                                                                      'identity_pair': Q_PAIR}
                                                                     if args.judge == 'local' else {'cloud': JUDGE}),
                                                 'summary': summary, 'rows': rows,
                                                 'created': datetime.now().isoformat(timespec='seconds')},
                                                indent=1), encoding='utf-8')

    # contact sheet: one row per model, one column per denoise, base first
    if rows:
        cols = len(args.denoise) + 1
        profiles = [p for p in args.profiles if any(r['profile'] == p for r in rows)]
        sheet = Image.new('RGB', (args.size * cols, args.size * len(profiles)), 'white')
        for r_i, profile in enumerate(profiles):
            sheet.paste(base_img, (0, r_i * args.size))
            for c_i, d in enumerate(args.denoise):
                match = [r for r in rows if r['profile'] == profile and r['denoise'] == d]
                if match:
                    sheet.paste(Image.open(match[0]['image']).convert('RGB'), ((c_i + 1) * args.size,
                                                                              r_i * args.size))
        sheet.resize((1024, int(1024 * sheet.height / sheet.width)), Image.LANCZOS).save(
            out / 'sheet.jpg', quality=88)
        print('\nsheet:', out / 'sheet.jpg')

    print('\n%-26s %s' % ('model', ' '.join('d=%.2f' % d for d in args.denoise)))
    for profile in args.profiles:
        cells = []
        for d in args.denoise:
            match = [r for r in rows if r['profile'] == profile and r['denoise'] == d]
            if not match:
                cells.append(' -  ')
                continue
            r = match[0]
            cells.append('%s%s ' % ('S' if r['same_character'] else '.', 'P' if r['sitting'] else '.'))
        wins = sum(1 for r in rows if r['profile'] == profile and r['works'])
        print('%-26s %s  works %d/%d' % (profile, ' '.join(cells), wins,
                                         sum(1 for r in rows if r['profile'] == profile)))
    print('\nlegend: S = judge says same character, P = judge says sitting; "SP" is the only success')
    print('judge: %s   peak VRAM: %s MiB' % (summary['judge'], summary['peak_vram_mib']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
