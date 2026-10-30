#!/usr/bin/env python
"""Rebuild a picture's `.json` sidecar from the metadata inside the picture itself.

    python recover_sidecars.py --folder C:/Users/kevin/Pictures/zimage-ui            # dry run, reports only
    python recover_sidecars.py --folder ... --apply                                  # write the sidecars

Two families of source, because two families embed their own provenance:

  **ComfyUI** - the PNG `prompt` tEXt chunk holds the executed graph as JSON. Ours and anyone else's; the mapping below
  reads the nodes generically (a sampler with positive/negative inputs, a text encoder, a loader with a model name), so a
  graph from a different workflow recovers as well as our own.
  **A1111 / Forge** - the `parameters` tEXt chunk holds one text block: prompt, "Negative prompt:", then
  "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 123, Size: 512x512, Model: ...".

What cannot be recovered is stated in the record rather than invented: our own `wins/losses/vs` (ranking), the style label,
and `created` (taken from the filename when it carries one). Every recovered sidecar is marked `"imported": <source>` so an
imported record is never mistaken for one this app wrote.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

STAMP = re.compile(r'^(\d{8})-(\d{6})')
SIZE_IN_NAME = re.compile(r'(\d{3,5})x(\d{3,5})')
SEED_IN_NAME = re.compile(r'_seed(\d+)')


def chunks(path):
    """The PNG's tEXt chunks as {key: text}. Empty for anything that will not open."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            out = {}
            for key, value in (im.info or {}).items():
                if isinstance(value, bytes):
                    out[key] = value.decode('utf-8', 'replace')
                elif isinstance(value, str):
                    out[key] = value
            return out
    except Exception:
        return {}


def _text_of(node):
    inputs = node.get('inputs') or {}
    for key in ('text', 'prompt', 'value', 'string'):
        if isinstance(inputs.get(key), str) and inputs[key].strip():
            return inputs[key].strip()
    return ''


def from_comfy(text):
    """Fields from a ComfyUI graph. Returns (fields, 'comfy-workflow') or (None, reason)."""
    try:
        graph = json.loads(text)
    except Exception:
        return None, 'not JSON'
    if not isinstance(graph, dict) or not graph:
        return None, 'empty graph'
    nodes = {str(k): v for k, v in graph.items() if isinstance(v, dict)}
    by_class = {}
    for node in nodes.values():
        by_class.setdefault(str(node.get('class_type') or ''), []).append(node)

    def linked_text(link):
        """A link is [node_id, output_index] - resolve it to the text of the node it points at."""
        if isinstance(link, list) and link and str(link[0]) in nodes:
            return _text_of(nodes[str(link[0])])
        return ''

    # the sampler: anything with steps and cfg, whatever it is called
    sampler = None
    for node in nodes.values():
        inputs = node.get('inputs') or {}
        if 'steps' in inputs and ('cfg' in inputs or 'seed' in inputs or 'noise_seed' in inputs):
            sampler = inputs
            break
    fields = {}
    if sampler:
        for src, dst in (('seed', 'seed'), ('noise_seed', 'seed'), ('steps', 'steps'), ('cfg', 'cfg'),
                         ('denoise', 'denoise'), ('sampler_name', 'sampler')):
            if src in sampler and not isinstance(sampler[src], list):
                fields[dst] = sampler[src]
    # prompt text: follow the sampler's positive link when there is one, else take the longest text
    prompt, negative = '', ''
    if sampler:
        prompt = linked_text(sampler.get('positive'))
        negative = linked_text(sampler.get('negative'))
    if not prompt:
        texts = [_text_of(n) for n in nodes.values()]
        texts = [t for t in texts if t]
        prompt = max(texts, key=len) if texts else ''
        if negative in texts and negative == prompt and len(texts) > 1:
            texts.remove(negative)
            prompt = max(texts, key=len) if texts else prompt
    if not prompt:
        return None, 'no prompt text in the graph'
    fields['prompt'] = prompt
    if negative:
        fields['negative'] = negative
    # model, size, and the base image of an img2img graph
    for node in nodes.values():
        inputs = node.get('inputs') or {}
        for key in ('unet_name', 'ckpt_name'):
            if isinstance(inputs.get(key), str) and 'model' not in fields:
                fields['model'] = inputs[key]
        if 'width' in inputs and 'height' in inputs and 'width' not in fields:
            fields['width'], fields['height'] = inputs.get('width'), inputs.get('height')
        if 'image' in inputs and isinstance(inputs['image'], str) and 'base_image' not in fields \
                and str(node.get('class_type') or '').startswith('Load'):
            fields['base_image'] = inputs['image']
    return fields, 'comfy-workflow'


A1111_PARAM = re.compile(r'([A-Za-z][A-Za-z ]+):\s*([^,]+)')


def from_a1111(text):
    """Fields from an A1111/Forge `parameters` block."""
    if not text or 'Steps:' not in text:
        return None, 'no Steps: line'
    body, _, tail = text.rpartition('Steps:')
    body = (body + 'Steps:' + tail) if body else ('Steps:' + tail)
    prompt, negative = body, ''
    if 'Negative prompt:' in body:
        prompt, _, negative = body.partition('Negative prompt:')
        negative = negative.split('\n')[0].strip()
    prompt = prompt.strip()
    line = 'Steps:' + tail
    params = {k.strip().lower(): v.strip() for k, v in A1111_PARAM.findall(line)}
    fields = {'prompt': prompt}
    if negative:
        fields['negative'] = negative
    if params.get('steps', '').isdigit():
        fields['steps'] = int(params['steps'])
    if params.get('seed', '').isdigit():
        fields['seed'] = int(params['seed'])
    try:
        fields['cfg'] = float(params.get('cfg scale', ''))
    except ValueError:
        pass
    try:
        fields['denoise'] = float(params.get('denoising strength', ''))
    except ValueError:
        pass
    size = params.get('size', '')
    m = SIZE_IN_NAME.search(size)
    if m:
        fields['width'], fields['height'] = int(m.group(1)), int(m.group(2))
    for key in ('sampler', 'model'):
        if params.get(key):
            fields['sampler' if key == 'sampler' else 'model'] = params[key]
    return fields, 'a1111-parameters'


def recover(path):
    """(fields, source) for one picture, with what the filename can add. (None, reason) when there is nothing to read."""
    info = chunks(path)
    if not info:
        return None, 'no readable text metadata'
    fields, source = None, ''
    if info.get('prompt'):
        fields, source = from_comfy(info['prompt'])
    if not fields and info.get('parameters'):
        fields, source = from_a1111(info['parameters'])
    if not fields:
        return None, source or 'unrecognised metadata'
    name = Path(path).name
    stamp = STAMP.match(name)
    if stamp:
        try:
            fields['created'] = datetime.strptime('%s-%s' % stamp.groups(), '%Y%m%d-%H%M%S').isoformat(timespec='seconds')
        except ValueError:
            pass
    size = SIZE_IN_NAME.search(name)
    if size and 'width' not in fields:
        fields['width'], fields['height'] = int(size.group(1)), int(size.group(2))
    seed = SEED_IN_NAME.search(name)
    if seed and 'seed' not in fields:
        fields['seed'] = int(seed.group(1))
    if 'width' in fields and 'height' in fields:
        pass
    fields['image'] = str(path)
    fields['imported'] = source
    return fields, source


def preview_groups(folder):
    """What the grouping would make of this folder once the import is done: (groups, ungrouped, counted).

    `recent=0` on purpose - the app keeps the newest 18 pictures unfiled, and a preview that hid two thirds of a small
    import behind that window would say nothing about whether the import was worth it.
    """
    import group_prompts
    rows = []
    for picture in sorted(Path(folder).glob('*.png')):
        side = picture.with_suffix('.json')
        prompt = ''
        if side.is_file():
            try:
                prompt = json.loads(side.read_text(encoding='utf-8')).get('prompt') or ''
            except Exception:
                prompt = ''
        rows.append({'path': str(picture), 'prompt': prompt, 'created': ''})
    plan = group_prompts.plan(rows, recent=0, keep_lexicon=False)
    groups = [(g.get('label', ''), len(g.get('items') or [])) for g in plan.get('groups') or []]
    return groups, len(plan.get('ungrouped') or []), len(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Rebuild missing sidecars from the pictures themselves.')
    ap.add_argument('--folder', required=True)
    ap.add_argument('--apply', action='store_true', help='write the sidecars (dry run otherwise)')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--preview', action='store_true',
                    help='afterwards, show the folder groups the import would produce')
    a = ap.parse_args(argv)

    folder = Path(a.folder)
    pictures = sorted(p for p in folder.glob('*.png') if p.is_file())
    bare = [p for p in pictures if not p.with_suffix('.json').is_file()]
    if a.limit:
        bare = bare[:a.limit]
    print('%s: %d pictures, %d without a sidecar' % (folder, len(pictures), len(bare)))
    written, skipped = 0, {}
    for p in bare:
        fields, source = recover(p)
        if not fields:
            skipped[source] = skipped.get(source, 0) + 1
            continue
        if a.apply:
            p.with_suffix('.json').write_text(json.dumps(fields, indent=1), encoding='utf-8')
        written += 1
        if written <= 3:
            print('   %-46s %-18s seed=%s steps=%s %sx%s' % (p.name[:46], source, fields.get('seed'),
                                                             fields.get('steps'), fields.get('width'),
                                                             fields.get('height')))
    verb = 'wrote' if a.apply else 'would write'
    print('%s %d sidecars%s' % (verb, written, '' if a.apply else '  (dry run - add --apply)'))
    for reason, n in sorted(skipped.items(), key=lambda kv: -kv[1]):
        print('   skipped %4d: %s' % (n, reason))
    if a.preview:
        groups, ungrouped, counted = preview_groups(folder)
        print()
        print('what the grouping makes of %d pictures (every picture counted, not the newest 18 the app keeps unfiled):'
              % counted)
        for label, n in groups[:8]:
            print('   %4d  %s' % (n, (label or '(untitled)')[:60]))
        print('   %4d  ungrouped (no prompt recovered)' % ungrouped if ungrouped else '   no ungrouped pictures')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
