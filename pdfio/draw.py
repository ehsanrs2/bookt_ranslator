
"""
Helpers for drawing translated content into the PDF.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Tuple

import fitz

from utils import text as text_utils

LOGGER = logging.getLogger(__name__)
DEFAULT_COLOR = (0, 0, 0)
WHITE = (1, 1, 1)
_DEBUG_BASELINE_COLOR = (0.75, 0.75, 0.75)


@dataclass(frozen=True)
class FontSpec:
    """Represents an embedded font resource."""

    path: str
    font: fitz.Font


def ensure_font(doc: fitz.Document, font_path: str) -> FontSpec:
    font_file = Path(font_path).expanduser().resolve()
    if not font_file.exists():
        raise FileNotFoundError(f"Font file not found: {font_file}")

    cache = getattr(doc, "_embedded_fonts", {})
    spec = cache.get(str(font_file))
    if isinstance(spec, FontSpec):
        return spec

    font = fitz.Font(fontfile=str(font_file))
    spec = FontSpec(path=str(font_file), font=font)
    cache[str(font_file)] = spec
    setattr(doc, "_embedded_fonts", cache)
    LOGGER.debug("Loaded font %s", font_file)
    return spec


def paint_background(page: fitz.Page, rect: fitz.Rect, color=WHITE, opacity: float = 1.0) -> None:
    page.draw_rect(rect, color=color, fill=color, fill_opacity=opacity, overlay=True)


def auto_fontsize_and_layout(
    text: str,
    rect: fitz.Rect,
    font: FontSpec,
    min_size: float,
    max_size: float,
    line_gap: float,
    shrink_to_fit: bool,
) -> Tuple[float, List[str], bool]:
    """Determine the best font size and layout for RTL text within a rectangle."""

    width_fn: Callable[[str, float], float] = lambda shaped, size: font.font.text_length(
        shaped, fontsize=size
    )

    def _layout(size: float) -> Tuple[List[str], float]:
        raw_lines = text_utils.wrap_rtl(text, font.font, size, rect.width, width_fn)
        height = text_utils.measure_par_height(raw_lines, line_gap, size)
        return raw_lines, height

    if not text.strip():
        return max(min_size, 0.0), [""], False

    low = min_size
    high = max_size
    best_size = min_size
    best_lines: List[str] = []
    fits = False

    while high - low > 0.2:
        trial = (low + high) / 2.0
        lines, height = _layout(trial)
        if height <= rect.height + 1e-3:
            fits = True
            best_size = trial
            best_lines = lines
            low = trial + 0.1
        else:
            high = trial - 0.1

    if not fits:
        best_size = min_size
        best_lines, height = _layout(best_size)
    else:
        height = text_utils.measure_par_height(best_lines, line_gap, best_size)

    if height <= rect.height + 1e-3:
        shaped = [_shape_line(line) for line in best_lines]
        return best_size, shaped, False

    if shrink_to_fit:
        size = best_size
        lines = best_lines
        while size > min_size + 0.1:
            size = max(min_size, size - 0.5)
            lines, height = _layout(size)
            if height <= rect.height + 1e-3:
                shaped = [_shape_line(line) for line in lines]
                return size, shaped, False
        lines, height = _layout(min_size)
        if height <= rect.height + 1e-3:
            shaped = [_shape_line(line) for line in lines]
            return min_size, shaped, False
        best_lines = lines
        best_size = min_size

    # Default to elision strategy at min_size
    min_lines, _ = _layout(min_size)
    line_advance = max(min_size * line_gap, min_size)
    max_lines = max(1, int(rect.height // line_advance))
    trimmed = min_lines[:max_lines]
    if not trimmed:
        trimmed = [""]
    trimmed[-1] = _elide_line(trimmed[-1], rect.width, min_size, width_fn)
    shaped = [_shape_line(line) for line in trimmed]
    return min_size, shaped, True


def draw_rtl_paragraph(
    page: fitz.Page,
    rect: fitz.Rect,
    lines: List[str],
    fontsize: float,
    font: FontSpec,
    line_gap: float,
    color=DEFAULT_COLOR,
    debug: bool = False,
) -> None:
    writer = fitz.TextWriter(page.rect)
    line_advance = fontsize * line_gap
    y = rect.y0 + fontsize

    for line in lines:
        shaped = line
        width = font.font.text_length(shaped, fontsize=fontsize)
        x = rect.x1 - width
        if x < rect.x0:
            x = rect.x0
        writer.append((x, y), shaped, font=font.font, fontsize=fontsize)
        if debug:
            page.draw_line((rect.x0, y), (rect.x1, y), color=_DEBUG_BASELINE_COLOR, width=0.3)
        y += line_advance

    writer.write_text(page, color=color)

    if debug:
        page.draw_rect(rect, color=(1, 0, 0), width=0.5)


def _shape_line(line: str) -> str:
    return text_utils.shape_rtl(line)


def _elide_line(
    raw_line: str,
    max_width: float,
    fontsize: float,
    width_fn: Callable[[str, float], float],
) -> str:
    base = raw_line.rstrip()
    ellipsis = " â€¦"
    if not base:
        base = ""
        ellipsis = "â€¦"
    candidate = f"{base}{ellipsis}".strip()
    shaped = text_utils.shape_rtl(candidate)
    while base and width_fn(shaped, fontsize) > max_width:
        base = base[:-1]
        candidate = f"{base.rstrip()}{ellipsis}" if base else ellipsis.strip()
        shaped = text_utils.shape_rtl(candidate)
    return candidate
