
# PDF and DOCX Persian Translator CLI

## Quick Start

1. Activate the prepared environment:

   ```bash
   conda activate translate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Generate the demo PDF (workspace is read-only in some sandboxes, run locally if needed):

   ```bash
   python samples/make_demo.py
   ```

4. Run a dry-run to verify extraction and translation without writing a PDF:

   ```bash
   python translate_pdf.py samples/demo.pdf --dry-run --log-level DEBUG
   ```

5. Produce the translated PDF with debug overlays:

   ```bash
   python translate_pdf.py samples/demo.pdf --out samples/demo_fa_fixed.pdf --src auto --tgt fa --font fonts/Vazirmatn-Regular.ttf --debug-layout --skip-small --overwrite
   ```

## CLI Options

- `--src` / `--tgt`: language codes understood by googletrans (`auto`, `en`, `fr`, etc.).
- `--max-chars`: chunk size to satisfy unofficial Google API limits (default 450).
- `--min-block-chars`: drop very short snippets.
- `--skip-small`: filter blocks that look like schematic labels or coordinates.
- `--line-gap`, `--min-font`, `--max-font`: control paragraph spacing and auto-fitted font sizes.
- `--debug-layout`: outline block rectangles and line baselines to inspect RTL layout decisions.
- `--shrink-to-fit`: keep shrinking the font down to `--min-font` instead of eliding text when space is tight.
- `--font`: TTF font path embedded into the output (default Vazirmatn).
- `--cache`: SQLite file storing translations to avoid redundant requests.
- `--dry-run`: extract and translate without modifying the PDF; prints sample pairs.
- `--overwrite`: allow clobbering an existing output file.

---

## DOCX Translation

Translate Microsoft Word `.docx` files while preserving paragraph structure, inline formatting (bold/italic/underline), lists, headings, tables, hyperlinks, headers/footers, and text boxes.

### Quick Start

1. Ensure dependencies are installed:

   ```bash
   pip install -r requirements.txt
   ```

2. Create the demo DOCX (optional):

   ```bash
   python samples/make_docx_demo.py
   ```

3. Run the DOCX translator:

   ```bash
   python translate_docx.py samples/demo.docx --out samples/demo_fa.docx \
     --src auto --tgt fa --font "Vazirmatn" --cache .cache/gt_cache.sqlite \
     --preserve-inline --skip-fields --skip-urls --skip-numeric --debug
   ```

### CLI Options (DOCX)

- `input_docx`: path to the source `.docx` file.
- `--out PATH`: output `.docx` path (required).
- `--src {auto,en,fr}`: source language (default: `auto`).
- `--tgt CODE`: target language (default: `fa`).
- `--font NAME`: complex script font family to set on runs (default: `Vazirmatn`).
- `--cache PATH`: SQLite cache path; reuses the PDF cache implementation.
- `--preserve-inline` / `--no-preserve-inline`: preserve bold/italic/underline by paragraph-level translation with run markers (default on); `--no-preserve-inline` uses per-run translation.
- `--skip-fields`: skip field-code paragraphs (TOC, PAGE, REF, HYPERLINK, SEQ). Recommended to keep enabled to avoid breaking automatic fields.
- `--skip-urls`: protect URLs and emails; translate only visible hyperlink text.
- `--skip-numeric`: skip paragraphs/runs with mostly digits/symbols.
- `--headers` / `--no-headers`: traverse headers (default: on; use `--no-headers` to disable).
- `--footers` / `--no-footers`: traverse footers (default: on; use `--no-footers` to disable).
- `--shapes` / `--no-shapes`: traverse text boxes/shapes (default: on; use `--no-shapes` to disable).
- `--agg` / `--no-agg`: enable/disable chunked aggregation across paragraphs (default: on). Aggregation reduces API calls by combining multiple paragraphs into one request using robust markers.
- `--agg-max-chars N`: max characters per aggregated request (default: 3800). Keep under upstream limits.
- `--agg-max-items N`: max paragraphs per aggregated request (default: 32).
- `--lists`: reserved; lists are handled as regular paragraphs.
- `--debug`: verbose logging.
- `--log-level`: set log level (`DEBUG`, `INFO`, etc.).

### Notes & Limitations

- Complex shapes and embedded diagrams may be skipped; only run text is replaced. Images are untouched.
- Field code paragraphs (e.g., automatic TOC) are skipped when `--skip-fields` is used.
- Inline preservation uses private-use Unicode markers. In the rare case markers are disturbed by MT, the translator falls back to per-run translation for that paragraph.
- Chunked aggregation currently groups paragraph-level translations (best results with `--preserve-inline`). If a pack fails marker parsing, it falls back to per-paragraph translation for that pack.



## How It Works

- `pdfio.layout` gathers text blocks with PyMuPDF, deduplicates near-identical rectangles, and applies heuristics to skip noise (URLs, labels, number-heavy blocks).
- `utils.text` cleans content, chunks long passages, wraps RTL text using measured widths, and reshapes output with `arabic_reshaper` + `python-bidi`.
- `translator.googletrans_client` wraps googletrans (4.0.0rc1) with batching, retry backoff, and optional caching.
- `pdfio.draw` paints the original block white, binary-searches an appropriate font size, and draws right-aligned Persian text with `fitz.TextWriter`.
- `translate_pdf.py` orchestrates everything with progress reporting via `tqdm` and structured logging.

## Notes & Caveats

- googletrans uses an unofficial Google endpoint and may break or throttle. The retry logic backs off exponentially, but you might still hit rate limits.
- Ensure the selected font supports Persian glyphs; Vazirmatn is bundled by default.
- The SQLite cache is optional but recommended for large PDFs to reduce repeated translations.
- Run with `--dry-run` whenever you tweak heuristics to inspect which blocks are being translated before overwriting valuable PDFs.
