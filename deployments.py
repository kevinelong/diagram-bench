"""What has actually been measured: which model runs on which card, at which settings, and how fast.

The question this answers, before a new machine is set up: *this card has N GiB and compute X.Y - what should I start
with, and what will it cost per picture?* Two boxes have answered it so far, and the difference is large enough that
guessing is expensive: the same 1024 px / 9-step render is **14.3 s** on the desktop RTX 5070 and **140-145 s** on the
laptop RTX 4060.

Rules for a row, so this stays evidence rather than folklore:

* a row needs **wall seconds, peak VRAM and a date**, and the command that produced it in `provenance`;
* `fits` is about the card, not about luck - a profile whose peak exceeds the card is listed with `fits=False` and is never
  proposed;
* `native_fp4` is recorded because the quantised weights are *emulated* below compute 12.0, which changes both speed and
  memory;
* a row is added by the commit that measured it, never by memory afterwards.

`best_for()` is the function an installer (and `--advise`) should call; nothing in the app picks a model from this table
yet, deliberately - the app's default is a product decision, this is the evidence behind it.
"""

from __future__ import annotations

#: cards, keyed by a short name. `compute` decides FP4 support; `vram_mib` is what a profile must fit inside.
CARDS = {
    'desktop-5070': {
        'name': 'NVIDIA GeForce RTX 5070',
        'compute': (12, 0),
        'vram_mib': 12227,
        'native_fp4': True,
        'measured_on': '2026-11-01',
        'source': 'kevin: workstation (bench-ours, zimage_gen.py, --cache-none server)',
    },
    'laptop-4060': {
        'name': 'NVIDIA GeForce RTX 4060 Laptop',
        'compute': (8, 9),
        'vram_mib': 8188,
        'native_fp4': False,          # comfy_kitchen reports nvfp4 as emulated on Ada
        'measured_on': '2026-11-02',
        'source': '4060-deployment box, STATE-2026-11-01-4060.md',
    },
}

#: one row per (card, profile, size, steps) that was actually rendered. `width`/`height`/`steps` are the proven parameters.
RUNS = [
    # --- desktop 5070, 12 GiB, native FP4 ---------------------------------------------------------------
    dict(card='desktop-5070', profile='z-image turbo', width=1024, height=1024, steps=9,
         wall_s=14.3, peak_mib=None, fits=True,
         provenance='2026-11-01 zimage_gen.py --width 1024 --steps 9 --seed 1234 -> bench-ours.png (995,722 bytes)'),
    dict(card='desktop-5070', profile='flux2 klein 9b uncensored', width=256, height=256, steps=4,
         wall_s=20.9, peak_mib=None, fits=True,
         provenance='2026-10-30 native WinHTTP client, warm server, twice, identical 88,807-byte PNG'),
    # --- laptop 4060, 8 GiB, FP4 emulated ---------------------------------------------------------------
    dict(card='laptop-4060', profile='z-image turbo', width=1024, height=1024, steps=9,
         wall_s=142.5, peak_mib=7201, fits=True,
         provenance='2026-11-02 4060 box: "140-145 s / 1024 px / 9 steps, peak 7,201 MiB" - of which ~65 s is --cache-none staging'),
    dict(card='laptop-4060', profile='flux2 klein 9b uncensored', width=512, height=512, steps=4,
         wall_s=None, peak_mib=8837, fits=False,
         provenance='2026-10-25 three-arm run (8837 MiB at 512) - over the 8188 MiB card, hence DEFAULT_MODEL moved to turbo'),
    dict(card='laptop-4060', profile='flux3 klein 4b', width=512, height=512, steps=16,
         wall_s=109.4, peak_mib=None, fits=True,
         provenance='2026-11-02 4060 box, b59ac7a: verified render, 512 px, 16 steps'),
]

#: non-generation work, which a deployment also pays for.
OTHER = [
    dict(card='laptop-4060', task='upscale 2x Real-ESRGAN', wall_s=14.3,
         provenance='2026-11-02 4060 box, d15a225: model:RealESRGAN_x4plus, no tiling seams'),
    dict(card='desktop-5070', task='upscale 2x (model)', wall_s=None,
         provenance='measured in 7c94ff5; number not carried into this table yet'),
    dict(card='laptop-4060', task='describe one picture (qwen3-vl:4b, cold)', wall_s=95.4,
         provenance='2026-11-02 4060 box'),
    dict(card='desktop-5070', task='describe one picture (512 px, warm)', wall_s=3.8,
         provenance='README: 3.8 s per picture at 512 px against 16 s at 1024 px'),
]


def card_key(name: str, vram_mib: int | None, compute: tuple[int, int] | None):
    """Which known card is this, or None. Matched by name first, then by compute + VRAM when the name is a variant."""
    low = (name or '').lower()
    for key, card in CARDS.items():
        if card['name'].lower() in low:
            return key
    if compute is not None and vram_mib is not None:
        for key, card in CARDS.items():
            same_arch = card['compute'] == compute
            close = abs(card['vram_mib'] - vram_mib) <= max(1024, card['vram_mib'] // 8)
            if same_arch and close:
                return key
    return None


def runs_for(card: str):
    """The rows that were actually timed, fastest first."""
    rows = [r for r in RUNS if r['card'] == card and r['wall_s'] is not None and r['fits']]
    return sorted(rows, key=lambda r: r['wall_s'])


def best_for(card: str, vram_mib: int | None = None) -> dict | None:
    """The proven starting point for a card: the fastest profile **at the largest size the card can hold**.

    Size before speed on purpose: the first version sorted by wall time alone and offered `flux3 klein 4b` at 512 px
    (109 s) over `z-image turbo` at 1024 px (142 s) on the 8 GiB card - cheaper per picture, half the picture. A starting
    point that silently halves the output is not a starting point.

    Returns the run row, or None when nothing was measured on that card, which is the honest answer.
    """
    limit = vram_mib or (CARDS.get(card) or {}).get('vram_mib')
    rows = [r for r in runs_for(card) if limit is None or (r['peak_mib'] or 0) <= limit]
    if not rows:
        return None
    widest = max(r['width'] for r in rows)
    return min((r for r in rows if r['width'] == widest), key=lambda r: r['wall_s'])


def fits(profile: str, name: str = '', vram_mib: int | None = None, compute: tuple[int, int] | None = None):
    """Does this profile fit the card? True / False, or None when nothing is known about either.

    `None` matters: it is the difference between "we measured that it does not fit" and "we never tried", and only the
    first one may hide a profile from the picker or move a default.
    """
    key = card_key(name, vram_mib, compute)
    if key is None:
        return None
    limit = vram_mib or CARDS[key]['vram_mib']
    rows = [r for r in RUNS if r['card'] == key and r['profile'] == profile]
    if not rows:
        return None
    return all(r['fits'] and (r['peak_mib'] or 0) <= limit for r in rows)


def pick_profile(default: str, name: str = '', vram_mib: int | None = None,
                 compute: tuple[int, int] | None = None):
    """(profile, note) for a machine: the default when it fits, otherwise the card's proven profile.

    The default model was the same string on every box, and on the 8 GiB card it is a profile whose measured peak is
    8,837 MiB - it cannot run there at all. The note is for the banner: a default that changed must say why.
    """
    verdict = fits(default, name, vram_mib, compute)
    if verdict is not False:
        return default, None
    best = best_for(card_key(name, vram_mib, compute), vram_mib)
    if best is None:
        return default, ('%s does not fit this card and there is no measured alternative' % default)
    return best['profile'], ('%s does not fit this card (%s MiB) - started on %s instead'
                             % (default, vram_mib, best['profile']))


def advise(name: str = '', vram_mib: int | None = None, compute: tuple[int, int] | None = None) -> str:
    """A one-line answer for an installer's log: the card, whether FP4 is native, and what to start with."""
    key = card_key(name, vram_mib, compute)
    if key is None:
        return ('no measurement for this card (%s, %s MiB, compute %s) - measure one render before choosing; the 4060 '
                'Laptop and the desktop 5070 are in deployments.py as the two known points'
                % (name or 'unknown', vram_mib, compute))
    card = CARDS[key]
    best = best_for(key, vram_mib)
    if best is None:
        return '%s: known card, no proven profile that fits %s MiB' % (card['name'], vram_mib or card['vram_mib'])
    return ('%s (%s MiB, compute %d.%d, FP4 %s): start with %s at %dx%d / %d steps - %.1f s measured on %s'
            % (card['name'], vram_mib or card['vram_mib'], card['compute'][0], card['compute'][1],
               'native' if card['native_fp4'] else 'emulated',
               best['profile'], best['width'], best['height'], best['steps'], best['wall_s'], card['measured_on']))


_THIS = None


def _this_card():
    """(name, vram MiB, compute) for the GPU in this machine, or (None, None, None) when nvidia-smi is absent.

    Cached for the process: the hardware does not change, and the call is a child process - asking it once per window
    was enough to delay the first map and make two mapping checks flap (seen in the suite).
    """
    global _THIS
    if _THIS is not None:
        return _THIS
    from winproc import run          # no console window: a child of a GUI app would steal the foreground
    try:
        out = run(['nvidia-smi', '--query-gpu=name,memory.total,compute_cap', '--format=csv,noheader,nounits'],
                  capture_output=True, text=True, timeout=20).stdout.strip().splitlines()
        if not out:
            return None, None, None
        name, vram, cap = [part.strip() for part in out[0].split(',')]
        major, minor = (cap.split('.') + ['0'])[:2]
        _THIS = (name, int(float(vram)), (int(major), int(minor)))
    except Exception:
        _THIS = (None, None, None)
    return _THIS


if __name__ == '__main__':
    name, vram, cap = _this_card()
    print(advise(name, vram, cap))
    key = card_key(name, vram, cap)
    if key:
        print('known measurements on %s:' % key)
        for run in runs_for(key):
            print('  %-28s %4d px %2d steps  %6.1f s  %s' % (run['profile'], run['width'], run['steps'],
                                                              run['wall_s'], '' if run['fits'] else '(does not fit)'))
