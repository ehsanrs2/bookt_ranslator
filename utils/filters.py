"""
Heuristics to detect content that should be skipped or protected for DOCX translation.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional

from docx.text.paragraph import Paragraph
from docx.text.run import Run


URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[\w\-\.\?\,\:/#%&=+~]+")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

FIELD_KEYWORDS = ("TOC", "HYPERLINK", "PAGEREF", "PAGE", "REF", "SEQ")

MONO_FONTS = {
    "Consolas",
    "Courier New",
    "Courier",
    "Fira Code",
    "Cascadia Code",
    "Monaco",
    "Menlo",
    "Source Code Pro",
    "JetBrains Mono",
}


def is_url(text: str) -> bool:
    if not text:
        return False
    return bool(URL_RE.search(text) or EMAIL_RE.search(text))


def is_numeric_heavy(text: str, threshold: float = 0.5) -> bool:
    """Return True if more than threshold of characters are digits or symbols."""
    if not text:
        return False
    letters = digits = symbols = 0
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith("L"):
            letters += 1
        elif cat.startswith("N"):
            digits += 1
        elif cat.startswith(("P", "S")):
            symbols += 1
    total = letters + digits + symbols
    if total == 0:
        return False
    return ((digits + symbols) / total) > threshold


def is_code_style(run: Run) -> bool:
    """Detect code-style runs by font or style hints."""
    try:
        name = run.font.name or ""
    except Exception:
        name = ""
    if name and name in MONO_FONTS:
        return True
    try:
        style_name = (run.style and run.style.name) or ""
    except Exception:
        style_name = ""
    if style_name and any(token in style_name.lower() for token in ("code", "mono")):
        return True
    return False


def is_field_code_paragraph(paragraph: Paragraph) -> bool:
    """True if the paragraph contains a field code (TOC, PAGE, HYPERLINK, etc.)."""
    p = paragraph._element
    # w:fldSimple directly indicates a field
    if p.xpath('.//w:fldSimple'):
        return True
    # field instructions may appear as w:instrText
    instr_nodes = p.xpath('.//w:instrText')
    for node in instr_nodes:
        val = (node.text or "").upper()
        if any(key in val for key in FIELD_KEYWORDS):
            return True
    return False


def is_field_code_oxml(p_element) -> bool:
    """Same as is_field_code_paragraph but for CT_P oxml element."""
    if p_element.xpath('.//w:fldSimple'):
        return True
    for node in p_element.xpath('.//w:instrText'):
        val = (node.text or "").upper()
        if any(key in val for key in FIELD_KEYWORDS):
            return True
    return False


def _w_ns():
    # Retained for compatibility if needed elsewhere
    return {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
