"""Filing PDFs: download once, cache on disk, read text or render pages to images.

Many exchange filings are scans (no text layer), so every consumer must be able
to fall back to page images.
"""
import hashlib
import io
from pathlib import Path

import requests

from . import config
from .announcements import BSE_HEADERS

TIMEOUT = 40
MAX_BYTES = 40 * 1024 * 1024


def _path(url: str) -> Path:
    d = config.cache_dir() / "pdf"
    d.mkdir(parents=True, exist_ok=True)
    return d / (hashlib.sha1(url.encode()).hexdigest()[:20] + ".pdf")


def fetch(url: str) -> Path:
    """Download (or reuse) a filing PDF. Tries BSE's other attachment folder on 404."""
    p = _path(url)
    if p.exists() and p.stat().st_size > 0:
        return p
    candidates = [url]
    if "/AttachLive/" in url:
        candidates.append(url.replace("/AttachLive/", "/AttachHis/"))
    elif "/AttachHis/" in url:
        candidates.append(url.replace("/AttachHis/", "/AttachLive/"))
    last = None
    for u in candidates:
        r = requests.get(u, headers=BSE_HEADERS, timeout=TIMEOUT)
        if r.status_code == 200 and r.content[:5] == b"%PDF-" and len(r.content) <= MAX_BYTES:
            p.write_bytes(r.content)
            return p
        last = f"HTTP {r.status_code}, {len(r.content)} bytes"
    raise RuntimeError(f"could not fetch PDF {url}: {last}")


def page_texts(path: Path, max_pages: int = 12) -> list[str]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    out = []
    for page in reader.pages[:max_pages]:
        try:
            out.append(page.extract_text() or "")
        except Exception:
            out.append("")
    return out


def num_pages(path: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(path)).pages)


def is_scanned(texts: list[str]) -> bool:
    """True if pages carry (almost) no extractable text."""
    return sum(len(t.strip()) for t in texts) < 80 * max(1, len(texts))


def render(path: Path, page_index: int, dpi: int = 110, max_side: int = 1600) -> bytes:
    """Render one page to PNG bytes (0-based index)."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    page = pdf[page_index]
    img = page.render(scale=dpi / 72).to_pil()
    if max(img.size) > max_side:
        ratio = max_side / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)))
    buf = io.BytesIO()
    img.convert("L").save(buf, format="PNG", optimize=True)   # grayscale: tables don't need colour
    return buf.getvalue()
