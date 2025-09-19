"""
Generate a tiny sample PDF with English and French text for testing.
"""

from __future__ import annotations

from pathlib import Path

import fitz


def main() -> None:
    samples_dir = Path(__file__).resolve().parent
    samples_dir.mkdir(parents=True, exist_ok=True)
    output_path = samples_dir / "demo.pdf"

    doc = fitz.open()

    page1 = doc.new_page()
    page1.insert_text(
        (72, 720),
        "This is a demo page with English content.\nUse it to test the translation workflow.",
        fontsize=14,
    )

    page2 = doc.new_page()
    page2.insert_text(
        (72, 720),
        "Voici une page d'exemple en fran\u00e7ais.\nServez-vous-en pour valider la traduction.",
        fontsize=14,
    )

    doc.save(output_path)
    doc.close()
    print(f"Sample PDF written to {output_path}")


if __name__ == "__main__":
    main()