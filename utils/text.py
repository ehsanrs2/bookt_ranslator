"""
Helpers for cleaning text, chunking for translation requests, and rendering RTL strings.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Callable, List, Tuple

import arabic_reshaper
import regex as re
from bidi.algorithm import get_display

LOGGER = logging.getLogger(__name__)

LETTER_RE = re.compile(r"\p{L}", re.UNICODE)
ALNUM_RE = re.compile(r"\p{L}|\p{N}", re.UNICODE)
URL_RE = re.compile(r"https?://", re.IGNORECASE)
LABEL_RE = re.compile(r"^[A-Z]{1,3}\s*[-/]?\s*\d{1,4}[A-Z]?$")
FIGURE_RE = re.compile(r"^\s*(fig\.|figure|table|eq\.|equation)\b", re.IGNORECASE)
SEGMENT_RE = re.compile(r"\S+\s*", re.UNICODE)

# Common invisible/control characters we want to tame early
ZWJ = chr(0x200C)  # Zero Width Non-Joiner (often leaked from PDFs)
RLM = chr(0x200F)
LRE = chr(0x202A)
PDF = chr(0x202C)
SOFT_HYPHEN = chr(0x00AD)
CR = chr(0x000D)
LF = chr(0x000A)
NBSP = chr(0x00A0)
BOM = chr(0xFEFF)

__all__ = [
    "clean_block_text",
    "chunk_text",
    "join_chunks",
    "reshape_for_persian",
    "shape_rtl",
    "wrap_rtl",
    "measure_par_height",
    "is_probably_translatable",
    "is_probably_label",
]


def clean_block_text(text: str) -> str:
    """Clean and sanitize text extracted from PDFs before further processing.

    Goals:
    - Normalize newlines and whitespace (keep newlines, collapse spaces)
    - Remove PDF formatting artifacts and private-use glyphs (PUA)
    - Drop invisible control/format characters that can poison downstream steps
    - Ensure result is NFC/NFKC-normalized to stabilize comparisons/caching
    """

    # 1) Normalize basic newlines and known PDF artifacts
    cleaned = text.replace(CR + LF, LF).replace(CR, LF)
    cleaned = cleaned.replace(ZWJ, " ")  # make joins visible as separations
    cleaned = cleaned.replace(RLM, "").replace(LRE, "").replace(PDF, "")
    cleaned = cleaned.replace(SOFT_HYPHEN, "")  # discretionary hyphen
    cleaned = cleaned.replace(NBSP, " ")  # unify to normal space
    cleaned = cleaned.replace(BOM, "")  # stray BOMs

    # 2) Unicode normalization to fold compatibility forms
    cleaned = unicodedata.normalize("NFKC", cleaned)

    # 3) Filter out problematic code points
    def _allowed_char(ch: str) -> bool:
        cp = ord(ch)
        if ch in ("\n", "\r", "\t", " "):
            return True
        # Remove Unicode replacement char and other obvious noise
        if cp == 0xFFFD:  # �
            return False
        # Strip Private Use Areas (common when PDFs lack ToUnicode maps)
        if 0xE000 <= cp <= 0xF8FF:  # BMP PUA
            return False
        if 0xF0000 <= cp <= 0xFFFFD or 0x100000 <= cp <= 0x10FFFD:  # Sup PUA
            return False
        # Drop control and format characters (Cc, Cf). Keep Mn (diacritics).
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf"):
            return False
        return True

    cleaned = "".join(ch for ch in cleaned if _allowed_char(ch))

    # 4) Tidy whitespace: collapse horizontal runs, keep newlines as structure
    cleaned = cleaned.replace("\t", " ")
    # Collapse 2+ spaces into one (per line) without touching newlines
    cleaned = "\n".join(re.sub(r"[ ]{2,}", " ", line).strip() for line in cleaned.splitlines())

    return cleaned.strip()


def chunk_text(text: str, max_chars: int) -> List[str]:
    max_chars = max(1, max_chars)
    prepared = clean_block_text(text)
    if len(prepared) <= max_chars:
        return [prepared] if prepared else []

    lines: List[str] = []
    for raw_line in prepared.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            lines.append("")
            continue
        lines.extend(_split_long_line(stripped, max_chars))

    chunks: List[str] = []
    current = ""

    for line in lines:
        if line == "":
            if current:
                chunks.append(current)
                current = ""
            continue
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = line

    if current:
        chunks.append(current)

    return [chunk for chunk in chunks if chunk]


def _split_long_line(line: str, max_chars: int) -> List[str]:
    parts: List[str] = []
    remaining = line
    while len(remaining) > max_chars:
        split_idx = remaining.rfind(" ", 0, max_chars)
        if split_idx <= 0:
            split_idx = max_chars
        parts.append(remaining[:split_idx].strip())
        remaining = remaining[split_idx:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def join_chunks(chunks: List[str]) -> str:
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def reshape_for_persian(text: str) -> str:
    return shape_rtl(text)


def shape_rtl(text: str) -> str:
    if not text:
        return ""
    shaped_lines: List[str] = []
    for line in text.splitlines():
        if not line:
            shaped_lines.append("")
            continue
        reshaped = arabic_reshaper.reshape(line)
        shaped_lines.append(get_display(reshaped))
    if text.endswith("\n"):
        shaped_lines.append("")
    return "\n".join(shaped_lines).rstrip("\n")


def wrap_rtl(
    text: str,
    font,
    fontsize: float,
    max_width: float,
    get_text_width: Callable[[str, float], float],
) -> List[str]:
    paragraphs = text.splitlines() or [text]
    lines: List[str] = []

    for paragraph in paragraphs:
        if not paragraph.strip():
            lines.append("")
            continue

        current = ""
        segments = SEGMENT_RE.findall(paragraph)
        for segment in segments:
            remainder = segment
            while remainder:
                candidate = current + remainder
                width = get_text_width(shape_rtl(candidate.rstrip()), fontsize)
                if width <= max_width:
                    current = candidate
                    remainder = ""
                else:
                    if current:
                        lines.append(current.rstrip())
                        current = ""
                        continue
                    part, remainder = _split_segment(remainder, max_width, fontsize, get_text_width)
                    if not part:
                        remainder = ""
                        break
                    lines.append(part.rstrip())
        if current:
            lines.append(current.rstrip())
            current = ""

    return lines


def _split_segment(
    segment: str,
    max_width: float,
    fontsize: float,
    get_text_width: Callable[[str, float], float],
) -> Tuple[str, str]:
    working = segment.lstrip()
    if not working:
        return "", ""

    low, high = 1, len(working)
    best = 1
    while low <= high:
        mid = (low + high) // 2
        sample = working[:mid]
        width = get_text_width(shape_rtl(sample.rstrip()), fontsize)
        if width <= max_width or mid == 1:
            best = mid
            low = mid + 1
        else:
            high = mid - 1

    part = working[:best]
    remainder = working[best:]
    return part.rstrip(), remainder.lstrip()


def measure_par_height(lines: List[str], line_gap: float, fontsize: float) -> float:
    if not lines:
        return 0.0
    return len(lines) * fontsize * line_gap


def is_probably_translatable(text: str, minimum_chars: int, max_symbol_ratio: float) -> bool:
    candidate = clean_block_text(text)
    if len(candidate) < minimum_chars:
        return False
    if URL_RE.search(candidate):
        return False
    if not LETTER_RE.search(candidate):
        return False
    if FIGURE_RE.match(candidate):
        return False
    ratio = _symbol_ratio(candidate)
    if ratio > max_symbol_ratio:
        LOGGER.debug("Skipping block due to symbol ratio %.2f: %s", ratio, candidate)
        return False
    return True


def _symbol_ratio(text: str) -> float:
    letters = digits = symbols = 0
    for char in text:
        category = unicodedata.category(char)
        if category.startswith("L"):
            letters += 1
        elif category.startswith("N"):
            digits += 1
        elif category.startswith(("P", "S")):
            symbols += 1
    total = letters + digits + symbols
    if total == 0:
        return 0.0
    return (symbols + digits) / total


def is_probably_label(text: str) -> bool:
    condensed = clean_block_text(text)
    if not condensed or len(condensed) > 6:
        return False
    if LABEL_RE.match(condensed):
        return True
    alnum = ALNUM_RE.findall(condensed)
    return len(alnum) <= 2
