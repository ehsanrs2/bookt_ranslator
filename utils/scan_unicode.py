#!/usr/bin/env python3
"""
Scan text files or directory trees for suspicious Unicode characters such as
Private Use Area glyphs, control/format characters, and the replacement
character U+FFFD. Optionally fix files in-place using a safe sanitizer.

Examples:
  - Scan a folder recursively:
      python3 utils/scan_unicode.py ./data --include-ext .txt .sql .md
  - Fix files in place with backups (.bak):
      python3 utils/scan_unicode.py dump.sql --fix
  - Print JSON report:
      python3 utils/scan_unicode.py dumps --report report.json

This script is standalone (stdlib only) to work without project extras.
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


SUSPECT_DISPLAY_OPEN = "⟦"
SUSPECT_DISPLAY_CLOSE = "⟧"


def is_private_use(cp: int) -> bool:
    # BMP PUA
    if 0xE000 <= cp <= 0xF8FF:
        return True
    # Supplementary PUA-A/B
    if 0xF0000 <= cp <= 0xFFFFD or 0x100000 <= cp <= 0x10FFFD:
        return True
    return False


def is_disallowed_control(ch: str) -> bool:
    # Keep newline, carriage return, and tab; drop other controls and formats
    if ch in ("\n", "\r", "\t"):
        return False
    cat = unicodedata.category(ch)
    return cat in ("Cc", "Cf")


def is_suspect_char(ch: str) -> bool:
    cp = ord(ch)
    if cp == 0xFFFD:  # replacement char
        return True
    if is_private_use(cp):
        return True
    if is_disallowed_control(ch):
        return True
    # NBSPs and other odd spaces are flagged as suspicious for dumps
    if cp in (0x00A0, 0x2007, 0x202F):
        return True
    return False


def sanitize_text(text: str) -> str:
    """Lossy sanitization: remove PUA, controls/format chars, U+FFFD, unify spaces.

    Mirrors the core of utils.text.clean_block_text without external deps.
    """
    # Normalize line breaks
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Remove visible PDF/control artifacts we often see
    text = (
        text.replace("\u200C", " ")  # ZWJ -> space
        .replace("\u200F", "")  # RLM
        .replace("\u202A", "")  # LRE
        .replace("\u202C", "")  # PDF (pop directional formatting)
        .replace("\u00AD", "")  # soft hyphen
        .replace("\ufeff", "")  # BOM
        .replace("\u00A0", " ")  # NBSP -> space
    )
    # Unicode normalize
    text = unicodedata.normalize("NFKC", text)
    # Filter characters
    kept: List[str] = []
    for ch in text:
        if ch in ("\n", "\r", "\t", " "):
            kept.append(ch)
            continue
        cp = ord(ch)
        if cp == 0xFFFD:
            continue
        if is_private_use(cp):
            continue
        if is_disallowed_control(ch):
            continue
        kept.append(ch)
    text = "".join(kept)
    # Tidy whitespace (collapse runs of spaces per line)
    lines = []
    for line in text.splitlines():
        # Replace tabs with single spaces and collapse multiple spaces
        line = line.replace("\t", " ")
        while "  " in line:
            line = line.replace("  ", " ")
        lines.append(line.strip())
    return "\n".join(lines).strip()


def visible_excerpt(line: str) -> str:
    parts: List[str] = []
    for ch in line:
        if is_suspect_char(ch):
            parts.append(f"{SUSPECT_DISPLAY_OPEN}U+{ord(ch):04X}{SUSPECT_DISPLAY_CLOSE}")
        else:
            # Coerce control whitespace to printable form for display
            if ch == "\t":
                parts.append("\\t")
            else:
                parts.append(ch)
    return "".join(parts)


@dataclass
class LineFinding:
    lineno: int
    count: int
    excerpt: str


@dataclass
class FileReport:
    path: str
    size: int
    suspect_chars: int
    suspect_lines: List[LineFinding]


def scan_text(text: str) -> Tuple[int, List[LineFinding]]:
    total = 0
    findings: List[LineFinding] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        count = sum(1 for ch in raw if is_suspect_char(ch))
        if count:
            total += count
            findings.append(LineFinding(lineno=i, count=count, excerpt=visible_excerpt(raw)))
    return total, findings


def scan_file(path: Path, max_bytes: int) -> Optional[FileReport]:
    try:
        if path.is_dir():
            return None
        size = path.stat().st_size
        if size > max_bytes:
            return FileReport(str(path), size, 0, [])
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return FileReport(str(path), -1, 0, [])
    total, findings = scan_text(text)
    return FileReport(str(path), len(text), total, findings)


def iter_files(paths: Iterable[Path], include_ext: Optional[Tuple[str, ...]], all_files: bool) -> Iterable[Path]:
    exts = tuple(e.lower() for e in include_ext) if include_ext else None
    for p in paths:
        if p.is_dir():
            for sub in p.rglob("*"):
                if sub.is_file() and (all_files or _accept_ext(sub, exts)):
                    yield sub
        elif p.is_file():
            if all_files or _accept_ext(p, exts):
                yield p


def _accept_ext(path: Path, exts: Optional[Tuple[str, ...]]) -> bool:
    if exts is None:
        # Sensible defaults for DB/text dumps
        exts = (".txt", ".sql", ".csv", ".json", ".md", ".tex", ".html", ".xml")
    return path.suffix.lower() in exts


def write_report_json(reports: List[FileReport], out_path: Path) -> None:
    payload = {
        "files": [
            {
                **asdict(fr),
                "suspect_lines": [asdict(l) for l in fr.suspect_lines],
            }
            for fr in reports
        ]
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fix_file(path: Path, make_backup: bool) -> Tuple[bool, Optional[str]]:
    try:
        original = path.read_text(encoding="utf-8", errors="replace")
        cleaned = sanitize_text(original)
        if cleaned == original:
            return False, None
        if make_backup:
            backup = path.with_suffix(path.suffix + ".bak")
            if not backup.exists():
                backup.write_text(original, encoding="utf-8")
        path.write_text(cleaned, encoding="utf-8")
        return True, None
    except Exception as exc:
        return False, str(exc)


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scan for suspicious Unicode characters in text/DB dump files.")
    p.add_argument("paths", nargs="+", help="Files or directories to scan.")
    p.add_argument("--include-ext", nargs="*", default=None, help="File extensions to include (default: common text/dumps).")
    p.add_argument("--all-files", action="store_true", help="Scan all files regardless of extension.")
    p.add_argument("--max-bytes", type=int, default=25_000_000, help="Skip files larger than this many bytes (default 25MB).")
    p.add_argument("--report", help="Optional path to write a JSON report with findings.")
    p.add_argument("--fix", action="store_true", help="Sanitize files in place using a safe, lossy cleaner.")
    p.add_argument("--no-backup", action="store_true", help="Do not write .bak backups when using --fix.")
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    paths = [Path(p).expanduser().resolve() for p in args.paths]
    include_ext = tuple(args.include_ext) if args.include_ext else None

    reports: List[FileReport] = []
    total_files = 0
    total_suspect = 0

    for file_path in iter_files(paths, include_ext, args.all_files):
        total_files += 1
        rep = scan_file(file_path, args.max_bytes)
        if rep is None:
            continue
        reports.append(rep)
        total_suspect += rep.suspect_chars

        if rep.suspect_chars:
            print(f"\n==> {rep.path}  (chars: {rep.suspect_chars})")
            for lf in rep.suspect_lines[:200]:  # cap output per file
                print(f"  L{lf.lineno:>5}: {lf.excerpt}")

        if args.fix and rep.suspect_chars:
            changed, err = fix_file(Path(rep.path), make_backup=not args.no_backup)
            if err:
                print(f"  !! Failed to fix: {err}")
            elif changed:
                print("  -- Fixed and sanitized.")
            else:
                print("  -- Already clean (no changes).")

    # Summary
    print(f"\nScanned files: {total_files} | Suspect characters: {total_suspect}")

    if args.report:
        out = Path(args.report).expanduser().resolve()
        write_report_json(reports, out)
        print(f"Report saved to {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

