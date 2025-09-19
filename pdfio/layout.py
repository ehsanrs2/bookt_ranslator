
"""
Block extraction helpers for PDF translation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import fitz

from utils import text as text_utils

_ROUND_STEP = 0.5
_SMALL_BLOCK_AREA = 144.0
_SYMBOL_RATIO = 0.65


@dataclass
class Block:
    """Represents a single text block extracted from a PDF page."""

    block_index: int
    rect: fitz.Rect
    text: str

    @property
    def identity(self) -> Tuple[float, float, float, float, str]:
        rounded = tuple(round(coord / _ROUND_STEP) * _ROUND_STEP for coord in self.rect)
        normalized = _normalize_text(self.text)
        return rounded + (normalized,)


def extract_blocks(page: fitz.Page) -> List[Block]:
    """Extract unique text blocks from a page.

    PyMuPDF may return duplicated blocks for complex layouts. This function
    rounds bounding boxes to the nearest 0.5pt and normalizes the text to
    deduplicate near-identical blocks.
    """

    blocks: List[Block] = []
    seen: set[Tuple[float, float, float, float, str]] = set()

    for index, entry in enumerate(page.get_text("blocks") or ()):  # type: ignore[arg-type]
        if len(entry) < 5:
            continue
        block_type = entry[5] if len(entry) > 5 else 0
        if block_type == 1:  # image block
            continue

        x0, y0, x1, y1, raw_text = entry[:5]
        text = (raw_text or "").strip()
        if not text:
            continue

        rect = fitz.Rect(x0, y0, x1, y1)
        block = Block(block_index=index, rect=rect, text=text)
        key = block.identity
        if key in seen:
            continue
        seen.add(key)
        blocks.append(block)

    return blocks


def should_translate(text: str, rect: fitz.Rect, args) -> bool:
    """Determine whether a block should be translated."""

    cleaned = text_utils.clean_block_text(text)
    if not cleaned:
        return False

    if getattr(args, "skip_small", False) and _is_small_block(cleaned, rect, args):
        return False

    if text_utils.is_probably_label(cleaned):
        return False

    return text_utils.is_probably_translatable(
        cleaned,
        minimum_chars=getattr(args, "min_block_chars", 2),
        max_symbol_ratio=_SYMBOL_RATIO,
    )


def _normalize_text(text: str) -> str:
    simplified = " ".join(text_utils.clean_block_text(text).split())
    return simplified.lower()


def _is_small_block(text: str, rect: fitz.Rect, args) -> bool:
    area = rect.get_area()
    if area <= getattr(args, "small_block_area", _SMALL_BLOCK_AREA):
        return True
    tokens = text.split()
    if not tokens:
        return True
    if len(tokens) == 1 and len(tokens[0]) <= max(3, getattr(args, "min_block_chars", 2)):
        return True
    return False
