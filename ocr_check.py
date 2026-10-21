"""ocr_check.py - report the text a deterministic OCR can read, and whether required labels appear verbatim."""
import json
import sys
from pathlib import Path

from rapidocr_onnxruntime import RapidOCR

ocr = RapidOCR()


def read_text(png):
    result, _ = ocr(str(png))
    return [r[1] for r in (result or [])]


if __name__ == '__main__':
    png = sys.argv[1]
    need = sys.argv[2].split(',') if len(sys.argv) > 2 else []
    found = read_text(png)
    hits = {n: any(n.lower() in f.lower() for f in found) for n in need}
    print(json.dumps({'file': Path(png).name, 'found': found, 'required': need, 'hits': hits,
                      'ocr_exact': (sum(hits.values()) / len(need)) if need else None}, indent=1))
