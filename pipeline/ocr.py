"""Text for PDFs that have no text layer. Universal: returns plain text for any
page count and any document type, so every downstream module treats a scan
exactly like a normal document.

Deliberately NOT specialised to the ownership table in the open set's one scan.
The private set may rasterise an 18-page credit agreement instead, and a
table-shaped extractor would be worthless there.

Backends, tried in order:

  1. sidecar   ocr_cache/<doc_id>.txt, committed to the repo
  2. tesseract offline, via PyMuPDF's OCR hook (needs the binary + rus traineddata)
  3. vision    Claude, if ANTHROPIC_API_KEY is set
  4. none      caller is told loudly; the run degrades, it does not crash

The sidecar comes first on purpose. It is the only backend guaranteed to work
with no install, no network and no rate limit, and on Aug 9 a human can produce
one in a few minutes for a page the other backends choke on - no code change,
just drop a file in. Rendering is via PyMuPDF, already a dependency.
"""
from __future__ import annotations

import os
from pathlib import Path

import pymupdf as fitz

CACHE = Path(__file__).resolve().parents[1] / "ocr_cache"
DPI = 200  # enough for 8-9pt body text; higher costs time for no accuracy gain


def sidecar_path(doc_id: str, page: int | None = None) -> Path:
    """Whole-document sidecar, or a per-page one for partially scanned files."""
    return CACHE / (f"{doc_id}.txt" if page is None else f"{doc_id}_p{page}.txt")


def from_sidecar(doc_id: str, page: int | None = None) -> str | None:
    p = sidecar_path(doc_id, page)
    if p.exists():
        text = p.read_text(encoding="utf-8").strip()
        return text or None
    return None


def from_tesseract(path: str | Path, page: int | None = None) -> str | None:
    """PyMuPDF's built-in hook. Needs Tesseract installed and TESSDATA_PREFIX set."""
    try:
        out = []
        with fitz.open(path) as pdf:
            targets = [pdf[page - 1]] if page else list(pdf)
            for pg in targets:
                tp = pg.get_textpage_ocr(language="rus+eng", dpi=DPI, full=True)
                out.append(pg.get_text(textpage=tp))
        text = "\n".join(out).strip()
        return text or None
    except Exception:
        return None  # not installed, or no rus traineddata


def render_pages(path: str | Path, dpi: int = DPI) -> list[bytes]:
    """PNG bytes per page. Used by the vision backend and for producing sidecars."""
    with fitz.open(path) as pdf:
        return [pdf[i].get_pixmap(dpi=dpi).tobytes("png") for i in range(len(pdf))]


def from_vision(path: str | Path, page: int | None = None) -> str | None:
    """Claude vision, one call per page. Only if a key is configured.

    Kept last: on Aug 9 this is the backend most likely to fail, because it is
    the only one needing network and quota during a three-hour window.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import base64

        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic(api_key=key)
        out = []
        pages = render_pages(path)
        for png in ([pages[page - 1]] if page else pages):
            msg = client.messages.create(
                model="claude-sonnet-5",
                max_tokens=4000,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                 "data": base64.b64encode(png).decode()}},
                    {"type": "text", "text":
                        "Transcribe this page verbatim as plain text, preserving all "
                        "numbers, percentages, names and table rows exactly. Render "
                        "tables as 'label: value' lines. Output only the transcription."},
                ]}],
            )
            out.append("".join(b.text for b in msg.content if b.type == "text"))
        text = "\n".join(out).strip()
        return text or None
    except Exception:
        return None


def text_for(doc_id: str, path: str | Path, page: int | None = None) -> tuple[str | None, str]:
    """Returns (text, backend_used). text is None when nothing could read it.

    `page` (1-indexed) reads a single rasterised page out of an otherwise-text
    document — the partial-scan case, which is the dangerous one.
    """
    if t := from_sidecar(doc_id, page):
        return t, "sidecar"
    if t := from_tesseract(path, page):
        CACHE.mkdir(exist_ok=True)
        sidecar_path(doc_id, page).write_text(t, encoding="utf-8")  # cache: OCR is slow
        return t, "tesseract"
    if t := from_vision(path, page):
        CACHE.mkdir(exist_ok=True)
        sidecar_path(doc_id, page).write_text(t, encoding="utf-8")
        return t, "vision"
    return None, "none"


def dump_pages(path: str | Path, out_dir: str | Path, dpi: int = DPI) -> list[Path]:
    """Write page PNGs so a human (or this agent) can read and transcribe them
    into a sidecar. The manual escape hatch, made one command long."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(path).stem
    written = []
    for i, png in enumerate(render_pages(path, dpi), 1):
        p = out_dir / f"{stem}_p{i}.png"
        p.write_bytes(png)
        written.append(p)
    return written
