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


def nc_pdf(project: dict, nc: dict, originals: List[bytes], remediation: List[bytes], status_line: str) -> bytes:
    import pymupdf
    doc = pymupdf.open()
    W, H, M = 595, 842, 40
    page = doc.new_page(width=W, height=H)
    y = M

    def text(t, size=10, bold=False, color=(0, 0, 0)):
        nonlocal page, y
        rect = pymupdf.Rect(M, y, W - M, H - M)
        h = page.insert_textbox(rect, t, fontsize=size, fontname="hebo" if bold else "helv", color=color)
        used = (rect.height - h) if h >= 0 else rect.height
        y += used + 6
        if h < 0 or y > H - 120:
            page = doc.new_page(width=W, height=H)
            y = M

    def section(title, imgs):
        nonlocal page, y
        text(title, 12, True)
        if not imgs:
            text("None provided.", 10, color=(0.4, 0.4, 0.4))
            return
        cw, ch = (W - 2 * M - 10) / 2, 220
        for i, b in enumerate(imgs):
            if i % 2 == 0 and y + ch > H - M:
                page = doc.new_page(width=W, height=H)
                y = M
            x = M + (i % 2) * (cw + 10)
            page.insert_image(pymupdf.Rect(x, y, x + cw, y + ch), stream=b, keep_proportion=True)
            if i % 2 == 1 or i == len(imgs) - 1:
                y += ch + 10

    text(f"{project['address']}{', ' + project['postcode'] if project.get('postcode') else ''}", 14, True)
    text(f"Project {project['ref']}   ·   {nc['number']}", 11)
    text(status_line, 11, True, (0, 0.45, 0.2) if status_line.startswith("INTERNALLY APPROVED") else (0.75, 0.2, 0))
    text("Original TrustMark observation", 11, True)
    text(nc.get("original_text") or nc["description"], 10)
    text("Required remedial work", 11, True)
    text(nc["required_work"], 10)
    section("ORIGINAL TRUSTMARK DEFECT PHOTOGRAPHS (before)", originals)
    section("INSTALLER REMEDIATION PHOTOGRAPHS (after) - stamped with date, time, GPS and project reference", remediation)
    out = doc.tobytes(deflate=True)
    doc.close()
    return out


def build_zip(entries: List[Tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()
