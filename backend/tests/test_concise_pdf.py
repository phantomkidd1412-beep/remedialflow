"""Concise evidence PDFs: one best photo per evidence step, no internal wording, both layouts.
Synthetic images only (in memory), AI arrangement mocked, no Dropbox, no DB writes except the smart-PDF flag (restored).
Writes two sample PDFs to /app/test_reports/samples/ for review."""
import io
import os
import random
import sys

import pymupdf
import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402
from scope import merge, rule_extract  # noqa: E402
from smart_pdf import plan  # noqa: E402

OUT = "/app/test_reports/samples"
GAS = ("There are non-compliances regarding a minimum of 250mm clearance above the gas pipe entry tee. "
       "This is located towards the front of the side elevation.")
W8 = "End caps missing to 8 no. external window sills (W1-W8)."
INTERNAL = ("not been confirmed", "not been assessed", "Evidence shown for all", "Outstanding", "Location not identified",
            "confidence", "uncertain", "review", "Evidence coverage")


def sc(t):
    return merge(rule_extract(t), None, t, None)


def scene(seed, shift=0, stamp="", sharp=True):
    """Distinct seed = different view; same seed + small shift = substantially the same view."""
    r = random.Random(seed)
    im = Image.new("RGB", (640, 480), (r.randint(40, 200), r.randint(40, 200), r.randint(40, 200)))
    d = ImageDraw.Draw(im)
    for _ in range(14):
        x, y = r.randint(0, 560), r.randint(0, 400)
        d.rectangle([x + shift, y, x + shift + r.randint(30, 160), y + r.randint(30, 120)],
                    fill=(r.randint(0, 255), r.randint(0, 255), r.randint(0, 255)) if sharp else (120, 120, 120))
    d.text((10, 10), f"scene {seed}", fill="white")
    if stamp:
        d.rectangle([0, 450, 640, 480], fill=(0, 0, 0))
        d.text((10, 458), stamp, fill="white")  # the burned-in capture timestamp of the stamped copy
    b = io.BytesIO()
    im.save(b, "JPEG", quality=85)
    return b.getvalue()


def photo(i, seed, stage, loc=None, shift=0, sharp=True, comp="", grp=None, dup=None, **kw):
    ts = f"2026-05-14 09:{i:02d}:00"
    return {"_id": f"f{i}", "kind": "photo", "status": "submitted", "server_received_at": f"2026-05-14T09:{i:02d}:00", "captured_at": ts,
            "_img": {"stamped": scene(seed, shift, f"Captured {ts}  51.5072N 0.1276W", sharp), "display": scene(seed, shift, "", sharp)},
            "classification": {"location_id": loc, "stage": stage, "component": comp, "location_group": grp, "dup_of": dup}, **kw}


async def get(f, *variants):
    return next((f["_img"][v] for v in variants if v in f["_img"]), None)


def gas_photos():
    return [photo(1, 11, "before"), photo(2, 11, "before", shift=2), photo(3, 12, "during"), photo(4, 12, "during", shift=3),
            photo(5, 12, "during", shift=1, sharp=False), photo(6, 13, "after", comp="vent cover"), photo(7, 13, "after", shift=2),
            photo(8, 13, "after", shift=4), photo(9, 14, "verification", comp="clearance measurement"), photo(10, 14, "verification", shift=2)]


def window_photos():
    ps = []
    for w in range(1, 7):  # 6 of 8 windows evidenced, two near-identical completed shots each
        ps += [photo(w * 2, 100 + w, "after", loc=f"loc-{w}"), photo(w * 2 + 1, 100 + w, "after", loc=f"loc-{w}", shift=2)]
    ps.append(photo(20, 101, "before", loc="loc-1"))
    return ps


@pytest.fixture
def smart_flag():
    from core import db
    old = LOOP.run_until_complete(db.settings.find_one({"_id": "global"})) or {}

    def set_(on):
        LOOP.run_until_complete(db.settings.update_one({"_id": "global"}, {"$set": {"smart_pdf_enabled": on}}, upsert=True))
    yield set_
    set_(old.get("smart_pdf_enabled", False))


def groups(n, files):
    import server
    return LOOP.run_until_complete(server.pdf_photo_groups(n, files, get))


def pdf_text(pdf):
    return " ".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf")).replace("\n", " ")


def render(n, g, name):
    from evidence_pack import nc_pdf
    pdf = nc_pdf({"address": "QA TEST 1 Example Road", "postcode": "QA1 1AA", "ref": "QA-SAMPLE"},
                 {**n, "export_note": n.get("export_note", "")}, [scene(900)], g)
    os.makedirs(OUT, exist_ok=True)
    open(f"{OUT}/{name}.pdf", "wb").write(pdf)
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    for k, p in enumerate(doc):
        p.get_pixmap(dpi=55).save(f"{OUT}/{name}_p{k + 1}.png")
    return pdf, len(doc)


# ---------------- planner ----------------
def test_plan_one_slot_per_step_single_location():
    g = plan({}, gas_photos(), sc(GAS))
    assert [h for h, _ in g] == ["BEFORE REMEDIATION", "DURING REMEDIATION", "COMPLETED WORK", "MEASUREMENT / TEST EVIDENCE"]
    assert all(len(slots) == 1 for _, slots in g)  # one photo printed per step
    assert g[3][1][0][1] == "Clearance measurement" and g[2][1][0][1] == ""  # no repeated generic captions


def test_plan_one_slot_per_location_and_confirmed_dups_never_candidates():
    ps = window_photos() + [photo(30, 150, "after", loc="loc-2", dup="f4")]
    g = plan({}, ps, sc(W8))
    assert [h for h, _ in g][:2] == ["WINDOW 1 - BEFORE REMEDIATION", "WINDOW 1 - COMPLETED WORK"]
    assert len(g) == 7 and all(len(s) == 1 for _, s in g)
    assert "f30" not in [f["_id"] for _, s in g for fs, _ in s for f in fs]


def test_plan_unidentified_groups_kept_without_internal_wording():
    ps = [photo(1, 1, "after", grp="grp-1"), photo(2, 1, "after", grp="grp-1", shift=2), photo(3, 2, "after", grp="grp-2"), photo(4, 3, "after")]
    g = plan({}, ps, sc(W8))
    assert g == [("COMPLETED WORK", g[0][1])] and len(g[0][1]) == 3  # one per distinct group (+ ungrouped), heading is plain
    assert all(cap == "" for _, cap in g[0][1])


def test_staff_stage_correction_wins():
    p = {**photo(1, 1, "before"), "classification": {"stage": "before", "staff": {"stage": "verification"}}}
    assert plan({}, [p], None)[0][0] == "MEASUREMENT / TEST EVIDENCE"


# ---------------- smart layout end to end (sample PDFs) ----------------
def test_sample_gas_tee_pdf(smart_flag):
    smart_flag(True)
    files = gas_photos()
    n = {"_id": "x", "number": "NC-01", "original_text": GAS, "export_note": "Clearance above the gas entry tee was restored and a vent cover fitted."}
    g = groups(n, files)
    assert [h for h, _ in g] == ["BEFORE REMEDIATION", "DURING REMEDIATION", "COMPLETED WORK", "MEASUREMENT / TEST EVIDENCE"]
    assert all(len(x) == 1 for _, x in g)  # 10 photos -> 4, one per step
    stamped = {f["_img"]["stamped"] for f in files}
    assert all(b in stamped for _, x in g for b, _ in x)  # the timestamped copy is used
    assert g[1][1][0][0] != files[4]["_img"]["stamped"]  # the blurred 'during' shot is not chosen
    pdf, pages = render(n, g, "NC-01_gas_entry_tee")
    txt = pdf_text(pdf)
    assert pages <= 2 and not any(w.lower() in txt.lower() for w in INTERNAL)
    assert all(h in txt for h in ("BEFORE REMEDIATION", "DURING REMEDIATION", "COMPLETED WORK", "MEASUREMENT / TEST EVIDENCE"))


def test_sample_multi_location_pdf(smart_flag):
    smart_flag(True)
    n = {"_id": "y", "number": "NC-02", "original_text": W8, "export_note": "End caps fitted to the external window sills."}
    g = groups(n, window_photos())
    heads = [h for h, _ in g]
    assert heads == ["WINDOW 1 - BEFORE REMEDIATION"] + [f"WINDOW {w} - COMPLETED WORK" for w in range(1, 7)]
    assert all(len(x) == 1 for _, x in g)  # 13 photos -> 7 (duplicates per window removed)
    pdf, pages = render(n, g, "NC-02_multi_location_windows")
    txt = pdf_text(pdf)
    assert pages <= 2 and all(h in txt for h in heads)
    assert not any(w.lower() in txt.lower() for w in INTERNAL)
    assert "WINDOW 7" not in txt and "WINDOW 8" not in txt and "all 8" not in txt  # incomplete never shown as complete


# ---------------- existing layout ----------------
def test_existing_layout_one_per_stage_and_extras_deduped(smart_flag, monkeypatch):
    import ai
    smart_flag(False)
    files = [dict(f, classification=None) for f in gas_photos()]
    for f in files[5:8]:  # staff labelled all three cover shots "final"
        f.update(stage="final", stage_source="staff")

    async def arr(*a, **k):  # mocked AI arrangement: picks pre/mid + an 'extra' that repeats the pre photo
        return {"pre": 0, "mid": 2, "final": None, "extra": [1, 8], "uncertain": True, "description": "x"}
    monkeypatch.setattr(ai, "arrange_photos", arr)
    g = groups({"_id": "z", "original_text": GAS}, files)
    assert [(h, len(x)) for h, x in g] == [("PRE-REMEDIATION PHOTOGRAPH", 1), ("MID-REMEDIATION PHOTOGRAPH", 1),
                                           ("FINAL-REMEDIATION PHOTOGRAPH", 1), ("ADDITIONAL SUPPORTING EVIDENCE", 1)]
    assert g[3][1][0] == files[8]["_img"]["stamped"]  # the measurement extra stays, the repeat of 'pre' is dropped


def test_existing_layout_staff_picks_respected(smart_flag):
    smart_flag(True)
    files = gas_photos()
    for f in files[:3]:
        f["pdf_include"] = True
    files[3]["pdf_include"] = False
    g = groups({"_id": "w", "original_text": GAS}, files)
    assert g == [("REMEDIATION PHOTOGRAPHS", g[0][1])] and len(g[0][1]) == 3  # exactly the staff selection


def test_trustmark_near_identical_copies_once():
    from evidence_pack import distinct
    t = [scene(500), scene(500, 1), scene(501)]
    assert len(distinct(t, [], 4)) == 2
