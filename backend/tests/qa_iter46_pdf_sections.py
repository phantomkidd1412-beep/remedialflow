# Iter46: PDF "REMEDIAL WORKS CARRIED OUT" omitted when the works description is empty; never "No details recorded.".
# In-process (no HTTP), AI stubbed, synthetic photo bytes, fake storage. Usage: python qa_iter46_pdf_sections.py [run|cleanup]
import asyncio
import io
import json
import sys
import zipfile

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
import fitz  # noqa: E402
from bson import ObjectId  # noqa: E402
from PIL import Image  # noqa: E402

from core import db, now_iso  # noqa: E402

REF = "QA-I46"
RES = []
BLOB = {}


def ok(name, cond, detail=""):
    RES.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f" :: {detail}" if detail and not cond else ""))


def mk(k):
    im = Image.new("RGB", (640, 480), ((k * 70) % 255, (k * 130) % 255, (k * 40) % 255))
    for x in range(0, 640, 40):
        for y in range(0, 480, 40):
            if (x // 40 + y // 40 + k) % 2:
                im.paste((255, 255, 255), (x, y, x + 20, y + 20))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    return b.getvalue()


async def seed():
    await cleanup(quiet=True)
    pid = str((await db.projects.insert_one({"ref": REF, "address": "QA TEST 46 Example Road", "postcode": "QA4 6AA", "installer_id": None,
                                             "status": "active", "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
    nids = {}
    for num, note in (("NC-01", "Replaced gas tee sleeve and refitted vent cover at 250mm clearance."), ("NC-02", "")):
        nids[num] = str((await db.ncs.insert_one({"project_id": pid, "number": num, "description": f"QA {num} works", "original_text": "Gas tee clearance 250mm",
                                                  "required_work": "", "status": "approved", "safety_critical": False, "updated_at": now_iso(),
                                                  "export_note": note, "export_note_source": "staff" if note else ""})).inserted_id)
        for k in range(3):
            _id = ObjectId()
            BLOB[f"qa46/{_id}.jpg"] = mk(k + (5 if num == "NC-02" else 0))
            await db.files.insert_one({"_id": _id, "project_id": pid, "nc_id": nids[num], "kind": "photo", "status": "submitted", "captured_at": f"2026-05-01 10:0{k}",
                                       "source": "in_app_camera", "sha256": f"qa46-{num}-{k}", "size": 1, "mime": "image/jpeg", "uploaded_by": None,
                                       "server_received_at": f"2026-05-01T10:0{k}:00", "paths": {"stamped": f"qa46/{_id}.jpg"}, "qa": "iter46"})
    return pid, nids


def stub_ai():
    import ai
    for name in dir(ai):
        fn = getattr(ai, name)
        if asyncio.iscoroutinefunction(fn):
            async def guard(*a, _n=name, **k):
                if _n == "arrange_photos":  # empty description from the 'AI' -> NC-02 stays without works text
                    photos = a[4] if len(a) > 4 else k.get("photos", [])
                    return {"pre": 0, "mid": None, "final": max(0, len(photos) - 1), "extra": [], "uncertain": [], "description": ""}
                return None
            setattr(ai, name, guard)


async def run():
    stub_ai()
    import server
    pid, nids = await seed()

    async def fake_aget(path):
        if path not in BLOB:
            raise RuntimeError(f"non-QA path requested: {path}")
        return BLOB[path], "image/jpeg"
    server.storage.aget = fake_aget
    p = await db.projects.find_one({"_id": ObjectId(pid)})
    data, n = await server.build_evidence_pack(pid, p)
    z = zipfile.ZipFile(io.BytesIO(data))
    texts = {}
    for name in z.namelist():
        if name.endswith(".pdf"):
            d = fitz.open(stream=z.read(name), filetype="pdf")
            texts[name.split("_")[0]] = "".join(pg.get_text() for pg in d)
    print(json.dumps({k: v[:600] for k, v in texts.items()}, indent=1))
    ok("two NC PDFs in pack", set(texts) == {"NC-01", "NC-02"}, str(list(texts)))
    t1, t2 = texts.get("NC-01", ""), texts.get("NC-02", "")
    ok("NC-01 has REMEDIAL WORKS heading", "REMEDIAL WORKS CARRIED OUT" in t1)
    ok("NC-01 shows its note", "Replaced gas tee sleeve" in t1)
    ok("NC-02 has NO REMEDIAL WORKS heading", "REMEDIAL WORKS CARRIED OUT" not in t2)
    ok("no 'No details recorded.' anywhere", all("No details recorded" not in t for t in texts.values()))
    ok("NC-02 still has NON-COMPLIANCE + photos section", "NON-COMPLIANCE" in t2 and ("REMEDIATION" in t2.upper() or "BEFORE" in t2.upper() or "AFTER" in t2.upper()))
    n2 = await db.ncs.find_one({"_id": ObjectId(nids["NC-02"])})
    ok("NC-02 export_note stays empty in DB", not (n2.get("export_note") or "").strip(), repr(n2.get("export_note")))
    # unit level: nc_pdf directly
    from evidence_pack import nc_pdf
    for note, expect in (("", False), ("   ", False), ("Fitted new sleeve.", True)):
        pdf = nc_pdf({"ref": "QA", "address": "QA", "postcode": "QA"}, {"number": "NC-09", "description": "x", "export_note": note}, [], [])
        t = "".join(pg.get_text() for pg in fitz.open(stream=pdf, filetype="pdf"))
        ok(f"nc_pdf note={note!r} heading={expect}", ("REMEDIAL WORKS CARRIED OUT" in t) == expect and "No details recorded" not in t)
    await cleanup()
    print(f"RESULT {sum(c for _, c in RES)}/{len(RES)} passed")


async def cleanup(quiet=False):
    projs = [str(p["_id"]) async for p in db.projects.find({"ref": REF})]
    ncs = [str(n["_id"]) async for n in db.ncs.find({"project_id": {"$in": projs}})]
    out = {"files": (await db.files.delete_many({"nc_id": {"$in": ncs}})).deleted_count,
           "ncs": (await db.ncs.delete_many({"project_id": {"$in": projs}})).deleted_count,
           "projects": (await db.projects.delete_many({"ref": REF})).deleted_count}
    if not quiet:
        print("cleanup", out)


cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
asyncio.get_event_loop().run_until_complete(run() if cmd == "run" else cleanup())
