#!/usr/bin/env python
"""Tests for caption.py: the reply parser, and the fact that a missing model is reported rather than papered over.

    python test_caption.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import caption  # noqa: E402

FAILS = []


def check(label, ok, detail=''):
    print('%-62s %s %s' % (label, 'PASS' if ok else 'FAIL', detail))
    if not ok:
        FAILS.append(label)


def main():
    print('-- the reply')
    topic, text = caption._parse('{"topic": "wooden mannequin", "caption": "A wooden mannequin sits on a box."}')
    check('a clean JSON reply gives both', (topic, text) == ('wooden mannequin', 'A wooden mannequin sits on a box.'),
          '%r / %r' % (topic, text))
    topic2, text2 = caption._parse('```json\n{"topic": "Study", "caption": "A seated figure."}\n```')
    check('a fenced reply is still read', (topic2, text2) == ('study', 'A seated figure.'), '%r / %r' % (topic2, text2))
    topic3, text3 = caption._parse('Just a plain sentence about a picture.')
    check('chatter falls back to the first line', (topic3, text3) == ('', 'Just a plain sentence about a picture.'),
          '%r / %r' % (topic3, text3))
    check('an empty reply does not crash', caption._parse('') == ('', ''), str(caption._parse('')))
    check('the topic is normalised for filing', caption._parse('{"topic": "  Wooden  Mannequin. "}')[0] == 'wooden  mannequin',
          repr(caption._parse('{"topic": "  Wooden  Mannequin. "}')[0]))

    print('\n-- no model means an error, not a silent cloud call')
    real = caption.base_url
    caption.base_url = lambda: 'http://127.0.0.1:1/v1'          # nothing listens here
    try:
        check('available() says no', caption.available(timeout=0.5) is False, 'reported available')
        try:
            caption.caption(__file__)
            check('caption() refuses without a model', False, 'it returned something')
        except caption.NoModel as e:
            check('caption() refuses without a model', 'no vision model' in str(e), str(e)[:60])
        except Exception as e:
            check('caption() refuses without a model', False, '%s: %s' % (type(e).__name__, e))
    finally:
        caption.base_url = real

    print('\nFAILURES:', FAILS if FAILS else 'none')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(main())
