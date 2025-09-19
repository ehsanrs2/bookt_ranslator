#!/usr/bin/env python3
"""
CLI entry for translating DOCX files to Persian, preserving structure and inline formatting.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List

from tqdm import tqdm

from docxio.translate_docx import TranslateOptions, translate_docx
from translator.googletrans_client import TranslatorClient


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="translate_docx.py",
        description="Translate a DOCX file (EN/FR -> FA) with structure preservation.",
    )
    parser.add_argument("input_docx", help="Path to the source .docx file.")
    parser.add_argument("--out", dest="output_docx", required=True, help="Output .docx path.")
    parser.add_argument("--src", choices=("auto", "en", "fr"), default="auto", help="Source language.")
    parser.add_argument("--tgt", default="fa", help="Target language (default: fa).")
    parser.add_argument("--font", default="Vazirmatn", help="Preferred Persian-capable font family.")
    parser.add_argument("--cache", help="Path to SQLite cache for translations.")
    parser.add_argument("--preserve-inline", action="store_true", help="Preserve bold/italic by paragraph-level markers (default).")
    parser.add_argument("--no-preserve-inline", action="store_true", help="Disable inline preservation and translate per run.")
    parser.add_argument("--skip-fields", action="store_true", help="Skip field code paragraphs (TOC, PAGE, REF).")
    parser.add_argument("--skip-urls", action="store_true", help="Do not translate URL targets; only visible text.")
    parser.add_argument("--skip-numeric", action="store_true", help="Skip paragraphs/runs that are mostly numeric/symbols.")
    # Traversal toggles (on by default). Use --no-headers to disable.
    parser.add_argument("--headers", action="store_true", help="Include headers (default on).")
    parser.add_argument("--footers", action="store_true", help="Include footers (default on).")
    parser.add_argument("--shapes", action="store_true", help="Include text boxes and shapes (default on).")
    parser.add_argument("--no-headers", action="store_true", help="Disable headers traversal.")
    parser.add_argument("--no-footers", action="store_true", help="Disable footers traversal.")
    parser.add_argument("--no-shapes", action="store_true", help="Disable shapes traversal.")
    parser.add_argument("--lists", action="store_true", help="Reserved flag; lists are handled as paragraphs.")
    parser.add_argument("--debug", action="store_true", help="Verbose logging.")
    parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR).")
    # Aggregation options
    parser.add_argument("--agg", action="store_true", help="Enable chunked aggregation across paragraphs (default on).")
    parser.add_argument("--no-agg", action="store_true", help="Disable aggregation; translate each paragraph separately.")
    parser.add_argument("--agg-max-chars", type=int, default=3800, help="Max characters per aggregated request (default 3800).")
    parser.add_argument("--agg-max-items", type=int, default=32, help="Max paragraphs per aggregated request (default 32).")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, (args.log_level or "INFO").upper(), logging.INFO), format="%(levelname)s: %(message)s")

    # Defaults
    preserve_inline = True
    if args.no_preserve_inline:
        preserve_inline = False

    include_headers = True
    include_footers = True
    include_shapes = True
    if args.no_headers:
        include_headers = False
    if args.no_footers:
        include_footers = False
    if args.no_shapes:
        include_shapes = False
    # If user explicitly provided positive flags, keep them on (redundant)
    if args.headers:
        include_headers = True
    if args.footers:
        include_footers = True
    if args.shapes:
        include_shapes = True

    agg = True
    if args.no_agg:
        agg = False
    if args.agg:
        agg = True

    options = TranslateOptions(
        src_lang=args.src,
        tgt_lang=args.tgt,
        font_family=args.font,
        preserve_inline=preserve_inline,
        skip_fields=args.skip_fields,
        skip_urls=args.skip_urls,
        skip_numeric=args.skip_numeric,
        include_headers=include_headers,
        include_footers=include_footers,
        include_shapes=include_shapes,
        debug=args.debug,
        agg=agg,
        agg_max_chars=int(args.agg_max_chars),
        agg_max_items=int(args.agg_max_items),
    )

    cache_path = args.cache if getattr(args, "cache", None) else None
    with TranslatorClient(cache_path=cache_path) as client:
        translate_docx(args.input_docx, args.output_docx, translator=client, options=options)

    logging.info("Saved translated DOCX to %s", args.output_docx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
