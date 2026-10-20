"""TrustMark Evidence Pack: one PDF per NC (before/after photos) + Videos/ folder, built with PyMuPDF."""
import io
import re
import zipfile
from typing import List, Tuple

from PIL import Image, ImageFilter, ImageStat


def slug(text: str, words: int = 4) -> str:
    w = re.findall(r"[A-Za-z0-9]+", text or "")[:words]
    return "_".join(x.capitalize() for x in w) or "NC"


def dhash(data: bytes) -> int:
    img = Image.open(io.BytesIO(data)).convert("L").resize((9, 8))
    px = list(img.getdata())
    return sum(1 << i for i in range(64) if px[(i // 8) * 9 + i % 8] > px[(i // 8) * 9 + i % 8 + 1])


def sharpness(data: bytes) -> float:
    img = Image.open(io.BytesIO(data)).convert("L")
    img.thumbnail((640, 640))
    edges = ImageStat.Stat(img.filter(ImageFilter.FIND_EDGES)).var[0]
    bright = ImageStat.Stat(img).mean[0]
    return edges * (0.4 if bright < 40 or bright > 230 else 1.0)  # penalise very dark / blown-out shots


def readable(imgs: List[bytes]) -> List[bytes]:
    """Drop corrupt/truncated images so one bad file can never break a whole evidence pack."""
    ok = []
    for b in imgs:
        try:
            Image.open(io.BytesIO(b)).convert("L")
            ok.append(b)
        except Exception:
            continue
    return ok


def select_photos(photos: List[bytes], limit: int = 6) -> List[bytes]:
    """Best first by sharpness/lighting; drops exact and near-duplicates (dHash distance <= 6)."""
    scored = sorted(((sharpness(b), dhash(b), b) for b in photos), key=lambda x: -x[0])
    chosen: List[Tuple[int, bytes]] = []
    for _, h, b in scored:
        if all(bin(h ^ ch).count("1") > 6 for ch, _ in chosen):
            chosen.append((h, b))
        if len(chosen) >= limit:
            break
    return [b for _, b in chosen]


def repeated_hashes(per_nc: List[List[bytes]]) -> set:
    """dHashes of images that appear on 2+ NCs (logos, headers, footers) - excluded from 'before' photos."""
    seen: List[Tuple[int, int]] = []  # (hash, nc_index)
    rep = set()
    for i, imgs in enumerate(per_nc):
        for b in imgs:
            h = dhash(b)
            for sh, si in seen:
                if si != i and bin(h ^ sh).count("1") <= 4:
                    rep.update({h, sh})
            seen.append((h, i))
    return rep


def drop_repeated(imgs: List[bytes], rep: set) -> List[bytes]:
    return [b for b in imgs if not any(bin(dhash(b) ^ r).count("1") <= 4 for r in rep)]


def _pdf_image(b: bytes, max_px: int = 1600) -> Tuple[bytes, float]:
    """Readable-quality JPEG (long edge <= max_px) and its aspect ratio (w/h). Keeps PDFs a sensible size."""
    from PIL import ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(b))).convert("RGB")
    img.thumbnail((max_px, max_px))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue(), img.width / max(img.height, 1)


def nc_pdf(project: dict, nc: dict, originals: List[bytes], remediation: List[bytes]) -> bytes:
    """Clean client-facing NC sheet: address, references, original TrustMark wording, before/after photos and an
    optional staff note. Deliberately contains NO internal workflow data (approvals, reviewers, AI, statuses)."""
    import pymupdf
    doc = pymupdf.open()
    W, H, M, GAP = 595, 842, 36, 8
    BLACK = (0, 0, 0)
    page = doc.new_page(width=W, height=H)
    y = M

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=W, height=H)
        y = M

    def text(t, size=10, bold=False, after=4):
        nonlocal y
        for _ in range(2):  # retry once on a fresh page if it doesn't fit
            rect = pymupdf.Rect(M, y, W - M, H - M)
            left = page.insert_textbox(rect, t, fontsize=size, fontname="hebo" if bold else "helv", color=BLACK)
            if left >= 0:
                y += rect.height - left + after
                return
            new_page()

    def heading(t):
        nonlocal y
        if y > H - M - 60:
            new_page()
        y += 6
        text(t, 9.5, True, 3)

    def photos(title, imgs):
        nonlocal y
        if not imgs:
            return
        prepared = [_pdf_image(b) for b in imgs]
        full = W - 2 * M
        cols = 1 if len(prepared) == 1 else 2
        cw = (full - GAP * (cols - 1)) / cols
        cap = 330 if cols == 1 else 250
        rows = [prepared[i:i + cols] for i in range(0, len(prepared), cols)]
        first_h = max(min(cw / a, cap) for _, a in rows[0])
        if y + 22 + first_h > H - M:  # keep the heading with its first row of photos
            new_page()
        heading(title)
        for row in rows:
            rh = max(min(cw / a, cap) for _, a in row)
            if y + rh > H - M:
                new_page()
            for i, (b, a) in enumerate(row):
                x = M + i * (cw + GAP)
                page.insert_image(pymupdf.Rect(x, y, x + cw, y + rh), stream=b, keep_proportion=True)
            y += rh + GAP

    addr = f"{project.get('address', '')}{', ' + project['postcode'] if project.get('postcode') else ''}"
    text(addr, 14, True, 2)
    text(f"Project Reference: {project['ref']}   |   NC Reference: {nc['number']}", 10, False, 4)
    page.draw_line((M, y), (W - M, y), color=BLACK, width=0.6)
    y += 4
    heading("NON-COMPLIANCE")
    text(nc.get("original_text") or nc.get("description", ""), 10)
    photos("ORIGINAL TRUSTMARK DEFECT PHOTOGRAPHS", originals)
    if remediation:
        photos("REMEDIATION PHOTOGRAPHS", remediation)
    else:
        heading("REMEDIATION PHOTOGRAPHS")
        text("Not yet supplied.", 10)
    if (nc.get("export_note") or "").strip():
        heading("REMEDIATION NOTE")
        text(nc["export_note"].strip(), 10)
    out = doc.tobytes(deflate=True, garbage=3)
    doc.close()
    return out


def build_zip(entries: List[Tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()
