"""test_video.py - the clip graphs, without a server and without a GPU.

    python test_video.py

Two models live behind `--model`, and they are not interchangeable: Wan 2.2 TI2V-5B packs a start image into a 16x16x4
latent and has no end-image input at all, while Wan 2.1 I2V-14B builds *conditioning* through
`WanFirstLastFrameToVideo` with the 2.1 VAE and a CLIP-vision tower. A wrong wire here is not a crash - ComfyUI reports
"required input is missing" after the weights have loaded, which costs minutes per attempt - so the graphs are asserted
structurally: node ids, the exact inputs each sampler reads, and the refusals.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import video

FAILS = []


def check(name, cond, detail=''):
    print('%-58s %s%s' % (name, 'PASS' if cond else 'FAIL', ('  ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


def main():
    print('-- the two profiles')
    check('the 5B grid is 704-based', video.grids_for('5b')['1:1'] == (704, 704), video.grids_for('5b'))
    check('the 14B grid is 480p-based', video.grids_for('i2v14')['16:9'] == (832, 480), video.grids_for('i2v14'))
    check('the 14B profile needs a CLIP-vision tower', 'clip_vision' in video.WAN_I2V14)
    check('and the 2.1 VAE, not the 2.2 one', video.WAN_I2V14['vae'] == 'wan_2.1_vae.safetensors')
    check('the 5B profile has no CLIP-vision tower', 'clip_vision' not in video.WAN)

    print('\n-- the start-only graph')
    g = video.graph('a.png', 704, 704, 49, 'a fox', 'blurry', 1234, 30, 5.0, 8.0, 'uni_pc', 'simple', 'pfx')
    check('it builds one latent node with a start image', g['8']['class_type'] == 'Wan22ImageToVideoLatent'
          and 'start_image' in g['8']['inputs'])
    check('and no end-image input exists to pass', 'end_image' not in g['8']['inputs'])
    check('the sampler reads that latent', g['9']['inputs']['latent_image'] == ['8', 0])

    print('\n-- the first-last-frame graph')
    g = video.graph_first_last('a.png', 'b.png', 832, 480, 49, 'drift left', 'blurry', 7, 30, 5.0, 8.0,
                               'uni_pc', 'simple', 'pfx')
    kinds = {k: v['class_type'] for k, v in g.items()}
    check('it loads a CLIP-vision tower', 'CLIPVisionLoader' in kinds.values(), kinds)
    check('it encodes both endpoints with CLIP vision',
          kinds['9'] == 'CLIPVisionEncode' and kinds['10'] == 'CLIPVisionEncode', kinds)
    flf = g['11']['inputs']
    check('the conditioning node takes a start and an end image',
          kinds['11'] == 'WanFirstLastFrameToVideo' and flf['start_image'] == ['7', 0] and flf['end_image'] == ['8', 0],
          flf)
    check('and both CLIP-vision embeddings', flf['clip_vision_start_image'] == ['9', 0]
          and flf['clip_vision_end_image'] == ['10', 0], flf)
    check('the sampler reads positive, negative and latent from that one node',
          g['13']['inputs']['positive'] == ['11', 0] and g['13']['inputs']['negative'] == ['11', 1]
          and g['13']['inputs']['latent_image'] == ['11', 2], g['13']['inputs'])
    check('the shift is applied to the model, not the sampler', kinds['12'] == 'ModelSamplingSD3'
          and g['13']['inputs']['model'] == ['12', 0])
    dangling = [value for node in g.values() for value in node['inputs'].values()
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] not in g]
    check('every referenced node exists', not dangling, dangling)

    print('\n-- the refusals, which are cheaper than a failed run')
    for kwargs, why in ((dict(model='5b', end='b.png'), 'end image on the 5B'),
                        (dict(model='nope'), 'unknown model'),
                        (dict(model='5b', grid='9:16'), 'a grid neither profile offers'),
                        (dict(model='i2v14', grid='1:1', frames=50), 'frames must be 4k+1')):
        base = dict(still='a.png', host='http://127.0.0.1:1', free_first=False)
        base.update(kwargs)
        try:
            video.make_clip(**base)
        except ValueError as e:
            check('refused: %s' % why, True)
        except Exception as e:
            check('refused: %s' % why, False, '%s (not a ValueError)' % type(e).__name__)
        else:
            check('refused: %s' % why, False, 'no error raised')

    print('\n%s' % ('ALL PASS' if not FAILS else 'FAILURES: %s' % ', '.join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == '__main__':
    sys.exit(main())
