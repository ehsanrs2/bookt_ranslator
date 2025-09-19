"""
Word-specific RTL helpers for paragraphs and runs.
"""

from __future__ import annotations

from typing import Optional

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def set_paragraph_rtl(paragraph: Paragraph) -> None:
    """Mark a paragraph as RTL and right-aligned.

    This sets w:bidi on the paragraph properties and aligns to RIGHT.
    """
    p = paragraph._element  # CT_P
    pPr = p.get_or_add_pPr()
    # Add <w:bidi/>
    bidi = OxmlElement("w:bidi")
    pPr.append(bidi)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT


def set_run_rtl(run: Run, font_family: Optional[str] = None) -> None:
    """Mark a run as RTL and set complex script font if provided."""
    r = run._element  # CT_R
    rPr = r.get_or_add_rPr()

    rtl = OxmlElement("w:rtl")
    rPr.append(rtl)

    if font_family:
        rFonts = rPr.rFonts
        if rFonts is None:
            rFonts = OxmlElement("w:rFonts")
            rPr.append(rFonts)
        rFonts.set(qn("w:cs"), font_family)


def set_run_rtl_oxml(r_element, font_family: Optional[str] = None) -> None:
    """Same as set_run_rtl but for a raw CT_R element (oxml)."""
    r = r_element
    rPr = r.get_or_add_rPr() if hasattr(r, "get_or_add_rPr") else _get_or_add_child(r, "w:rPr")

    rtl = OxmlElement("w:rtl")
    rPr.append(rtl)

    if font_family:
        # try to get rFonts if exists otherwise create
        rFonts = getattr(rPr, "rFonts", None)
        if rFonts is None:
            rFonts = _find_child(rPr, "w:rFonts")
        if rFonts is None:
            rFonts = OxmlElement("w:rFonts")
            rPr.append(rFonts)
        rFonts.set(qn("w:cs"), font_family)


def _get_or_add_child(parent, tag: str):
    child = _find_child(parent, tag)
    if child is None:
        child = OxmlElement(tag)
        parent.append(child)
    return child


def _find_child(parent, tag: str):
    for c in parent:
        if c.tag == qn(tag):
            return c
    return None

