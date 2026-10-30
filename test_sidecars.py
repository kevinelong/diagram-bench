#!/usr/bin/env python
"""Tests for recover_sidecars.py: our workflow, someone else's ComfyUI graph, A1111 blocks, and what cannot be recovered.

    python test_sidecars.py
"""

import json
import sys
import tempfile
from pathlib import Path

from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recover_sidecars as rs  # noqa: E402

FAILS = []


def check(label, ok, detail=''):
    print('%-64s %s %s' % (label, 'PASS' if ok else 'FAIL', detail))
    if not ok:
        FAILS.append(label)


def png(path, **chunks):
    info = PngImagePlugin.PngInfo()
    for key, value in chunks.items():
        info.add_text(key, value)
    Image.new('RGB', (32, 32), (60, 90, 120)).save(path, pnginfo=info)
    return path


OUR_GRAPH = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "z_image_turbo_nvfp4.safetensors"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a wooden mannequin in the same seated pose"}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry, watermark"}},
    "4": {"class_type": "KSampler", "inputs": {"steps": 4, "cfg": 1.0, "seed": 4242, "denoise": 0.72,
                                               "sampler_name": "euler",
                                               "positive": ["2", 0], "negative": ["3", 0]}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 768}},
    "6": {"class_type": "LoadImage", "inputs": {"image": "base-shot.png"}},
}

OTHER_GRAPH = {
    "10": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
    "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "a lighthouse on a rock at dusk"}},
    "12": {"class_type": "CLIPTextEncode", "inputs": {"text": "ugly, distorted"}},
    "13": {"class_type": "KSampler", "inputs": {"steps": 26, "cfg": 6.5, "seed": 77, "denoise": 1.0,
                                                "sampler_name": "dpmpp_2m",
                                                "positive": ["11", 0], "negative": ["12", 0]}},
    "14": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216}},
}

A1111_BLOCK = ("a lighthouse on a rock at dusk, dramatic light\nNegative prompt: ugly, distorted\n"
               "Steps: 26, Sampler: DPM++ 2M, CFG scale: 6.5, Seed: 77, Size: 832x1216, "
               "Model: sd_xl_base_1.0, Denoising strength: 0.55")


def main():
    tmp = Path(tempfile.mkdtemp(prefix='sidecars-'))

    print('\n-- our own pipeline (the graph this app writes)')
    p = png(tmp / '20261022-154057_1024x1024_seed1235.png', prompt=json.dumps(OUR_GRAPH))
    fields, source = rs.recover(p)
    check('the prompt comes from the positive link',
          fields and fields.get('prompt') == 'a wooden mannequin in the same seated pose', str(fields and fields.get('prompt')))
    check('the negative comes from the negative link',
          fields and fields.get('negative') == 'blurry, watermark', str(fields and fields.get('negative')))
    check('the sampler settings travel', fields and (fields.get('steps'), fields.get('seed'), fields.get('cfg'),
                                                     fields.get('denoise')) == (4, 4242, 1.0, 0.72),
          str(fields and (fields.get('steps'), fields.get('seed'), fields.get('cfg'), fields.get('denoise'))))
    check('the model is named', fields and fields.get('model') == 'z_image_turbo_nvfp4.safetensors',
          str(fields and fields.get('model')))
    check('the size is read', fields and (fields.get('width'), fields.get('height')) == (1024, 768),
          str(fields and (fields.get('width'), fields.get('height'))))
    check('the base image of an img2img graph is kept', fields and fields.get('base_image') == 'base-shot.png',
          str(fields and fields.get('base_image')))
    check('it is marked as imported, not native', fields and fields.get('imported') == 'comfy-workflow',
          str(fields and fields.get('imported')))
    check('the filename supplies created', fields and (fields.get('created') or '').startswith('2026-10-22T15:40:57'),
          str(fields and fields.get('created')))
    check('ranking fields are absent, not invented',
          fields and not ({'wins', 'losses', 'vs', 'bouts'} & set(fields)), str(fields and sorted(fields)))

    print('\n-- someone else\'s ComfyUI graph')
    other = png(tmp / 'foreign.png', prompt=json.dumps(OTHER_GRAPH))
    fields2, _ = rs.recover(other)
    check('a different workflow recovers too', fields2 and fields2.get('prompt') == 'a lighthouse on a rock at dusk',
          str(fields2 and fields2.get('prompt')))
    check('with its own sampler and model',
          fields2 and (fields2.get('steps'), fields2.get('sampler'), fields2.get('model'))
          == (26, 'dpmpp_2m', 'sd_xl_base_1.0.safetensors'),
          str(fields2 and (fields2.get('steps'), fields2.get('sampler'), fields2.get('model'))))

    print('\n-- A1111 / Forge')
    a = png(tmp / '20250101-101112_832x1216_seed77.png', parameters=A1111_BLOCK)
    fields3, source3 = rs.recover(a)
    check('the prompt line is the prompt', fields3 and fields3.get('prompt').startswith('a lighthouse on a rock at dusk'),
          str(fields3 and fields3.get('prompt')))
    check('the negative is split out', fields3 and fields3.get('negative') == 'ugly, distorted',
          str(fields3 and fields3.get('negative')))
    check('the parameter line is parsed',
          fields3 and (fields3.get('steps'), fields3.get('seed'), fields3.get('cfg'), fields3.get('denoise'))
          == (26, 77, 6.5, 0.55),
          str(fields3 and (fields3.get('steps'), fields3.get('seed'), fields3.get('cfg'), fields3.get('denoise'))))
    check('the size comes from Size:', fields3 and (fields3.get('width'), fields3.get('height')) == (832, 1216),
          str(fields3 and (fields3.get('width'), fields3.get('height'))))
    check('and the source is named', source3 == 'a1111-parameters', str(source3))

    print('\n-- what cannot be recovered is refused, not faked')
    plain = png(tmp / 'plain.png')
    none_fields, why = rs.recover(plain)
    check('a picture with no metadata yields nothing', none_fields is None, str(none_fields))
    check('and says why', 'no readable' in why or 'unrecognised' in why, str(why))
    broken = png(tmp / 'broken.png', prompt='not json at all')
    none2, why2 = rs.recover(broken)
    check('unparseable JSON is refused', none2 is None, str(none2))

    print('\n-- the command: dry run, write, then idempotent')
    folder = tmp / 'gallery'
    folder.mkdir()
    png(folder / 'a.png', prompt=json.dumps(OUR_GRAPH))
    png(folder / 'b.png', prompt=json.dumps(OTHER_GRAPH))
    png(folder / 'c.png')
    rs.main(['--folder', str(folder)])
    check('a dry run writes nothing', not list(folder.glob('*.json')), str(list(folder.glob('*.json'))))
    rs.main(['--folder', str(folder), '--apply'])
    made = sorted(p.name for p in folder.glob('*.json'))
    check('--apply writes only the recoverable ones', made == ['a.json', 'b.json'], str(made))
    written = json.loads((folder / 'a.json').read_text(encoding='utf-8'))
    check('the written sidecar points at its picture', Path(written.get('image', '')).name == 'a.png',
          str(written.get('image')))
    check('and carries the import mark', written.get('imported') == 'comfy-workflow', str(written.get('imported')))
    rs.main(['--folder', str(folder), '--apply'])
    check('a second pass has nothing to do', len(list(folder.glob('*.json'))) == 2, str(len(list(folder.glob('*.json')))))

    print('\n-- an imported picture from another pipeline becomes groupable')
    ext = tmp / 'external'
    ext.mkdir()
    for i in range(3):
        png(ext / ('ext%d.png' % i), parameters=A1111_BLOCK)     # a stranger's A1111 files, same prompt
    groups_before, ungrouped_before, counted = rs.preview_groups(ext)
    check('before the import nothing can be grouped', not groups_before and ungrouped_before == 3,
          '%s / %d' % (groups_before, ungrouped_before))
    rs.main(['--folder', str(ext), '--apply'])
    groups_after, ungrouped_after, counted2 = rs.preview_groups(ext)
    check('after the import the three pictures form one group',
          len(groups_after) == 1 and groups_after[0][1] == 3 and ungrouped_after == 0,
          '%s / ungrouped %d' % (groups_after, ungrouped_after))
    check('and the group is named from their prompt', 'lighthouse' in (groups_after[0][0] if groups_after else '').lower(),
          str(groups_after))

    print('\n-- an original prompt in the file wins over a caption that arrived later')
    later = tmp / 'later'
    later.mkdir()
    p = png(later / 'shot.png', prompt=json.dumps(OUR_GRAPH))
    p.with_suffix('.json').write_text(json.dumps({'image': str(p), 'caption': 'A caption added afterwards.',
                                                  'topic': 'afterwards', 'imported': 'caption', 'wins': 7}),
                                     encoding='utf-8')
    rs.main(['--folder', str(later), '--apply'])
    merged = json.loads(p.with_suffix('.json').read_text(encoding='utf-8'))
    check('the prompt from the picture is adopted',
          merged.get('prompt') == 'a wooden mannequin in the same seated pose', str(merged.get('prompt'))[:50])
    check('the later caption is kept in its own field', merged.get('caption') == 'A caption added afterwards.',
          str(merged.get('caption')))
    check('and nothing else is lost', merged.get('wins') == 7 and merged.get('topic') == 'afterwards',
          '%s / %s' % (merged.get('wins'), merged.get('topic')))
    check('the provenance of the prompt is recorded', merged.get('prompt_from') == 'comfy-workflow',
          str(merged.get('prompt_from')))
    second = json.loads(p.with_suffix('.json').read_text(encoding='utf-8'))
    rs.main(['--folder', str(later), '--apply'])
    again = json.loads(p.with_suffix('.json').read_text(encoding='utf-8'))
    check('a second run leaves it alone', again == second, 'changed on a re-run')

    print('\nFAILURES:', FAILS if FAILS else 'none')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(main())
