
#!/usr/bin/env python3
"""
CLI entry point for translating PDF text content to Persian while preserving layout.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Tuple

import fitz  # PyMuPDF
from tqdm import tqdm

from pdfio import draw, layout
from translator.googletrans_client import TranslationError, TranslatorClient
from utils import text as text_utils


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="translate_pdf.py",
        description="Translate text blocks inside a PDF to Persian while preserving layout.",
    )
    parser.add_argument("input_pdf", help="Path to the source PDF.")
    parser.add_argument(
        "--out",
        dest="output_pdf",
        help="Optional path for the translated PDF (default: append _translated).",
    )
    parser.add_argument(
        "--src",
        choices=("auto", "en", "fr"),
        default="auto",
        help="Source language code for translation.",
    )
    parser.add_argument(
        "--tgt",
        default="fa",
        help="Target language code (default: fa (Persian)).",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=450,
        help="Maximum characters per translation chunk.",
    )
    parser.add_argument(
        "--font",
        default="fonts/Vazirmatn-Regular.ttf",
        help="Path to a TTF font with Persian glyph coverage.",
    )
    parser.add_argument(
        "--min-block-chars",
        type=int,
        default=2,
        help="Skip blocks shorter than this length.",
    )
    parser.add_argument(
        "--skip-small",
        action="store_true",
        help="Skip blocks that look like small schematic labels.",
    )
    parser.add_argument(
        "--line-gap",
        type=float,
        default=1.35,
        help="Line gap multiplier when laying out translated paragraphs.",
    )
    parser.add_argument(
        "--min-font",
        type=float,
        default=7.0,
        help="Minimum font size allowed when fitting translated text.",
    )
    parser.add_argument(
        "--max-font",
        type=float,
        default=14.0,
        help="Maximum font size explored during fitting.",
    )
    parser.add_argument(
        "--debug-layout",
        action="store_true",
        help="Overlay block rectangles and baselines to debug layout decisions.",
    )
    parser.add_argument(
        "--shrink-to-fit",
        action="store_true",
        help="Allow the engine to keep shrinking font size (down to min-font) instead of eliding.",
    )
    parser.add_argument(
        "--cache",
        help="Path to an optional SQLite cache for translations.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process and translate blocks without writing a PDF.",
    )
    parser.add_argument(
        "--dry-run-preview",
        type=int,
        default=5,
        help="How many block translations to print during --dry-run.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Configure logging verbosity.",
    )
    return parser.parse_args(argv)


def process_document(
    doc: fitz.Document,
    translator: TranslatorClient,
    args: argparse.Namespace,
) -> Tuple[int, int, List[Tuple[str, str]]]:
    font_spec = None
    if not args.dry_run:
        font_spec = draw.ensure_font(doc, args.font)

    translated_blocks = 0
    considered_blocks = 0
    dry_run_samples: List[Tuple[str, str]] = []

    for page_index in tqdm(range(doc.page_count), desc="Translating pages", unit="page"):
        page = doc[page_index]
        blocks = layout.extract_blocks(page)
        drawn_keys: set[tuple] = set()

        for block in blocks:
            if not layout.should_translate(block.text, block.rect, args):
                continue

            considered_blocks += 1
            key = block.identity
            if key in drawn_keys:
                continue

            cleaned = text_utils.clean_block_text(block.text)
            if not cleaned:
                continue

            chunks = text_utils.chunk_text(cleaned, args.max_chars)
            if not chunks:
                continue

            try:
                translations = translator.translate_batch(chunks, src=args.src, tgt=args.tgt)
            except TranslationError as exc:
                logging.error(
                    "Failed to translate block on page %s (block #%s): %s",
                    page_index + 1,
                    block.block_index,
                    exc,
                )
                continue

            translated = text_utils.join_chunks(translations).strip()
            if not translated:
                continue

            translated_blocks += 1

            if args.dry_run:
                if len(dry_run_samples) < args.dry_run_preview:
                    dry_run_samples.append((cleaned, translated))
                drawn_keys.add(key)
                continue

            if font_spec is None:
                logging.error("Font resource not initialized.")
                return considered_blocks, translated_blocks, dry_run_samples

            fontsize, lines, elided = draw.auto_fontsize_and_layout(
                translated,
                block.rect,
                font_spec,
                min_size=args.min_font,
                max_size=args.max_font,
                line_gap=args.line_gap,
                shrink_to_fit=args.shrink_to_fit,
            )

            if not lines:
                drawn_keys.add(key)
                continue

            draw.paint_background(page, block.rect)
            draw.draw_rtl_paragraph(
                page,
                block.rect,
                lines,
                fontsize,
                font_spec,
                args.line_gap,
                color=draw.DEFAULT_COLOR,
                debug=args.debug_layout,
            )

            if elided:
                logging.info(
                    "Elided translated block on page %s (block #%s) to fit.",
                    page_index + 1,
                    block.block_index,
                )
            elif fontsize <= args.min_font + 0.1:
                logging.debug(
                    "Block on page %s (block #%s) rendered at minimum font size %.2f.",
                    page_index + 1,
                    block.block_index,
                    fontsize,
                )

            drawn_keys.add(key)

    return considered_blocks, translated_blocks, dry_run_samples


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    if args.max_font < args.min_font:
        args.max_font = args.min_font
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    input_path = Path(args.input_pdf).expanduser().resolve()
    if not input_path.exists():
        logging.error("Input PDF not found: %s", input_path)
        return 1

    if args.output_pdf:
        output_path = Path(args.output_pdf).expanduser().resolve()
    else:
        output_path = input_path.with_name(f"{input_path.stem}_translated.pdf")

    if output_path.exists() and not (args.overwrite or args.dry_run):
        logging.error("Output already exists: %s (use --overwrite to replace)", output_path)
        return 1

    try:
        doc = fitz.open(str(input_path))
    except Exception as exc:  # pragma: no cover - PyMuPDF error surfaces here
        logging.error("Failed to open PDF %s: %s", input_path, exc)
        return 1

    with TranslatorClient(cache_path=args.cache) as translator:
        considered, translated, samples = process_document(doc, translator, args)

    logging.info("Blocks considered: %s | Blocks translated: %s", considered, translated)

    if args.dry_run:
        if not samples:
            logging.info("Dry-run finished with no translated samples to display.")
        else:
            print("=== Dry-run preview (source -> translated) ===")
            for source, target in samples:
                print("---")
                print(source.strip())
                print(">>>")
                print(target.strip())
        doc.close()
        return 0

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path), deflate=True)
        logging.info("Translated PDF saved to %s", output_path)
    except Exception as exc:
        logging.error("Unable to write translated PDF: %s", exc)
        return 1
    finally:
        doc.close()

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
