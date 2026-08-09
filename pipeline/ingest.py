"""Archive -> Doc objects. Tolerant by design: a bad PDF degrades, never raises.

Handles the two scan cases: a wholly rasterised document, and - far more
dangerous - a text document with individual pages rasterised, which reads as
perfectly fine while silently losing whatever was on those pages.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pymupdf as fitz


@dataclass
class Page:
    number: int  # 1-indexed, as a human reads it
    text: str


@dataclass
class Doc:
    doc_id: str
    path: str
    pages: list
    ok: bool = True  # False => ingest degraded (no text layer, corrupt, etc.)
    note: str = ""

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.pages)

PDF_EXT = {".pdf"}

# Real PDFs are full of lookalike Unicode: NBSP instead of space, soft hyphen or
# en-dash instead of '-', curly quotes, zero-width joiners. Downstream regexes
# then silently fail to match. Normalize once, here, so nothing below has to care.
# ponytail: U+00AD -> '-' keeps identifiers like DOC-001 intact. That is the wrong
# call for end-of-line hyphenation (should rejoin the word); revisit only if the
# real dataset actually hyphenates across lines.
_TRANSLATE = str.maketrans({
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    "­": "-", "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "​": "", "‌": "", "‍": "", "﻿": "",
})


def normalize_text(s: str) -> str:
    return (s or "").translate(_TRANSLATE)


def doc_id_from_path(path: Path) -> str:
    """SWAP ON AUG 6 once we see the real naming convention."""
    return path.stem


SCAN_PAGE_MAX_CHARS = 20  # a rasterised page still carries its page number


def is_scan_page(page: Page, n_images: int) -> bool:
    """An image-bearing page whose only text is a page number is a scan."""
    return n_images > 0 and len(page.text.strip()) < SCAN_PAGE_MAX_CHARS


@lru_cache(maxsize=None)
def load_pdf(path: Path) -> Doc:
    """Parsing 200 PDFs dominates wall clock, and the index plus the engine each
    open the same files. Memoised by path: callers treat Doc as read-only.
    On Aug 9 there will be many runs inside a three-hour window."""
    return _load_pdf(path)


def _load_pdf(path: Path) -> Doc:
    doc_id = doc_id_from_path(path)
    page_images: dict[int, int] = {}
    try:
        with fitz.open(path) as pdf:
            pages = []
            for i, page in enumerate(pdf, start=1):
                page_images[i] = len(page.get_images())
                text = normalize_text(page.get_text("text"))
                pages.append(Page(number=i, text=text))
    except Exception as e:
        return Doc(doc_id=doc_id, path=str(path), pages=[], ok=False, note=f"unreadable: {e}")

    from . import ocr

    if not any(p.text.strip() for p in pages):
        # Whole document is a scan.
        text, backend = ocr.text_for(doc_id, path)
        if text:
            # One page: downstream cares about content, not pagination, and OCR
            # backends do not reliably preserve page boundaries.
            return Doc(doc_id=doc_id, path=str(path),
                       pages=[Page(number=1, text=normalize_text(text))],
                       note=f"ocr:{backend}")
        return Doc(doc_id=doc_id, path=str(path), pages=pages, ok=False,
                   note="no text layer, no OCR backend")

    # PARTIAL scan: a text document with individual pages rasterised. Three files
    # in the open set do this, and it is far more dangerous than a full scan -
    # the document reads as fine, so the missing page is lost in silence. P2's
    # KYC hides its entire ownership table and threshold on such a page.
    missing = [p.number for p in pages if is_scan_page(p, page_images.get(p.number, 0))]
    if missing:
        filled, failed = [], []
        for p in pages:
            if p.number in missing:
                text, backend = ocr.text_for(doc_id, path, page=p.number)
                if text:
                    p.text = normalize_text(text)
                    filled.append(p.number)
                else:
                    failed.append(p.number)
        note = (f"ocr pages {filled}" if filled else "") + \
               (f" UNREAD pages {failed}" if failed else "")
        # ok=False when a page could not be read: silently dropping it is what
        # this whole branch exists to prevent.
        return Doc(doc_id=doc_id, path=str(path), pages=pages,
                   ok=not failed, note=note.strip())

    return Doc(doc_id=doc_id, path=str(path), pages=pages)


def load_archive(root: str | Path) -> list[Doc]:
    root = Path(root)
    docs = [load_pdf(p) for p in sorted(root.rglob("*")) if p.suffix.lower() in PDF_EXT]
    return docs


def summary(docs: list[Doc]) -> str:
    bad = [d for d in docs if not d.ok]
    line = f"{len(docs)} docs, {sum(len(d.pages) for d in docs)} pages"
    if bad:
        line += f", {len(bad)} DEGRADED: " + ", ".join(f"{d.doc_id}({d.note})" for d in bad)
    return line
