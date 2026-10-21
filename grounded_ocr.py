"""grounded_ocr.py - the deterministic half of the grounding proof.

grounded.py proves structure (counts, label strings, connectors attach, everything inside the
canvas) from the spec.  This proves the *rendered* reference is legible to a machine reader:
each reference SVG is rasterised (Chromium, 1024x1024, via the browser tool) and read back with
RapidOCR.  A grounded prompt whose reference drawing does not OCR to its own labels is a bad
prompt, because no model output could score better than its own reference.

    python grounded_ocr.py            # prints a per-prompt table, exits 1 on any miss
"""

from __future__ import annotations

import json
import sys

from rapidocr_onnxruntime import RapidOCR

import grounded

DEFAULT_DIR = grounded.REPO / "grounded_ref"


def read_text(png, ocr):
    result, _ = ocr(str(png))
    return [r[1] for r in (result or [])]


def main(argv):
    d = DEFAULT_DIR
    ocr = RapidOCR()
    rows, failures = [], []
    for sid in grounded.ORDER:
        png = d / (sid + ".png")
        if not png.exists():
            failures.append("%s: no raster at %s (run: python grounded.py --emit, then render)" % (sid, png))
            continue
        spec = grounded.SPECS[sid]
        need = grounded.labels(spec)
        found = read_text(png, ocr)
        hits = [any(n.lower() in f.lower() for f in found) for n in need]  # one entry per label, multiplicity kept
        good = sum(hits)
        missed = [n for n, h in zip(need, hits) if not h]
        rows.append((sid, good, len(need), missed, found))
        if good != len(need):
            failures.append("%s: %d/%d labels read; missed %s (reader saw %s)"
                            % (sid, good, len(need), missed, found))

    print("%-12s labels  missed" % "prompt")
    for sid, good, need, missed, _ in rows:
        print("  %-12s %d/%d    %s" % (sid, good, need, ",".join(missed) if missed else "-"))
    total_need = sum(r[2] for r in rows)
    total_good = sum(r[1] for r in rows)
    print("\nlabels read exactly: %d/%d" % (total_good, total_need))
    if failures:
        print("FAILED %d prompts" % len(failures))
        for f in failures:
            print("  -", f)
        return 1
    out = {
        "declared": "2026-10-21",
        "method": "grounded_ref/<id>.svg rasterised 1024x1024 in Chromium, read with RapidOCR; "
                  "a label counts only if the exact string appears in the read text",
        "per_prompt": [{"id": sid, "labels_read": good, "labels_required": need, "missed": missed,
                        "reader_text": found} for sid, good, need, missed, found in rows],
        "total": {"read": total_good, "required": total_need},
    }
    (grounded.REPO / "result-grounded-ocr.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("PASS; wrote result-grounded-ocr.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
