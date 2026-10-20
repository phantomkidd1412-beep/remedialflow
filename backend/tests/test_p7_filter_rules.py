"""Unit tests for the conservative report-image filter: only page decorations are hidden.
Technical diagrams, annotated defect photos and measurement photos must be kept."""
import io
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test")

from PIL import Image, ImageDraw  # noqa: E402
from report_import import classify  # noqa: E402


def jpeg(img):
    b = io.BytesIO()
    img.save(b, "JPEG", quality=90)
    return b.getvalue()


def logo():  # wide flat-colour banner, like the TrustMark header
    img = Image.new("RGB", (2000, 551), "white")
    d = ImageDraw.Draw(img)
    d.polygon([(80, 100), (300, 100), (200, 450), (40, 450)], fill=(30, 140, 150))
    d.rectangle([400, 200, 1800, 330], fill=(40, 50, 90))
    return jpeg(img)


def diagram():  # white background, thin lines + text (technical detail drawing)
    img = Image.new("RGB", (500, 353), "white")
    d = ImageDraw.Draw(img)
    for y in range(40, 330, 25):
        d.line([(20, y), (480, y - 10)], fill=(200, 0, 120), width=1)
    d.rectangle([300, 250, 490, 345], outline="black")
    d.text((310, 260), "DETAIL 8b  250mm MIN", fill="black")
    return jpeg(img)


def photo(seed, annotate=False, tape=False):
    r = random.Random(seed)
    img = Image.new("RGB", (500, 375))
    px = img.load()
    for x in range(500):
        for y in range(375):
            px[x, y] = (120 + r.randint(-40, 40), 110 + r.randint(-40, 40), 100 + r.randint(-40, 40))
    d = ImageDraw.Draw(img)
    if annotate:
        d.ellipse([150, 100, 300, 250], outline=(255, 0, 0), width=6)
        d.line([(320, 60), (260, 120)], fill=(255, 0, 0), width=6)
    if tape:
        d.rectangle([230, 0, 260, 375], fill=(240, 200, 30))
        for y in range(0, 375, 15):
            d.line([(230, y), (245, y)], fill="black", width=2)
    return jpeg(img)


def test_running_logo_hidden_and_learned():
    L = logo()
    items = [(f"logo{p}", p, L) for p in range(1, 7)] + [(f"ph{p}", p, photo(p)) for p in range(1, 7)]
    out, learned = classify(items)
    assert all(out.get(f"logo{p}") == "repeated" for p in range(1, 7))
    assert not any(k.startswith("ph") for k in out)
    assert len(learned) == 1


def test_known_logo_hidden_even_once():
    L = logo()
    _, learned = classify([(f"l{p}", p, L) for p in range(1, 5)])
    out, _ = classify([("only", 1, L), ("ph", 2, photo(9))], learned)
    assert out == {"only": "known_logo"}


def test_single_diagram_annotated_and_measurement_kept():
    items = [("diagram", 3, diagram()), ("annotated", 4, photo(1, annotate=True)), ("tape", 5, photo(2, tape=True))]
    out, _ = classify(items)
    assert out == {}


def test_diagram_kept_even_with_known_logo_fingerprint():
    L = logo()
    _, learned = classify([(f"l{p}", p, L) for p in range(1, 5)])
    out, _ = classify([("diagram", 1, diagram())], learned)
    assert out == {}


def test_photo_reused_on_many_pages_is_kept():
    P = photo(5, annotate=True)
    out, _ = classify([(f"p{p}", p, P) for p in range(1, 7)])
    assert out == {}
