"""PDF coverage safeguard: with Location-Aware Evidence ON every PDF path prints one coverage line; OFF = unchanged.
Four states x smart + existing layout. In-memory images, AI arrangement mocked, no Dropbox, no DB writes except the flag (restored)."""
import io
import sys

import pytest

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402

W8 = "End caps missing to 8 no. external window sills (W1-W8)."
ALL8 = "Evidence shown for all 8 windows."
SHORT = "Evidence of completed work shown for 6 of 8 windows. Outstanding: Window 7, Window 8."
UNCONF = "Evidence coverage has not been confirmed."
UNASSESSED = "Evidence coverage has not been assessed."


def jpeg(c):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (80, 60), (c * 20 % 255, 40, 90)).save(b, "JPEG")
    return b.getvalue()


def ph(i, loc, stage="after", tagged=True, **kw):
    f = {"_id": f"f{i}", "kind": "photo", "status": "submitted", "server_received_at": f"2026-01-01T00:{i:02d}:00", "paths": {"display": f"qa-mem:{i}"}, **kw}
    if tagged:
        f["classification"] = {"location_id": loc, "stage": stage, "location_group": None, "dup_of": None, "component": "end cap"}
    return f


STATES = {
    "complete": ([ph(i, f"loc-{i}") for i in range(1, 9)], ALL8),
    "incomplete": ([ph(i, f"loc-{i}") for i in range(1, 7)], SHORT),
    "unverified": ([ph(i, f"loc-{i}") for i in range(1, 7)] + [ph(7, None), ph(8, None)], UNCONF),
    "not_assessed": ([ph(i, None, tagged=False) for i in range(1, 9)], UNASSESSED),
}


@pytest.fixture
def env(monkeypatch):
    import ai
    import server
    from core import db
    old = LOOP.run_until_complete(db.settings.find_one({"_id": "global"})) or {}
    calls = []

    async def no_ai(*a, **k):  # existing layout's AI arrangement: mocked out
        calls.append(1)
        return None
    monkeypatch.setattr(ai, "arrange_photos", no_ai)

    def flag(on):
        LOOP.run_until_complete(db.settings.update_one({"_id": "global"}, {"$set": {"smart_pdf_enabled": on}}, upsert=True))

    async def get(f, *v):
        return jpeg(int(f["_id"][1:]))

    def groups(files, picked=False):
        fs = [dict(f, pdf_include=True) if picked and k == 0 else dict(f) for k, f in enumerate(files)]
        n = {"_id": "x", "original_text": W8}
        return LOOP.run_until_complete(server.pdf_photo_groups(n, fs, get)), n
    yield flag, groups, calls
    flag(old.get("smart_pdf_enabled", False))


@pytest.mark.parametrize("state", list(STATES))
def test_smart_path_four_states(env, state):
    flag, groups, _ = env
    flag(True)
    files, note = STATES[state]
    g, n = groups(files)
    assert n["coverage_note"] == note
    if state != "not_assessed":
        assert g[0][0].startswith("WINDOW 1")  # smart layout used
    else:
        assert not g[0][0].startswith("WINDOW")  # untagged -> existing layout


@pytest.mark.parametrize("state", list(STATES))
def test_existing_layout_four_states(env, state):
    """Staff hand-picked a photo -> existing layout keeps exactly the staff selection, and still gets the same line."""
    flag, groups, _ = env
    flag(True)
    files, note = STATES[state]
    g, n = groups(files, picked=True)
    assert n["coverage_note"] == note
    assert g == [("REMEDIATION PHOTOGRAPHS", g[0][1])] and len(g[0][1]) == 1  # staff selection preserved


def test_switch_off_unchanged(env):
    flag, groups, _ = env
    flag(False)
    for files, _ in STATES.values():
        for picked in (False, True):
            g, n = groups(files, picked)
            assert "coverage_note" not in n and not any(h.startswith("WINDOW") for h, _ in g)


def test_single_location_complete_no_line_and_no_invented_shortfall(env):
    flag, groups, _ = env
    flag(True)
    import server
    t = "Insulation continuity not maintained around the external wall bracket."

    async def get(f, *v):
        return jpeg(1)
    n = {"_id": "y", "original_text": t}
    LOOP.run_until_complete(server.pdf_photo_groups(n, [ph(1, None, stage="after")], get))
    assert n["coverage_note"] == ""  # complete: no warning
    n = {"_id": "y", "original_text": t}
    LOOP.run_until_complete(server.pdf_photo_groups(n, [ph(1, None, tagged=False)], get))
    assert n["coverage_note"] == UNASSESSED  # untagged is never reported as missing work


def test_note_printed_once_in_pdf(env):
    import pymupdf
    from evidence_pack import nc_pdf
    flag, groups, _ = env
    flag(True)
    g, n = groups(STATES["incomplete"][0])
    pdf = nc_pdf({"address": "QA TEST 1 Example Road", "ref": "QA"}, {**n, "number": "NC-01", "export_note": "End caps fitted."}, [], g)
    txt = "".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf")).replace("\n", " ")
    assert txt.count("Outstanding: Window 7") == 1
