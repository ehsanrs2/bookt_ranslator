"""
DOCX translation driver: traverses a .docx, translates EN/FR to FA, preserves structure and inline formatting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from docx import Document
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from lxml.etree import _Element
from tqdm import tqdm

from docxio.rtl import set_paragraph_rtl, set_run_rtl, set_run_rtl_oxml
from docxio.walker import DocxWalker, TextUnit
from translator.googletrans_client import TranslationError, TranslatorClient
from utils import filters as filt


LOGGER = logging.getLogger(__name__)


# Private-use markers for run boundaries and protected tokens.
OPEN_MARK = "\uE010"       # run start
CLOSE_MARK = "\uE011"      # run end
PROTECT_OPEN = "\uE020"   # protected token start
PROTECT_CLOSE = "\uE021"  # protected token end
PAR_OPEN = "\uE030"       # paragraph start (aggregation)
PAR_CLOSE = "\uE031"      # paragraph end (aggregation)


@dataclass
class TranslateOptions:
    src_lang: str = "auto"
    tgt_lang: str = "fa"
    font_family: Optional[str] = None
    preserve_inline: bool = True
    skip_fields: bool = True
    skip_urls: bool = True
    skip_numeric: bool = True
    include_headers: bool = True
    include_footers: bool = True
    include_shapes: bool = True
    simple_mode: bool = False  # force per-run translation
    debug: bool = False
    # Aggregation of multiple paragraphs into a single request
    agg: bool = True
    agg_max_chars: int = 3800
    agg_max_items: int = 32


def translate_docx(
    input_path: str,
    output_path: str,
    *,
    translator: TranslatorClient,
    options: TranslateOptions,
) -> None:
    """Translate a DOCX file in-place and save to output_path."""
    doc = Document(input_path)
    walker = DocxWalker(
        doc,
        include_headers=options.include_headers,
        include_footers=options.include_footers,
        include_shapes=options.include_shapes,
        skip_fields=options.skip_fields,
    )

    units: List[TextUnit] = list(walker.iter_units())
    if not units:
        LOGGER.info("No text units found to translate.")
        doc.save(output_path)
        return

    if options.debug:
        LOGGER.debug("Collected %d text units", len(units))

    # Decide batchable payloads: paragraph-level text for preserve-inline, otherwise per run.
    if options.simple_mode or not options.preserve_inline:
        _translate_simple(units, translator, options)
    else:
        _translate_with_markers(units, translator, options)

    doc.save(output_path)


def _translate_simple(units: Sequence[TextUnit], translator: TranslatorClient, options: TranslateOptions) -> None:
    payloads: List[str] = []
    mapping: List[Tuple[TextUnit, int, int]] = []  # (unit, run_index, payload_index)
    for unit in units:
        for i, run in enumerate(unit.runs):
            text = _get_run_text(run)
            if not text:
                continue
            if options.skip_urls and filt.is_url(text):
                continue
            if options.skip_numeric and filt.is_numeric_heavy(text):
                continue
            if isinstance(run, Run) and filt.is_code_style(run):
                continue
            mapping.append((unit, i, len(payloads)))
            payloads.append(text)

    results: List[str] = []
    if payloads:
        results = translator.translate_batch(payloads, src=options.src_lang, tgt=options.tgt_lang)

    # Apply back to runs
    for (unit, run_idx, payload_idx) in tqdm(mapping, desc="Apply runs", unit="run"):
        translated_text = results[payload_idx]
        _set_run_text(unit.runs[run_idx], _normalize_par_text(translated_text))
        _apply_rtl_to_run(unit.runs[run_idx], options.font_family)
        _apply_rtl_to_paragraph(unit)


def _translate_with_markers(units: Sequence[TextUnit], translator: TranslatorClient, options: TranslateOptions) -> None:
    # Build marked payload per paragraph
    payloads: List[str] = []
    indices: List[int] = []  # unit index -> payload index

    prepared: List[_MarkedUnit] = []
    for unit in units:
        marked = _build_marked_paragraph(unit, options)
        if not marked or not marked.combined_text.strip():
            prepared.append(marked)
            indices.append(-1)
            continue
        indices.append(len(payloads))
        payloads.append(marked.combined_text)
        prepared.append(marked)

    results: List[str] = []
    if payloads:
        try:
            if options.agg:
                results = _aggregate_translate(payloads, translator, options)
            else:
                results = translator.translate_batch(payloads, src=options.src_lang, tgt=options.tgt_lang)
        except TranslationError as exc:
            LOGGER.warning("Batch translation failed, falling back to simple mode: %s", exc)
            _translate_simple(units, translator, options)
            return

    # Apply per paragraph
    for u_idx, unit in enumerate(tqdm(units, desc="Apply paragraphs", unit="par")):
        marked = prepared[u_idx]
        if marked is None or indices[u_idx] == -1:
            _apply_rtl_to_paragraph(unit)
            continue

        translated = results[indices[u_idx]]
        ok = _distribute_translated(marked, translated, options)
        if not ok:
            # Fallback per-run for this paragraph
            if options.debug:
                LOGGER.debug("Markers failed for %s; switching to per-run fallback.", unit.location)
            _translate_simple([unit], translator, options)
        _apply_rtl_to_paragraph(unit)


def _apply_rtl_to_paragraph(unit: TextUnit) -> None:
    # Only Paragraph objects get alignment; shapes keep run-level rtl
    if isinstance(unit.paragraph, Paragraph):
        set_paragraph_rtl(unit.paragraph)


def _apply_rtl_to_run(run_obj, font_family: Optional[str]) -> None:
    if isinstance(run_obj, Run):
        set_run_rtl(run_obj, font_family)
    else:
        set_run_rtl_oxml(run_obj, font_family)


def _get_run_text(run_obj) -> str:
    if isinstance(run_obj, Run):
        return run_obj.text or ""
    # oxml run
    texts = run_obj.xpath('.//w:t')
    return "".join([(t.text or "") for t in texts])


def _set_run_text(run_obj, value: str) -> None:
    if isinstance(run_obj, Run):
        run_obj.text = value
        return
    # Clear all existing w:t and create one
    for t in run_obj.xpath('.//w:t'):
        parent = t.getparent()
        parent.remove(t)
    r = run_obj
    rPr = getattr(r, "rPr", None)
    # new w:t
    from docx.oxml import OxmlElement  # local import to avoid docx oxml types at module top
    t = OxmlElement("w:t")
    if value and (value.startswith(" ") or value.endswith(" ")):
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = value
    # append t to run (after rPr if present)
    if rPr is None:
        run_obj.append(t)
    else:
        insert_pos = list(run_obj).index(rPr) + 1
        run_obj.insert(insert_pos, t)


def _normalize_par_text(text: str) -> str:
    # Avoid embedded newlines inside a single paragraph; replace with space
    return (text or "").replace("\r", " ").replace("\n", " ")


@dataclass
class _MarkedUnit:
    unit: TextUnit
    combined_text: str
    run_indices: List[int]
    protected_map: Dict[str, str]  # key -> original token


def _build_marked_paragraph(unit: TextUnit, options: TranslateOptions) -> Optional[_MarkedUnit]:
    runs = list(unit.runs)
    if not runs:
        return _MarkedUnit(unit, "", [], {})

    parts: List[str] = []
    protected: Dict[str, str] = {}
    used_runs: List[int] = []

    for idx, run in enumerate(runs):
        raw = _get_run_text(run)
        if not raw:
            continue
        # Skip certain runs entirely
        if options.skip_numeric and filt.is_numeric_heavy(raw):
            continue
        if isinstance(run, Run) and filt.is_code_style(run):
            # Protect unchanged
            token = f"{PROTECT_OPEN}K{idx}{PROTECT_CLOSE}"
            protected[token] = raw
            parts.append(token)
            used_runs.append(idx)
            continue
        if options.skip_urls and filt.is_url(raw):
            token = f"{PROTECT_OPEN}U{idx}{PROTECT_CLOSE}"
            protected[token] = raw
            parts.append(token)
            used_runs.append(idx)
            continue

        used_runs.append(idx)
        parts.append(f"{OPEN_MARK}{idx}{OPEN_MARK}")
        parts.append(raw)
        parts.append(f"{CLOSE_MARK}{idx}{CLOSE_MARK}")

    combined = "".join(parts)
    return _MarkedUnit(unit=unit, combined_text=combined, run_indices=used_runs, protected_map=protected)


def _distribute_translated(marked: _MarkedUnit, translated: str, options: TranslateOptions) -> bool:
    # Restore protected tokens first
    restored = translated
    for token, original in marked.protected_map.items():
        restored = restored.replace(token, original)

    # Extract segments for each run index
    segments: Dict[int, str] = {}
    remaining = restored
    try:
        for idx in marked.run_indices:
            start_tag = f"{OPEN_MARK}{idx}{OPEN_MARK}"
            end_tag = f"{CLOSE_MARK}{idx}{CLOSE_MARK}"
            s_pos = remaining.find(start_tag)
            e_pos = remaining.find(end_tag)
            if s_pos == -1 or e_pos == -1 or e_pos < s_pos:
                return False
            content = remaining[s_pos + len(start_tag) : e_pos]
            segments[idx] = _normalize_par_text(content)
            # Remove processed prefix to keep search stable and allow duplicates in later runs
            remaining = remaining[e_pos + len(end_tag) :]
    except Exception:
        return False

    # Apply back to runs; clear all first to avoid leftover content
    for idx in marked.run_indices:
        text = segments.get(idx, "")
        _set_run_text(marked.unit.runs[idx], text)
        _apply_rtl_to_run(marked.unit.runs[idx], options.font_family)

    return True


def _aggregate_translate(
    texts: List[str], translator: TranslatorClient, options: TranslateOptions
) -> List[str]:
    """Translate multiple paragraphs by aggregating several into a single request.

    Uses paragraph-level markers to split the translated output back to items.
    Falls back to per-item translation for any pack that fails marker parsing.
    """
    outputs: List[Optional[str]] = [None] * len(texts)

    idx = 0
    n = len(texts)
    base_overhead = 20  # approx marker overhead per paragraph
    while idx < n:
        pack_map: List[int] = []  # local_i -> global index
        current: List[str] = []
        current_len = 0
        while idx < n and len(pack_map) < options.agg_max_items:
            t = texts[idx]
            if not t:
                outputs[idx] = ""
                idx += 1
                continue
            est = len(t) + base_overhead
            if current and (current_len + est) > options.agg_max_chars:
                break
            local_i = len(pack_map)
            current.append(f"{PAR_OPEN}{local_i}{PAR_OPEN}{t}{PAR_CLOSE}{local_i}{PAR_CLOSE}")
            pack_map.append(idx)
            current_len += est
            idx += 1

        if not pack_map:
            # nothing to translate in this iteration (all empties), continue
            continue

        aggregated = "\n".join(current)
        try:
            agg_translated = translator.translate_text(aggregated, src=options.src_lang, tgt=options.tgt_lang)
        except TranslationError:
            # Fallback to per-item
            individuals = translator.translate_batch([texts[i] for i in pack_map], src=options.src_lang, tgt=options.tgt_lang)
            for j, gi in enumerate(pack_map):
                outputs[gi] = individuals[j]
            continue

        # Parse aggregated translation back
        ok = True
        for local_i, gi in enumerate(pack_map):
            start_tag = f"{PAR_OPEN}{local_i}{PAR_OPEN}"
            end_tag = f"{PAR_CLOSE}{local_i}{PAR_CLOSE}"
            s = agg_translated.find(start_tag)
            e = agg_translated.find(end_tag)
            if s == -1 or e == -1 or e < s:
                ok = False
                break
            seg = agg_translated[s + len(start_tag) : e]
            outputs[gi] = seg
            # remove the processed portion to keep search simpler for the next local_i occurrences
            agg_translated = agg_translated[e + len(end_tag) : ]

        if not ok:
            # fallback this pack
            individuals = translator.translate_batch([texts[i] for i in pack_map], src=options.src_lang, tgt=options.tgt_lang)
            for j, gi in enumerate(pack_map):
                outputs[gi] = individuals[j]

    return [o or "" for o in outputs]
