"""score_arm.py - score a folder of rendered PNGs against the frozen diagram corpus.

The raster metrics come from `raster_measure.measure()` (one implementation, shared with
trace_score.py); this script only decides what a folder's numbers are and writes them out.

Output schema is the one the existing `result-*-512-scores.json` files use, unchanged:
  summary: n, paths_median, nodes_median, axis_median, colours_median, ocr_exact_mean,
           ocr_full_match, rows
  row    : id, kind, sample, paths, nodes, traced_svg_kb, axis_pct, colours_major, ink_pct,
           ocr_found, required, ocr_hits, ocr_exact
Added additively (old readers ignore them):
  row    : width, height, segments, edge_sparse_pct, low_contrast_normalised
  summary: segments_median, edge_sparse_median, low_contrast_rows
`low_contrast_rows` counts images whose segment count needed the contrast rescue, so a folder whose
geometry was measured from a stretched image is visible in the summary instead of silently passing.
"""
import json
import statistics
import sys
from pathlib import Path

from rapidocr_onnxruntime import RapidOCR

import raster_measure
import trace_score

ROOT = Path(__file__).resolve().parent
ocr = RapidOCR()


def ocr_texts(png):
    result, _ = ocr(str(png))
    return [r[1] for r in (result or [])]


def main(folder, out_name, corpus_file='prompts-diagrams.json'):
    # an experiment corpus can supply its own labels; the frozen corpus stays the default
    corpus = json.loads((ROOT / corpus_file).read_text(encoding='utf-8'))
    by_id = {p['id']: p for p in corpus['prompts']}
    rows = []
    for png in sorted(Path(folder).glob('*.png')):
        base = png.stem              # e.g. flow-1-s0
        pid = base.rsplit('-s', 1)[0]
        prompt = by_id.get(pid)
        m = raster_measure.measure(png)
        t = trace_score.score(png, measured=m)
        found = ocr_texts(png)
        need = prompt['labels'] if prompt else []
        hits = {n: any(n.lower() in f.lower() for f in found) for n in need}
        rows.append({
            'id': pid, 'kind': prompt['kind'] if prompt else '?', 'sample': base,
            'paths': t['paths'], 'nodes': t['nodes'], 'traced_svg_kb': t['svg_kb'],
            'axis_pct': t['axis_pct'], 'colours_major': t['colours_major'],
            'ink_pct': t['ink_pct'], 'ocr_found': found, 'required': need,
            'ocr_hits': sum(hits.values()), 'ocr_exact': round(sum(hits.values()) / len(need), 3) if need else None,
            'width': m['width'], 'height': m['height'], 'segments': m['segments'],
            'edge_sparse_pct': m['edge_sparse_pct'],
            'low_contrast_normalised': m['low_contrast_normalised'],
        })
        print('  %-14s paths %4d axis %6s colours %2d segs %3d sparse %4.1f%%%s ocr %d/%d'
              % (base, t['paths'], t['axis_pct'] if t['axis_pct'] is not None else '-',
                 t['colours_major'], m['segments'],
                 m['edge_sparse_pct'], ' NORM' if m['low_contrast_normalised'] else '',
                 sum(hits.values()), len(need)), flush=True)

    axis_rows = [r['axis_pct'] for r in rows if r['axis_pct'] is not None]
    ocr_rows = [r['ocr_exact'] for r in rows if r['ocr_exact'] is not None]
    summary = {
        'n': len(rows),
        'paths_median': statistics.median([r['paths'] for r in rows]),
        'nodes_median': statistics.median([r['nodes'] for r in rows]),
        'axis_median': statistics.median(axis_rows) if axis_rows else None,
        'colours_median': statistics.median([r['colours_major'] for r in rows]),
        'ocr_exact_mean': round(statistics.mean(ocr_rows), 3) if ocr_rows else None,
        'ocr_full_match': sum(1 for r in rows if r['ocr_exact'] == 1.0),
        'segments_median': statistics.median([r['segments'] for r in rows]),
        'edge_sparse_median': statistics.median([r['edge_sparse_pct'] for r in rows]),
        'low_contrast_rows': sum(1 for r in rows if r['low_contrast_normalised']),
        'rows': rows,
    }
    (ROOT / out_name).write_text(json.dumps(summary, indent=1), encoding='utf-8')
    print('\n== %s' % out_name)
    for k in ('n', 'paths_median', 'nodes_median', 'axis_median', 'colours_median', 'ocr_exact_mean',
              'ocr_full_match', 'segments_median', 'edge_sparse_median', 'low_contrast_rows'):
        print('  %-18s %s' % (k, summary[k]))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else 'prompts-diagrams.json')
