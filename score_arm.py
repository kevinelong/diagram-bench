"""score_arm.py - score a folder of rendered PNGs against the frozen diagram corpus."""
import json
import statistics
import sys
from pathlib import Path

from rapidocr_onnxruntime import RapidOCR

import trace_score

ROOT = Path(__file__).resolve().parent
ocr = RapidOCR()


def ocr_texts(png):
    result, _ = ocr(str(png))
    return [r[1] for r in (result or [])]


def main(folder, out_name):
    corpus = json.loads((ROOT / 'prompts-diagrams.json').read_text(encoding='utf-8'))
    by_id = {p['id']: p for p in corpus['prompts']}
    rows = []
    for png in sorted(Path(folder).glob('*.png')):
        base = png.stem              # e.g. flow-1-s0
        pid = base.rsplit('-s', 1)[0]
        prompt = by_id.get(pid)
        t = trace_score.score(png)
        found = ocr_texts(png)
        need = prompt['labels'] if prompt else []
        hits = {n: any(n.lower() in f.lower() for f in found) for n in need}
        rows.append({
            'id': pid, 'kind': prompt['kind'] if prompt else '?', 'sample': base,
            'paths': t['paths'], 'nodes': t['nodes'], 'traced_svg_kb': t['svg_kb'],
            'axis_pct': t['axis_pct'], 'colours_major': t['colours_major'],
            'ink_pct': t['ink_pct'], 'ocr_found': found, 'required': need,
            'ocr_hits': sum(hits.values()), 'ocr_exact': round(sum(hits.values()) / len(need), 3) if need else None,
        })
        print('  %-14s paths %4d axis %5s%% colours %2d ocr %d/%d'
              % (base, t['paths'], t['axis_pct'], t['colours_major'], sum(hits.values()), len(need)), flush=True)

    summary = {
        'n': len(rows),
        'paths_median': statistics.median([r['paths'] for r in rows]),
        'nodes_median': statistics.median([r['nodes'] for r in rows]),
        'axis_median': statistics.median([r['axis_pct'] for r in rows if r['axis_pct'] is not None]),
        'colours_median': statistics.median([r['colours_major'] for r in rows]),
        'ocr_exact_mean': round(statistics.mean([r['ocr_exact'] for r in rows if r['ocr_exact'] is not None]), 3),
        'ocr_full_match': sum(1 for r in rows if r['ocr_exact'] == 1.0),
        'rows': rows,
    }
    (ROOT / out_name).write_text(json.dumps(summary, indent=1), encoding='utf-8')
    print('\n== %s' % out_name)
    for k in ('n', 'paths_median', 'nodes_median', 'axis_median', 'colours_median', 'ocr_exact_mean', 'ocr_full_match'):
        print('  %-16s %s' % (k, summary[k]))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
