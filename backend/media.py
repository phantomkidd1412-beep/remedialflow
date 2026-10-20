"""Image stamping / resizing and video frame extraction (CPU work, run in threadpool)."""
import io
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # pragma: no cover
    FFMPEG = None


def local_time(captured_at: Optional[str], tz_offset_min: Optional[int]) -> datetime:
    try:
        dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now(timezone.utc)
    if tz_offset_min is not None:
        dt = dt.astimezone(timezone(timedelta(minutes=tz_offset_min)))
    return dt


def stamp_lines(meta: dict) -> List[str]:
    gallery = meta.get("source") == "gallery"
    if gallery and not meta.get("captured_at"):
        l1 = "DATE TAKEN: NOT AVAILABLE"  # never stamp the upload time as if it were the capture time
    else:
        dt = local_time(meta.get("captured_at"), meta.get("tz_offset_min"))
        l1 = dt.strftime("%d/%m/%Y | %H:%M:%S") + (f" {meta['tz']}" if meta.get("tz") and not gallery else "")
        if gallery:
            l1 += " (FROM FILE, NOT VERIFIED)"
    if gallery:
        l2 = "GPS: NOT AVAILABLE (GALLERY UPLOAD)"
    elif meta.get("lat") is not None and meta.get("lng") is not None:
        acc = f" (±{round(meta['accuracy_m'])}m)" if meta.get("accuracy_m") is not None else ""
        l2 = f"GPS: {meta['lat']:.4f}, {meta['lng']:.4f}{acc}"
        if not meta.get("location_verified"):
            l2 += " LOCATION NOT VERIFIED"
    else:
        l2 = "GPS: LOCATION NOT VERIFIED"
    l3 = f"Project: {meta['project_ref']} · {meta.get('postcode', '')} · {meta['nc_number']}"
    if meta.get("source") == "gallery":
        l3 += " · GALLERY UPLOAD"
    return [l1, l2, l3]


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def process_photo(data: bytes, meta: Optional[dict]) -> dict:
    """Returns jpeg bytes for stamped (full res), display (1280) and thumb (360). Original untouched."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    out = {}
    if meta:
        w, hgt = img.size
        fs = max(14, int(min(w, hgt) * 0.028))
        font = _font(fs)
        lines = stamp_lines(meta)
        pad = int(fs * 0.5)
        band = (fs + pad) * len(lines) + pad
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        d.rectangle([0, hgt - band, w, hgt], fill=(0, 0, 0, 150))
        for i, line in enumerate(lines):
            d.text((pad, hgt - band + pad + i * (fs + pad)), line, font=font, fill=(255, 255, 255, 255))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
        out["stamped"] = buf.getvalue()
    for name, size, q in (("display", 1280, 78), ("thumb", 360, 70)):
        c = img.copy()
        c.thumbnail((size, size))
        buf = io.BytesIO()
        c.save(buf, "JPEG", quality=q, optimize=True)
        out[name] = buf.getvalue()
    return out


def video_frames(data: bytes, duration: Optional[float], count: int = 4) -> List[bytes]:
    if not FFMPEG:
        return []
    frames = []
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "v.mp4")
        with open(src, "wb") as f:
            f.write(data)
        dur = duration or 8
        for i in range(count):
            t = dur * (i + 1) / (count + 1)
            dst = os.path.join(td, f"f{i}.jpg")
            subprocess.run([FFMPEG, "-y", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1", "-vf", "scale=960:-2", dst],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            if os.path.exists(dst):
                with open(dst, "rb") as f:
                    frames.append(f.read())
        if not frames:  # very short clip
            dst = os.path.join(td, "f.jpg")
            subprocess.run([FFMPEG, "-y", "-i", src, "-frames:v", "1", dst],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            if os.path.exists(dst):
                with open(dst, "rb") as f:
                    frames.append(f.read())
    return frames


def pdf_text_and_images(data: bytes, max_pages: int = 60, known_logos: Optional[list] = None) -> tuple:
    """Returns (text, {page_no: [jpeg bytes]}, stats) – embedded photos >= 200px; logos/decorations filtered out."""
    import pymupdf
    from report_import import classify
    doc = pymupdf.open(stream=data, filetype="pdf")
    text_parts, raw = [], {}
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        text_parts.append(f"--- page {i + 1} ---\n{page.get_text()}")
        seen = set()
        for info in page.get_images(full=True):
            xref = info[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                pix = pymupdf.Pixmap(doc, xref)
                if pix.width < 200 or pix.height < 200:
                    continue
                if pix.n - pix.alpha >= 4:  # CMYK -> RGB
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                img.thumbnail((2000, 2000))
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=90)
                raw.setdefault(i + 1, []).append(buf.getvalue())
            except Exception:
                continue
            if len(raw.get(i + 1, [])) >= 12:
                break
    flagged, learned = classify([(f"{p}-{k}", p, b) for p, imgs in raw.items() for k, b in enumerate(imgs)], known_logos)
    images = {}
    for p, imgs in raw.items():
        kept = [b for k, b in enumerate(imgs) if f"{p}-{k}" not in flagged][:6]
        if kept:
            images[p] = kept
    stats = {"found": sum(len(v) for v in raw.values()), "kept": sum(len(v) for v in images.values()),
             "filtered": len(flagged), "reasons": dict(__import__("collections").Counter(flagged.values())), "learned": learned}
    return "\n".join(text_parts), images, stats


def demo_trustmark_image(label: str, seed: int) -> bytes:
    """Generates a textured placeholder 'inspection photo' for seeded demo data."""
    import random
    r = random.Random(seed)
    img = Image.new("RGB", (900, 650), (150 + r.randint(0, 40), 140, 120))
    d = ImageDraw.Draw(img)
    for _ in range(140):  # brick-ish texture
        x, y = r.randint(0, 900), r.randint(0, 650)
        d.rectangle([x, y, x + r.randint(40, 90), y + r.randint(18, 30)], outline=(90, 60, 50),
                    fill=(170 + r.randint(-30, 30), 90 + r.randint(-20, 20), 70))
    d.rectangle([0, 560, 900, 650], fill=(0, 0, 0))
    d.text((20, 580), f"TRUSTMARK INSPECTION · {label}", font=_font(30), fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()
