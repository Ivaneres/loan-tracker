#!/usr/bin/env python3
"""
CLI: run the same PDF extraction helpers used for Monthly Spending (and the text
sent toward the LLM for transaction extraction).

Usage (from repo root, with venv activated):

  ./venv/bin/python scripts/extract_pdf_spending.py path/to/statement.pdf
  ./venv/bin/python scripts/extract_pdf_spending.py path/to/statement.pdf --mode base
  ./venv/bin/python scripts/extract_pdf_spending.py path/to/statement.pdf --mode hints-json

Note: this imports ``app``, which starts the background scheduler (APScheduler).
For a one-off test that is usually fine; avoid long-running imports in production shells.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _hints_to_json_serializable(hints: list) -> list[dict]:
    out = []
    for h in hints:
        if not isinstance(h, dict):
            continue
        tokens = h.get('tokens')
        if isinstance(tokens, set):
            tokens = sorted(tokens)
        out.append({
            'amount': h.get('amount'),
            'direction': h.get('direction'),
            'tokens': tokens,
            'line_text': h.get('line_text'),
        })
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT))

    parser = argparse.ArgumentParser(
        description='Extract text / hints from a bank PDF using Finance Tracker spending code.',
    )
    parser.add_argument('pdf', type=Path, help='Path to a .pdf file')
    parser.add_argument(
        '--mode',
        choices=('full', 'base', 'hints-text', 'hints-json'),
        default='full',
        help=(
            'full: base text + COLUMN HINTS block (same shape as LLM statement text for spending); '
            'base: best of pdfplumber vs pypdf only; '
            'hints-text: LINE_HINT lines only; '
            'hints-json: JSON list of geometry reconcile hints (amount/direction/tokens/line_text)'
        ),
    )
    parser.add_argument('-o', '--output', type=Path, help='Write result to this file (UTF-8); default is stdout')
    parser.add_argument(
        '--stats',
        action='store_true',
        help='Print character counts to stderr',
    )
    args = parser.parse_args()

    pdf_path: Path = args.pdf
    if not pdf_path.is_file():
        print(f'Not a file: {pdf_path}', file=sys.stderr)
        return 1
    if pdf_path.suffix.lower() != '.pdf':
        print('Warning: file does not end in .pdf', file=sys.stderr)

    raw = pdf_path.read_bytes()

    # Import after path setup; pulls in Flask app module (scheduler starts).
    from app import (  # noqa: WPS433 (runtime import intentional)
        _build_spending_direction_hints,
        _extract_pdf_text,
        _extract_pdf_text_for_spending,
        _pdfplumber_spending_column_hints,
    )

    if args.mode == 'base':
        text = _extract_pdf_text(raw)
    elif args.mode == 'full':
        text = _extract_pdf_text_for_spending(raw)
    elif args.mode == 'hints-text':
        text = _pdfplumber_spending_column_hints(raw)
        if not text:
            text = ''
    else:
        hints = _build_spending_direction_hints(raw)
        text = json.dumps(_hints_to_json_serializable(hints), indent=2, ensure_ascii=False)

    if args.stats:
        non_ws = len(re.sub(r'\s+', '', text or ''))
        print(
            f'chars={len(text)} non_whitespace_chars={non_ws}',
            file=sys.stderr,
        )

    data = text.encode('utf-8')
    if args.output:
        args.output.write_bytes(data)
    else:
        sys.stdout.buffer.write(data)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
