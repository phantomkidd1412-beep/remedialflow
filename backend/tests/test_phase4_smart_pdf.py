"""Phase 4 - smart PDF wiring + rendering. Planner selection rules are covered in test_concise_pdf.py.
No AI calls, no Dropbox, no DB writes except the global flag (restored)."""
import io
import sys

import pytest

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402
from scope import merge, rule_extract  # noqa: E402
from smart_pdf import usable  # noqa: E402

W8 = "End caps missing to 8 no. external window sills (W1-W8)."


def sc(t):
    return merge(rule_extract(t), None, t, None)


def ph(i, loc=None, stage="after", grp=None, dup=None, comp="end cap", **kw):
    return {"_id": f"f{i}", "kind": "photo", "status": "submitted", "server_received_at": f"2026-01-01T00:{i:02d}:00", **kw,
            "classification": {"location_id": loc, "stage": stage, "location_group": grp, "dup_of": dup, "component": comp}}


def jpeg(c):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (80, 60), c).save(b, "JPEG")
    return b.getvalue()


def test_usable_requires_tags_on_every_photo():
    assert usable([ph(1)]) and not usable([ph(1), {"_id": "x"}]) and not usable([])


def test_pdf_renders_captions_and_ignores_internal_fields():
    import pymupdf
    from evidence_pack import nc_pdf
    pdf = nc_pdf({"address": "QA TEST 1 Example Road", "ref": "QA-P4"}, {"number": "NC-01", "original_text": W8, "export_note": "End caps fitted.",
                 "coverage_note": "Evidence coverage has not been confirmed."}, [], [("WINDOW 1 - COMPLETED WORK", [(jpeg((200, 0, 0)), "Window 1")])])
    txt = "".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf"))
    assert "Window 1" in txt and "WINDOW 1 - COMPLETED WORK" in txt and "not been confirmed" not in txt
    old = nc_pdf({"address": "A", "ref": "R"}, {"number": "NC-01", "original_text": W8}, [], [("COMPLETED WORK", [jpeg((0, 0, 200))])])
    assert "COMPLETED WORK" in "".join(p.get_text() for p in pymupdf.open(stream=old, filetype="pdf"))  # bytes-only input still works


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
    assert [h for h, _ in g] == ["WINDOW 1 - COMPLETED WORK", "WINDOW 2 - COMPLETED WORK"] and calls == []  # excluded + dup left out, no AI
    assert "coverage_note" not in n
    untagged = [dict(f, classification=None) for f in files[:3]]
    flag(False)
    g_off = LOOP.run_until_complete(server.pdf_photo_groups({"_id": "y", "original_text": W8}, untagged, get))
    flag(True)
    g_on = LOOP.run_until_complete(server.pdf_photo_groups({"_id": "y", "original_text": W8}, untagged, get))
    assert [h for h, _ in g_on] == [h for h, _ in g_off]  # untagged -> existing layout either way
