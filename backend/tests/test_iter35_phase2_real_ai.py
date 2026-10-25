"""Iteration 35 - Phase 2 photo classification with REAL Gemini (6 calls, run concurrently) on synthetic in-memory PIL images.
Nothing is stored (no DB, no storage, no Dropbox): photo_class.ai_classify -> photo_class.build only.
Run: pytest tests/test_iter35_phase2_real_ai.py -n 0 -s"""
import asyncio
import io
import sys

import pytest
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402
from photo_class import ai_classify, build, dhash  # noqa: E402

FONT = ImageFont.load_default(size=30)


def _wall(d, tint, w=640, h=480):
    d.rectangle([0, 0, w, h], fill=(150 + tint, 70 + tint // 2, 50))
    for y in range(0, h, 24):  # brick courses
        d.line([0, y, w, y], fill=(210, 200, 190), width=2)
        off = 0 if (y // 24) % 2 else 30
        for x in range(off, w, 60):
            d.line([x, y, x, y + 24], fill=(210, 200, 190), width=2)


def _label(d, text):
    if text:
        d.rectangle([10, 420, 630, 472], fill=(255, 255, 255))
        d.text((20, 428), text, fill=(0, 0, 0), font=FONT)


def _jpeg(im):
    b = io.BytesIO()
    im.save(b, "JPEG", quality=88)
    return b.getvalue()


def endcap(tint=0, dx=0, cap=True, text="", detail="endcap"):
    """Close-up of a uPVC window sill corner; cap=False = missing end cap (defect)."""
    im = Image.new("RGB", (640, 480))
    d = ImageDraw.Draw(im)
    _wall(d, tint)
    d.rectangle([120 + dx, 40, 520 + dx, 250], fill=(245, 245, 245), outline=(120, 120, 120), width=6)  # frame
    d.rectangle([140 + dx, 60, 500 + dx, 230], fill=(150, 190, 220))  # glass
    d.rectangle([90 + dx, 250, 560 + dx, 290], fill=(235, 235, 235), outline=(100, 100, 100), width=3)  # sill
    if detail == "sealant":
        d.line([120 + dx, 250, 520 + dx, 250], fill=(30, 30, 30), width=8)  # sealant bead close-up framing
        d.rectangle([60 + dx, 300, 600 + dx, 400], fill=(90, 90, 90))
    elif cap:
        d.rectangle([548 + dx, 244, 580 + dx, 296], fill=(250, 250, 250), outline=(60, 60, 60), width=3)  # end cap fitted
    else:
        d.polygon([(560 + dx, 250), (575 + dx, 260), (560 + dx, 290)], fill=(40, 40, 40))  # open sill end
    _label(d, text)
    return _jpeg(im)


def vent_room(text):
    im = Image.new("RGB", (640, 480), (230, 225, 210))
    d = ImageDraw.Draw(im)
    d.rectangle([140, 80, 500, 380], fill=(250, 250, 250), outline=(140, 140, 140), width=8)
    d.rectangle([165, 140, 475, 360], fill=(170, 200, 225))
    d.rectangle([200, 96, 440, 124], fill=(235, 235, 235), outline=(80, 80, 80), width=2)  # trickle vent
    for x in range(210, 430, 12):
        d.line([x, 104, x, 116], fill=(60, 60, 60), width=3)
    _label(d, text)
    return _jpeg(im)


def fan(text, bath=True):
    im = Image.new("RGB", (640, 480), (240, 240, 240) if bath else (220, 210, 190))
    d = ImageDraw.Draw(im)
    if bath:
        for x in range(0, 640, 40):
            for y in range(0, 400, 40):
                d.rectangle([x, y, x + 38, y + 38], outline=(200, 200, 200))
    else:
        d.rectangle([0, 300, 640, 400], fill=(120, 90, 60))  # worktop / units
    d.ellipse([240, 80, 400, 240], fill=(255, 255, 255), outline=(90, 90, 90), width=5)
    for k in range(4):
        d.line([320, 160, 320 + [60, -60, 0, 0][k], 160 + [0, 0, 60, -60][k]], fill=(120, 120, 120), width=6)
    _label(d, text)
    return _jpeg(im)


def ewi(text, rear=False):
    im = Image.new("RGB", (640, 480), (170, 200, 230))
    d = ImageDraw.Draw(im)
    d.rectangle([80, 120, 560, 410], fill=(235, 225, 200))
    d.polygon([(60, 120), (320, 30), (580, 120)], fill=(120, 60, 50))
    for x in range(80, 560, 80):
        d.line([x, 120, x, 410], fill=(200, 190, 170), width=2)  # EWI board joints
    if rear:
        d.rectangle([260, 260, 380, 410], fill=(110, 110, 110))  # patio door
    else:
        d.rectangle([290, 300, 350, 410], fill=(60, 40, 120))  # front door
    d.rectangle([120, 160, 220, 240], fill=(160, 190, 215))
    d.rectangle([420, 160, 520, 240], fill=(160, 190, 215))
    _label(d, text)
    return _jpeg(im)


def ambiguous():
    im = Image.new("RGB", (640, 480), (128, 120, 110))
    d = ImageDraw.Draw(im)
    for k in range(40):
        d.ellipse([(k * 97) % 600, (k * 61) % 440, (k * 97) % 600 + 40, (k * 61) % 440 + 40], fill=(100 + k % 40, 100, 95))
    return _jpeg(im)


W8_TEXT = "End caps missing to 8 no. external window sills (W1-W8)."
W8 = [{"id": f"loc-{i}", "label": f"Window {i}"} for i in range(1, 9)]
ROOMS = [{"id": f"loc-{i + 1}", "label": l} for i, l in enumerate(["Living Room", "Bedroom 1", "Bedroom 2", "Bedroom 3"])]
WET = [{"id": "loc-1", "label": "Bathroom"}, {"id": "loc-2", "label": "Kitchen"}]
ELEV = [{"id": "loc-1", "label": "Front elevation"}, {"id": "loc-2", "label": "Rear elevation"}]

SCEN = {
    "eight_unlabelled": (W8_TEXT, W8, [endcap(tint=t, dx=dx) for t, dx in [(0, 0), (6, 3), (12, -3), (3, 5), (9, -5), (15, 2), (18, -2), (21, 4)]]),
    "dup_and_before_after": (W8_TEXT, W8, [endcap(text="WINDOW 3 - END CAP FITTED"), endcap(text="WINDOW 3 - END CAP FITTED", dx=2),
                                          endcap(tint=10, cap=False, text="WINDOW 1 - BEFORE (CAP MISSING)"), endcap(tint=10, text="WINDOW 1 - AFTER (CAP FITTED)")]),
    "angles_and_ambiguous": (W8_TEXT, W8, [endcap(tint=20, text="WINDOW 2 - END CAP FITTED"), endcap(tint=20, detail="sealant", text="WINDOW 2 - SEALANT JOINT"), ambiguous()]),
    "four_rooms_vents": ("Background ventilation (trickle vents) not provided in habitable rooms: Living Room, Bedroom 1, Bedroom 2, Bedroom 3.", ROOMS,
                         [vent_room(t) for t in ["LIVING ROOM - TRICKLE VENT", "BEDROOM 1 - TRICKLE VENT", "BEDROOM 2 - TRICKLE VENT", "BEDROOM 3 - TRICKLE VENT"]]),
    "wet_rooms_fans": ("Extract ventilation not commissioned in wet rooms (Bathroom, Kitchen).", WET, [fan("BATHROOM - EXTRACTOR FAN"), fan("KITCHEN - EXTRACTOR FAN", bath=False)]),
    "ewi_elevations": ("EWI render boards not mechanically fixed at front and rear elevations.", ELEV, [ewi("FRONT ELEVATION - EWI"), ewi("REAR ELEVATION - EWI", rear=True)]),
}


@pytest.fixture(scope="module")
def results():
    async def go():
        keys = list(SCEN)
        outs = await asyncio.gather(*[ai_classify(SCEN[k][0], "", [l["label"] for l in SCEN[k][1]], SCEN[k][2]) for k in keys])
        res = {}
        for k, items in zip(keys, outs):
            _, locs, ims = SCEN[k]
            n = len(ims)
            built = build(items or [], [f"f{i}" for i in range(n)], [dhash(b) for b in ims], locs, {}) if items is not None else None
            res[k] = (items, built)
            print(f"\n=== {k}\nRAW: {[(o.get('i'), o.get('location_label'), o.get('location_confidence'), o.get('same_location_as'), o.get('component'), o.get('stage')) for o in (items or [])]}")
            if built:
                print("BUILT:", {f: (c['location_id'], c['location_group'], c['location_uncertain'], c['component'], c['stage'], c['dup_of']) for f, c in built.items()})
        return res
    return LOOP.run_until_complete(go())


def _ok(results, k):
    items, built = results[k]
    assert items is not None and built is not None, f"{k}: AI call failed"
    assert len(items) == len(SCEN[k][2]), f"{k}: expected {len(SCEN[k][2])} items, got {len(items)}"
    return built


def test_eight_unlabelled_not_merged_not_duplicates(results):
    b = _ok(results, "eight_unlabelled")
    assert all(c["dup_of"] is None for c in b.values()), "similar installations wrongly marked duplicates"
    assert all(c["location_uncertain"] and c["location_id"] is None for c in b.values()), "location invented without label"
    groups = [c["location_group"] for c in b.values() if c["location_group"]]
    assert not groups or max(groups.count(g) for g in set(groups)) < 8, "all 8 merged into one location"


def test_near_identical_same_window_duplicate_kept(results):
    b = _ok(results, "dup_and_before_after")
    assert b["f0"]["location_id"] == "loc-3" and b["f1"]["location_id"] == "loc-3"
    assert b["f1"]["dup_of"] == "f0", "near-identical photo of the same labelled window not flagged duplicate"
    assert len(b) == 4  # both kept


def test_before_after_same_component_not_duplicates(results):
    b = _ok(results, "dup_and_before_after")
    assert b["f2"]["location_id"] == "loc-1" and b["f3"]["location_id"] == "loc-1"
    assert b["f2"]["stage"] == "before" and b["f3"]["stage"] in ("after", "verification")
    assert b["f2"]["dup_of"] is None and b["f3"]["dup_of"] is None


def test_two_angles_different_details_not_duplicates_and_ambiguous_uncertain(results):
    b = _ok(results, "angles_and_ambiguous")
    assert b["f0"]["dup_of"] is None and b["f1"]["dup_of"] is None
    assert b["f2"]["location_uncertain"] is True and b["f2"]["location_id"] is None


def test_four_rooms_distinct(results):
    b = _ok(results, "four_rooms_vents")
    assert {c["location_id"] for c in b.values()} == {"loc-1", "loc-2", "loc-3", "loc-4"}
    assert all(c["dup_of"] is None for c in b.values())


def test_wet_rooms_distinct(results):
    b = _ok(results, "wet_rooms_fans")
    assert b["f0"]["location_id"] == "loc-1" and b["f1"]["location_id"] == "loc-2" and b["f1"]["dup_of"] is None


def test_ewi_front_rear_distinct(results):
    b = _ok(results, "ewi_elevations")
    assert b["f0"]["location_id"] == "loc-1" and b["f1"]["location_id"] == "loc-2"
    assert b["f0"]["dup_of"] is None and b["f1"]["dup_of"] is None
