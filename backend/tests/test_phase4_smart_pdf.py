"""Phase 4 - smart PDF photo plan. Pure planner + PDF render + pdf_photo_groups wiring with in-memory storage.
No AI calls, no Dropbox, no DB writes except the global flag (restored)."""
import io
import sys

import pytest

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402
from scope import merge, rule_extract  # noqa: E402
from smart_pdf import plan, usable  # noqa: E402

W8 = "End caps missing to 8 no. external window sills (W1-W8)."


def sc(t):
    return merge(rule_extract(t), None, t, None)


def ph(i, loc=None, stage="after", grp=None, dup=None, comp="end cap", **kw):
    return {"_id": f"f{i}", "kind": "photo", "status": "submitted", "server_received_at": f"2026-01-01T00:{i:02d}:00", **kw,
            "classification": {"location_id": loc, "stage": stage, "location_group": grp, "dup_of": dup, "component": comp}}


def ids(groups):
    return [(h, [f["_id"] for f, _ in items]) for h, items in groups]


def test_every_location_no_cap_and_stage_order():
    photos = [ph(i, f"loc-{i}") for i in range(1, 9)] + [ph(20, "loc-1", stage="before")]
    g, note = plan({"original_text": W8}, photos, sc(W8))
    heads = [h for h, _ in g]
    assert heads[0] == "WINDOW 1 - BEFORE REMEDIATION" and heads[1] == "WINDOW 1 - COMPLETED WORK" and heads[-1] == "WINDOW 8 - COMPLETED WORK"
    assert sum(len(x) for _, x in g) == 9  # all 9 photos, no artificial limit
    assert note == "Evidence shown for all 8 windows."
    assert g[1][1][0][1] == "Window 1 - End cap - completed"


def test_duplicates_only_when_confident_and_original_shown():
    photos = [ph(1, "loc-1"), ph(2, "loc-1", dup="f1"), ph(3, "loc-2"), ph(4, "loc-2", dup="f3", pdf_include=True)]
    g, _ = plan({}, photos, sc(W8))
    assert ids(g) == [("WINDOW 1 - COMPLETED WORK", ["f1"]), ("WINDOW 2 - COMPLETED WORK", ["f3", "f4"])]  # staff-kept dup stays
    g, _ = plan({}, [ph(2, "loc-1", dup="f1")], sc(W8))  # original not shown (e.g. staff excluded it) -> keep the duplicate
    assert ids(g)[0][1] == ["f2"]


def test_similar_photos_at_different_locations_kept():
    photos = [ph(i, f"loc-{i}") for i in (1, 2, 3)]  # look alike, different windows, no dup marks
    assert sum(len(x) for _, x in plan({}, photos, sc(W8))[0]) == 3


def test_uncertain_locations_retained_and_note():
    photos = [ph(1, "loc-1"), ph(2, None, grp="grp-1"), ph(3, None, grp="grp-1", stage="before"), ph(4, None, stage="uncertain")]
    g, note = plan({"original_text": W8}, photos, sc(W8))
    heads = [h for h, _ in g]
    assert heads == ["WINDOW 1 - COMPLETED WORK", "LOCATION NOT IDENTIFIED (1) - BEFORE REMEDIATION", "LOCATION NOT IDENTIFIED (1) - COMPLETED WORK",
                     "OTHER PHOTOGRAPHS - SUPPORTING PHOTOGRAPHS"]
    assert note == "Evidence of completed work shown for 3 of 8 windows."  # names withheld: untagged photos could be any window


def test_incomplete_note_names_missing():
    _, note = plan({"original_text": W8}, [ph(i, f"loc-{i}") for i in range(1, 7)], sc(W8))
    assert note.endswith("Outstanding: Window 7, Window 8.")


def test_verification_and_staff_stage_override():
    t = "Extractor fan in the bathroom has not been commissioned; airflow test results required."
    staff = {**ph(3, stage="before"), "stage": "final", "stage_source": "staff"}  # staff labelled it final-remediation
    photos = [ph(1, stage="during", comp="extractor fan"), ph(2, stage="verification", comp="airflow reading"), staff]
    g, note = plan({"original_text": t}, photos, sc(t))
    assert [h for h, _ in g] == ["DURING REMEDIATION", "COMPLETED WORK", "MEASUREMENT / TEST EVIDENCE"] and note == ""
    assert g[2][1][0][1] == "Airflow reading - measurement / test"  # caption only from stored labels


def test_no_scope_single_section_plain_headings():
    g, note = plan({}, [ph(1, None, stage="before"), ph(2, None)], None)
    assert [h for h, _ in g] == ["BEFORE REMEDIATION", "COMPLETED WORK"] and note == "Evidence coverage has not been assessed."


def test_usable_requires_tags_on_every_photo():
    assert usable([ph(1)]) and not usable([ph(1), {"_id": "x"}]) and not usable([])


def jpeg(c):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (80, 60), c).save(b, "JPEG")
    return b.getvalue()


def test_pdf_renders_captions_and_note():
    import pymupdf
    from evidence_pack import nc_pdf
    pdf = nc_pdf({"address": "QA TEST 1 Example Road", "ref": "QA-P4"}, {"number": "NC-01", "original_text": W8, "export_note": "End caps fitted.",
                 "coverage_note": "Evidence shown for all 8 windows."}, [], [("WINDOW 1 - COMPLETED WORK", [(jpeg((200, 0, 0)), "Window 1 - End cap - completed")])])
    txt = "".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf"))
    assert "Window 1 - End cap - completed" in txt and "Evidence shown for all 8 windows." in txt and "WINDOW 1 - COMPLETED WORK" in txt
    old = nc_pdf({"address": "A", "ref": "R"}, {"number": "NC-01", "original_text": W8}, [], [("COMPLETED WORK", [jpeg((0, 0, 200))])])
    assert "COMPLETED WORK" in "".join(p.get_text() for p in pymupdf.open(stream=old, filetype="pdf"))  # legacy bytes-only input still works


@pytest.fixture
def flag():
    from core import db
    old = LOOP.run_until_complete(db.settings.find_one({"_id": "global"})) or {}
    yield lambda on: LOOP.run_until_complete(db.settings.update_one({"_id": "global"}, {"$set": {"smart_pdf_enabled": on}}, upsert=True))
    LOOP.run_until_complete(db.settings.update_one({"_id": "global"}, {"$set": {"smart_pdf_enabled": old.get("smart_pdf_enabled", False)}}))


def test_pdf_photo_groups_wiring(flag, monkeypatch):
    import ai
    import server
    imgs = {f"f{i}": jpeg((i * 30, 0, 0)) for i in range(1, 5)}

    async def get(f, *v):
        return imgs.get(f["_id"])
    calls = []

    async def no_ai(*a, **k):
        calls.append(1)
        return None
    monkeypatch.setattr(ai, "arrange_photos", no_ai)
    n = {"_id": "x", "original_text": W8, "scope": sc(W8)}
    files = [ph(1, "loc-1"), ph(2, "loc-1", dup="f1"), ph(3, "loc-2"), ph(4, "loc-3", pdf_include=False)]
    flag(True)
    g = LOOP.run_until_complete(server.pdf_photo_groups(n, files, get))
    assert [h for h, _ in g] == ["WINDOW 1 - COMPLETED WORK", "WINDOW 2 - COMPLETED WORK"] and calls == []  # staff-excluded + dup left out, no AI
    assert n["coverage_note"].startswith("Evidence of completed work shown for 2 of 8")
    untagged = [dict(f, classification=None) for f in files[:3]]
    flag(False)
    g_off = LOOP.run_until_complete(server.pdf_photo_groups({"_id": "y", "original_text": W8}, untagged, get))
    flag(True)
    n2 = {"_id": "y", "original_text": W8}
    g_on = LOOP.run_until_complete(server.pdf_photo_groups(n2, untagged, get))
    assert [h for h, _ in g_on] == [h for h, _ in g_off] and n2["coverage_note"] == "Evidence coverage has not been assessed."  # existing layout + safeguard line
