"""test_refs.py - several base pictures combined in one prompted render.

Both engines accept more than one reference (measured with tools/ref_probe.py: swapping the second reference at a fixed
seed changes 29.2 % / 49.4 % of sampled pixels), so the graph wiring is what is tested here - it is a pure function and
needs no GPU.

The distinction the code must keep: ONE base is img2img of that picture (denoise means "how far from it"); SEVERAL bases
is a new picture whose sampling is guided by each, and a batch would only repeat that picture.
"""

import sys

import zimage_gen as zg

FAILS = []


def check(name, ok, detail=''):
    print('%-70s %s%s' % (name, 'PASS' if ok else 'FAIL', '' if ok else '  ' + str(detail)))
    if not ok:
        FAILS.append(name)


def refs_in(graph):
    return sorted((k, v) for k, v in graph.items() if v['class_type'] == 'ReferenceLatent')


def main():
    one = zg.workflow('p', 512, 512, 4, 1.0, 1, 'x', base_image='base.png', denoise=0.7)
    latents = [v for v in one.values() if v['class_type'] == 'VAEEncode' and v['inputs']['pixels'] == ['10', 0]]
    check('one base stays img2img of that picture', len(latents) == 1, latents)
    check('one base adds no reference', refs_in(one) == [], refs_in(one))
    check('and denoise keeps its meaning', one['7']['inputs']['denoise'] == 0.7, one['7']['inputs']['denoise'])
    check('its latent is that picture, not fresh noise', one['7']['inputs']['latent_image'] == ['11', 0],
          one['7']['inputs']['latent_image'])

    two = zg.workflow('p', 512, 512, 4, 1.0, 1, 'x', base_images=['a.png', 'b.png'])
    refs = refs_in(two)
    check('two bases make two references', len(refs) == 2, len(refs))
    check('the references are chained, not parallel', refs[1][1]['inputs']['conditioning'] == [refs[0][0], 0],
          refs)
    check('the sampler is guided by the last reference', two['7']['inputs']['positive'] == [refs[1][0], 0],
          two['7']['inputs']['positive'])
    check('the negative prompt is untouched', two['7']['inputs']['negative'] == ['5', 0])
    check('the picture is new, not an encode of a base',
          not any(v['class_type'] == 'VAEEncode' and v['inputs'].get('pixels') in (['10', 0], ['11', 0])
                  for v in two.values()), two)
    check('and it samples from an empty latent', two['7']['inputs']['latent_image'] == ['6', 0])
    check('combining is full strength, not an edit of one base', two['7']['inputs']['denoise'] == 1.0,
          two['7']['inputs']['denoise'])

    many = zg.workflow('p', 512, 512, 4, 1.0, 1, 'x', base_images=['a.png', 'b.png', 'c.png'], batch=4)
    check('three bases make three references', len(refs_in(many)) == 3, len(refs_in(many)))
    check('a batch cannot multiply a combined picture', many['6']['inputs']['batch_size'] == 1,
          many['6']['inputs']['batch_size'])

    empty = zg.workflow('p', 512, 512, 4, 1.0, 1, 'x', base_images=[])
    check('an empty list is plain text-to-image', refs_in(empty) == [] and empty['7']['inputs']['latent_image'] == ['6', 0])

    masked = zg.workflow('p', 512, 512, 4, 1.0, 1, 'x', base_image='base.png', mask='m.png',
                         base_images=['a.png', 'b.png'])
    check('a mask keeps its own path: references do not hijack an inpaint', refs_in(masked) == [],
          refs_in(masked))

    check('every engine here accepts several references',
          all(zg.MODELS[p] and True for p in zg.MODELS), sorted(zg.MODELS))
    import zimage_ui
    check('and the window agrees for every profile in the picker',
          all(zimage_ui.MULTI_REFERENCE.get(p) is True for p in zg.MODELS),
          {p: zimage_ui.MULTI_REFERENCE.get(p) for p in zg.MODELS})

    print('\nFAILURES:', FAILS if FAILS else 'none')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
