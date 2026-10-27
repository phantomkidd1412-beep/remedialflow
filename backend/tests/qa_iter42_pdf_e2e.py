# Iter42: end-to-end PDF/selection/auto-select/compat verification on synthetic QA project QA-KBUI-42.
# HTTP (public URL) for selection save/compat endpoints; in-process for PDF build (local synthetic image bytes, NO storage/Dropbox
# access) and Auto Select (AI functions stubbed, any real AI call is recorded as a failure). Usage: python qa_iter42_pdf_e2e.py [run|cleanup]
import asyncio
import io
import json
import os
import re
import subprocess
import sys
import zipfile

import fitz
import requests
from bson import ObjectId
from PIL import Image, ImageDraw

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/frontend/.env")
BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
from core import db, now_iso  # noqa: E402

PW = "QaCheck-7742x"
EMAILS = {"admin": "qa.i42.admin@example.com", "installer": "qa.i42.installer@example.com"}
REF = "QA-KBUI-42"
FLAGS = ("location_scope_enabled", "photo_classification_enabled", "coverage_check_enabled", "smart_pdf_enabled", "staff_review_enabled")
PAL = [(r, g, b) for r in (30, 130, 230) for g in (30, 130, 230) for b in (30, 130, 230)]
BLOB, AI_CALLS, RES = {}, [], []


def ok(name, cond, detail=""):
    RES.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f" :: {detail}" if detail else ""))


def mk(k):
    im = Image.new("RGB", (640, 480), PAL[k])
    d = ImageDraw.Draw(im)
    for x in range(0, 640, 40):
        d.line([(x, 0), (x, 480)], fill=(128, 128, 128), width=1)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=92)
    return b.getvalue()


def ident(png_bytes):
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB").resize((32, 24))
    px = list(im.getdata())
    m = tuple(sum(p[i] for p in px) / len(px) for i in range(3))
    k = min(range(len(PAL)), key=lambda j: sum((m[i] - PAL[j][i]) ** 2 for i in range(3)))
    return k if sum((m[i] - PAL[k][i]) ** 2 for i in range(3)) < 40 ** 2 else None


# letter -> (nc number, stage, palette idx)
PHOTOS = {"A": ("NC-01", "before", 0), "B": ("NC-01", "before", 1), "C": ("NC-01", "verification", 2), "D": ("NC-01", "after", 3), "E": ("NC-01", "after", 4),
          "F": ("NC-02", "before", 5), "G": ("NC-02", "after", 6), "H": ("NC-02", "after", 7),
          "I": ("NC-03", None, 8), "J": ("NC-03", None, 9), "K": ("NC-03", None, 10), "L": ("NC-03", None, 11),
          "M": ("NC-04", None, 12), "N": ("NC-04", None, 13), "O": ("NC-04", None, 14),
          "P": ("NC-05", "before", 15), "Q": ("NC-05", "verification", 16), "R": ("NC-05", "after", 17), "S": ("NC-05", "after", 18), "T": ("NC-05", "uncertain", 19)}
ITEMS = [{"id": "a-before", "title": "Gas tee before work", "stage": "before", "locations": [], "measurement": "", "criterion": "", "sources": []},
         {"id": "a-tape", "title": "Tape showing 250mm", "stage": "verification", "locations": [], "measurement": "250mm", "criterion": "", "sources": []},
         {"id": "a-during", "title": "Sleeve being fitted", "stage": "during", "locations": [], "measurement": "", "criterion": "", "sources": []},
         {"id": "a-after", "title": "Vent cover fitted", "stage": "after", "locations": [], "measurement": "", "criterion": "", "sources": []}]


async def seed():
    from auth import ph
    await cleanup(quiet=True)
    uid = {}
    for role, email in EMAILS.items():
        uid[role] = str((await db.users.insert_one({"email": email, "name": f"QA I42 {role}", "role": role, "active": True, "password_hash": ph.hash(PW),
                                                     "token_version": 0, "created_at": now_iso()})).inserted_id)
    pid = str((await db.projects.insert_one({"ref": REF, "address": "QA TEST 42 Example Road", "postcode": "QA4 2AA", "installer_id": uid["installer"],
                                             "status": "active", "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
    nids = {}
    for num in ("NC-01", "NC-02", "NC-03", "NC-04", "NC-05"):
        d = {"project_id": pid, "number": num, "description": f"QA {num}", "original_text": "Gas tee clearance 250mm", "required_work": "", "status": "employee_review",
             "safety_critical": num == "NC-05", "assigned_installer_id": uid["installer"], "updated_at": now_iso(), "export_note": "QA note", "export_note_source": "staff"}
        if num == "NC-05":
            d["checklist"] = {"v": 1, "status": "draft", "source": "ai", "profile": None, "items": ITEMS, "history": []}
        nids[num] = str((await db.ncs.insert_one(d)).inserted_id)
    fid = {}
    for i, (L, (num, st, k)) in enumerate(PHOTOS.items()):
        _id = ObjectId()
        fid[L] = str(_id)
        BLOB[f"qa42/{_id}.jpg"] = mk(k)
        doc = {"_id": _id, "project_id": pid, "nc_id": nids[num], "kind": "photo", "status": "submitted", "captured_at": f"2026-05-01 10:{i:02d}",
               "source": "in_app_camera", "sha256": f"qa42-{i}", "size": 1, "mime": "image/jpeg", "uploaded_by": uid["installer"],
               "server_received_at": f"2026-05-01T10:{i:02d}:00", "paths": {"stamped": f"qa42/{_id}.jpg"}, "qa": "iter42"}
        if st:
            doc["classification"] = {"stage": st, "location_id": None, "dup_of": None, "relevant": True}
        if L == "R":
            doc["checklist_item"] = "a-after"  # staff-marked requirement
        await db.files.insert_one(doc)
    return pid, nids, fid, uid


def login(email):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": email, "password": PW}, timeout=30)
    r.raise_for_status()
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return s


def pdf_images(pdf):
    doc = fitz.open(stream=pdf, filetype="pdf")
    seq, text = [], ""
    for pno, page in enumerate(doc):
        text += page.get_text()
        infos = sorted(page.get_image_info(xrefs=True), key=lambda i: (round(i["bbox"][1]), i["bbox"][0]))
        for inf in infos:
            if inf["xref"]:
                k = ident(fitz.Pixmap(doc, inf["xref"]).tobytes("png"))
                if k is not None:
                    seq.append(k)
    return seq, text


async def build_pack(pid):
    import server
    import storage as st_mod

    async def fake_aget(path):
        if path not in BLOB:
            raise RuntimeError(f"non-QA path requested: {path}")
        return BLOB[path], "image/jpeg"
    server.storage.aget = fake_aget  # local synthetic bytes only; storage/Dropbox never touched
    st_mod  # noqa
    p = await db.projects.find_one({"_id": ObjectId(pid)})
    data, n = await server.build_evidence_pack(pid, p)
    z = zipfile.ZipFile(io.BytesIO(data))
    return {name.split("_")[0]: pdf_images(z.read(name)) for name in z.namelist() if name.endswith(".pdf")}


def install_ai_stubs():
    import ai
    import checklist
    import recommend

    async def arrange(nc_text, req, note, works, photos, times):
        AI_CALLS.append("arrange_photos(stub)")
        return {"pre": 0, "mid": None, "final": len(photos) - 1, "extra": [], "uncertain": [], "description": "QA stub"}
    ai.arrange_photos = arrange

    async def pick(*a, **k):
        AI_CALLS.append("ai_pick(stub)")
        return None

    async def draft(*a, **k):
        AI_CALLS.append("ai_draft(stub)")
        return None
    recommend.ai_pick = pick
    checklist.ai_draft = draft
    for name in dir(ai):  # anything else in ai.* that is awaited = real call attempt -> recorded
        fn = getattr(ai, name)
        if asyncio.iscoroutinefunction(fn) and name not in ("arrange_photos",):
            async def guard(*a, _n=name, **k):
                AI_CALLS.append(f"REAL:{_n}")
                return None
            setattr(ai, name, guard)


def old_pdf_photo_groups():
    import server
    src = subprocess.run(["git", "-C", "/app", "show", "e5e8cd3:backend/server.py"], capture_output=True, text=True).stdout
    m = re.search(r"async def pdf_photo_groups\(.*?(?=\n\n\n(?:class|@|async def|def) )", src, re.S)
    ns = dict(vars(server))
    exec(m.group(0), ns)
    return ns["pdf_photo_groups"]


async def flags(v):
    await db.settings.update_one({"_id": "global"}, {"$set": {f: v for f in FLAGS}})
    import core
    if hasattr(core, "_settings_cache"):
        core._settings_cache = None


async def main():
    install_ai_stubs()
    await flags(False)
    pid, nids, fid, uid = await seed()
    rev = {v: k for k, v in fid.items()}
    L = lambda seq: [list(PHOTOS)[[p[2] for p in PHOTOS.values()].index(k)] for k in seq]  # palette idx -> letter
    a, inst = login(EMAILS["admin"]), login(EMAILS["installer"])
    # --- selection save + order persistence via public API
    r = a.put(f"{BASE}/api/ncs/{nids['NC-01']}/evidence-selection", json={"ids": [fid["D"], fid["A"], fid["C"]], "seq": 100})
    ok("PUT selection NC-01", r.status_code == 200 and r.json()["ids"] == [fid["D"], fid["A"], fid["C"]], r.text[:120])
    r = a.put(f"{BASE}/api/ncs/{nids['NC-01']}/evidence-selection", json={"ids": [fid["C"], fid["D"], fid["A"]], "seq": 101})  # = drag reorder
    g = a.get(f"{BASE}/api/projects/{pid}").json()
    n1 = next(n for n in g["ncs"] if n["number"] == "NC-01")
    ok("GET order after reorder = C,D,A", [rev[i] for i in n1["evidence_selection"]["ids"]] == ["C", "D", "A"], n1["evidence_selection"])
    r = a.put(f"{BASE}/api/ncs/{nids['NC-01']}/evidence-selection", json={"ids": [fid["B"]], "seq": 50})
    ok("older seq is stale, not applied", r.json().get("stale") is True and [rev[i] for i in r.json()["ids"]] == ["C", "D", "A"])
    r = a.put(f"{BASE}/api/ncs/{nids['NC-02']}/evidence-selection", json={"ids": [], "seq": 1})
    ok("PUT empty selection NC-02", r.status_code == 200 and r.json()["ids"] == [])
    # --- legacy pdf_include endpoint unchanged (NC-03): I always, J never, K auto, L always
    for x, inc in (("I", True), ("J", False), ("K", None), ("L", True)):
        r = a.put(f"{BASE}/api/evidence/{fid[x]}/pdf-include", json={"include": inc})
        ok(f"PUT /evidence/{x}/pdf-include={inc}", r.status_code == 200, r.text[:100])
    g = a.get(f"{BASE}/api/projects/{pid}").json()
    n3 = next(n for n in g["ncs"] if n["number"] == "NC-03")
    ok("legacy NC-03 has no evidence_selection key (or null)", not n3.get("evidence_selection"), str(n3.get("evidence_selection")))
    ok("admin GET project shape (ncs/photos keys)", {"id", "ref", "ncs"} <= set(g) and all("photos" in n for n in g["ncs"]), sorted(g)[:12])
    ri = inst.get(f"{BASE}/api/projects/{pid}")
    ok("installer GET project 200 without evidence_selection", ri.status_code == 200 and "evidence_selection" not in ri.text, ri.status_code)
    r = inst.put(f"{BASE}/api/ncs/{nids['NC-01']}/evidence-selection", json={"ids": [], "seq": 999})
    ok("installer cannot PUT selection (403)", r.status_code == 403, r.status_code)
    r = a.get(f"{BASE}/api/projects/{pid}/evidence-packs")
    ok("GET evidence-packs responds", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    r = a.get(f"{BASE}/api/ncs/{nids['NC-05']}/evidence-review")
    ok("flags OFF: evidence-review enabled false", r.status_code == 200 and r.json().get("enabled") is False, r.text[:100])
    r = a.post(f"{BASE}/api/ncs/{nids['NC-05']}/evidence-selection/auto", json={"mode": "replace"})
    ok("flags OFF: Auto Select blocked (409)", r.status_code == 409, f"{r.status_code} {r.text[:100]}")
    r = a.put(f"{BASE}/api/ncs/{nids['NC-05']}/evidence-selection", json={"ids": [fid["T"]], "seq": 1})
    ok("flags OFF: manual select works", r.status_code == 200 and r.json()["ids"] == [fid["T"]])

    # --- PDF (flags OFF)
    old = old_pdf_photo_groups()
    import server
    AI_CALLS.clear()
    pdfs = await build_pack(pid)
    ok("pack has 5 NC PDFs", sorted(pdfs) == ["NC-01", "NC-02", "NC-03", "NC-04", "NC-05"], sorted(pdfs))
    ok("PDF NC-01: only selected, order C,D,A", L(pdfs["NC-01"][0]) == ["C", "D", "A"], L(pdfs["NC-01"][0]))
    print("   NC-01 headings:", [h for h in ("BEFORE REMEDIATION", "COMPLETED WORK", "MEASUREMENT / TEST EVIDENCE", "REMEDIATION PHOTOGRAPHS") if h in pdfs["NC-01"][1]])
    ok("PDF NC-02 (ids==[]): zero evidence photos", pdfs["NC-02"][0] == [], L(pdfs["NC-02"][0]))
    ok("PDF NC-03 legacy pdf_include: I,L only", L(pdfs["NC-03"][0]) == ["I", "L"], L(pdfs["NC-03"][0]))
    ok("PDF NC-05 manual [T] only", L(pdfs["NC-05"][0]) == ["T"], L(pdfs["NC-05"][0]))
    print("   NC-04 legacy auto:", L(pdfs["NC-04"][0]), "AI calls during build:", AI_CALLS)

    async def getb(f, *vs):
        for v in vs:
            if f.get("paths", {}).get(v) in BLOB:
                return BLOB[f["paths"][v]]
    for num in ("NC-03", "NC-04"):  # legacy: identical to the pre-selection implementation (git e5e8cd3)
        n = await db.ncs.find_one({"_id": ObjectId(nids[num])})
        files = await db.files.find({"nc_id": nids[num]}).sort("server_received_at", 1).to_list(100)
        new_g, old_g = await server.pdf_photo_groups(dict(n), files, getb), await old(dict(n), files, getb)
        ok(f"legacy {num} groups identical to pre-selection code", new_g == old_g, [(h, len(x)) for h, x in new_g])
    ok("no REAL AI calls", not [c for c in AI_CALLS if c.startswith("REAL")], AI_CALLS)

    # --- PDF with smart_pdf ON: selection still authoritative; legacy NC-03 unchanged vs old code
    await flags(True)
    pdfs = await build_pack(pid)
    ok("flags ON PDF NC-01 still C,D,A", L(pdfs["NC-01"][0]) == ["C", "D", "A"], L(pdfs["NC-01"][0]))
    ok("flags ON PDF NC-02 still empty", pdfs["NC-02"][0] == [])
    for num in ("NC-03", "NC-04"):
        n = await db.ncs.find_one({"_id": ObjectId(nids[num])})
        files = await db.files.find({"nc_id": nids[num]}).sort("server_received_at", 1).to_list(100)
        ok(f"flags ON legacy {num} identical to pre-selection code", await server.pdf_photo_groups(dict(n), files, getb) == await old(dict(n), files, getb))

    # --- Auto Select (flags ON, in-process, AI stubbed)
    from selection import auto_select, AutoIn
    from core import User
    adm = User.from_mongo(await db.users.find_one({"email": EMAILS["admin"]}))
    AI_CALLS.clear()
    res = await auto_select(nids["NC-05"], AutoIn(mode="add"), adm)
    ids = [rev[i] for i in res["selection"]["ids"]]
    ok("Auto add: keeps T + adds P,Q,R(staff) in item order", ids == ["T", "P", "Q", "R"], ids)
    ok("Auto: missing lists 'Sleeve being fitted' (not filled)", res["missing"] == ["Sleeve being fitted"], res["missing"])
    ok("Auto: staff-marked a-after keeps R (not S)", "S" not in ids and "R" in ids)
    ok("Auto: ai_used false", res["ai_used"] is False, AI_CALLS)
    ok("Auto: ai_ids = rule picks P,Q only", sorted(rev[i] for i in res["selection"]["ai_ids"]) == ["P", "Q"], res["selection"]["ai_ids"])
    ok("Auto: review_notes safety-critical", any("Safety" in x for x in res["review_notes"]), res["review_notes"])
    ok("Auto: checklist_status draft", res["checklist_status"] == "draft")
    import recommend
    v = await recommend.view(nids["NC-05"])
    ok("Auto uses recommend.view items", [i["file_id"] and rev[i["file_id"]] for i in v["items"]] == ["P", "Q", None, "R"], [(i["item_id"], i["file_id"] and rev[i["file_id"]], i["source"]) for i in v["items"]])
    res = await auto_select(nids["NC-05"], AutoIn(mode="replace"), adm)
    ids = [rev[i] for i in res["selection"]["ids"]]
    ok("Auto replace: P,Q,R only", ids == ["P", "Q", "R"], ids)
    ok("Auto: no REAL AI calls", not [c for c in AI_CALLS if c.startswith("REAL")], AI_CALLS)
    print("   AI stub calls during auto:", AI_CALLS)
    r = a.get(f"{BASE}/api/ncs/{nids['NC-05']}/evidence-review")
    ok("flags ON: evidence-review enabled true", r.status_code == 200 and r.json().get("enabled") is True, r.text[:80])
    pdfs = await build_pack(pid)
    ok("PDF NC-05 after Auto replace: P,Q,R", L(pdfs["NC-05"][0]) == ["P", "Q", "R"], L(pdfs["NC-05"][0]))
    await flags(False)
    print(json.dumps({"passed": sum(1 for _, c in RES if c), "total": len(RES), "failed": [n for n, c in RES if not c]}))


async def cleanup(quiet=False):
    out = {}
    projs = [str(p["_id"]) async for p in db.projects.find({"ref": REF})]
    ncs = [str(n["_id"]) async for n in db.ncs.find({"project_id": {"$in": projs}})]
    users = [str(u["_id"]) async for u in db.users.find({"email": {"$in": list(EMAILS.values())}})]
    out["learning_examples"] = (await db.learning_examples.delete_many({"nc_id": {"$in": ncs}})).deleted_count
    out["files"] = (await db.files.delete_many({"project_id": {"$in": projs}, "qa": "iter42"})).deleted_count
    out["ncs"] = (await db.ncs.delete_many({"_id": {"$in": [ObjectId(n) for n in ncs]}})).deleted_count
    out["projects"] = (await db.projects.delete_many({"_id": {"$in": [ObjectId(p) for p in projs]}})).deleted_count
    for c in ("sessions", "refresh_tokens"):
        if c in await db.list_collection_names():
            out[c] = (await db[c].delete_many({"user_id": {"$in": users}})).deleted_count
    out["users"] = (await db.users.delete_many({"email": {"$in": list(EMAILS.values())}})).deleted_count
    for c in ("auto_packs", "dropbox_exports", "notifications"):
        out[c] = (await db[c].delete_many({"project_id": {"$in": projs}})).deleted_count
    from pathlib import Path
    pack_dir = Path(os.environ.get("MEDIA_CACHE_DIR", "/tmp/rf_media")) / "packs"
    gone = [f for pid in projs for f in pack_dir.glob(f"{pid}-*")] if pack_dir.exists() else []
    for f in gone:
        f.unlink()
    out["pack_cache_files"] = len(gone)
    await flags(False)
    if not quiet:
        s = await db.settings.find_one({"_id": "global"})
        print(out, {f: s.get(f) for f in FLAGS})


async def show():
    import server
    p = await db.projects.find_one({"ref": REF})
    pid = str(p["_id"])
    out = {"pid": pid, "fp": await server.pack_fingerprint(pid, p), "ncs": {}}
    async for n in db.ncs.find({"project_id": pid}).sort("number", 1):
        fs = [str(f["_id"]) async for f in db.files.find({"nc_id": str(n["_id"])}).sort("server_received_at", 1)]
        out["ncs"][n["number"]] = {"id": str(n["_id"]), "photos": fs, "sel": (n.get("evidence_selection") or {}).get("ids"), "seq": (n.get("evidence_selection") or {}).get("seq")}
    print(json.dumps(out))


async def pdf_now():
    """PDF from the CURRENT DB state (after UI edits) via cached_pack (local pack cache only)."""
    import server
    import recommend
    install_ai_stubs()
    p = await db.projects.find_one({"ref": REF})
    pid = str(p["_id"])
    fs = await db.files.find({"project_id": pid, "qa": "iter42"}).sort("server_received_at", 1).to_list(100)
    for f, k in zip(fs, [v[2] for v in PHOTOS.values()]):
        BLOB[f["paths"]["stamped"]] = mk(k)

    async def fake_aget(path):
        if path not in BLOB:
            raise RuntimeError(f"non-QA path requested: {path}")
        return BLOB[path], "image/jpeg"
    server.storage.aget = fake_aget
    data, fp = await server.cached_pack(pid, p)
    print("pack fp", fp, "cached file exists:", (server.PACK_DIR / f"{pid}-{fp}.zip").exists())
    z = zipfile.ZipFile(io.BytesIO(data))
    letters = list(PHOTOS)
    for name in sorted(z.namelist()):
        if name.endswith(".pdf"):
            seq, _ = pdf_images(z.read(name))
            print(name.split("_")[0], [letters[[v[2] for v in PHOTOS.values()].index(k)] for k in seq])
    n5 = await db.ncs.find_one({"project_id": pid, "number": "NC-05"})
    ph = await recommend._photos(str(n5["_id"]))
    print("NC-05 recs cache valid (Auto Select via HTTP = no AI):", recommend._fp(n5["checklist"], ph, await recommend._profile(n5)) == (n5.get("photo_recs") or {}).get("fp"))
    print("AI calls:", AI_CALLS)
    # public HTTP download path: served from the local pack cache just built (fingerprint unchanged -> no storage reads)
    s = login(EMAILS["admin"])
    url = s.get(f"{BASE}/api/export/link", params={"project_id": pid}).json()["url"]
    r = requests.get(f"{BASE}{url}", timeout=120)
    print("HTTP export zip:", r.status_code, r.headers.get("content-type"), len(r.content), "same bytes as cache:", r.content == data)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    for name in sorted(z.namelist()):
        if name.endswith(".pdf"):
            seq, _ = pdf_images(z.read(name))
            print("  HTTP", name.split("_")[0], [letters[[v[2] for v in PHOTOS.values()].index(k)] for k in seq])


cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
asyncio.get_event_loop().run_until_complete(main() if cmd == "run" else show() if cmd == "show" else pdf_now() if cmd == "pdf" else flags(cmd == "flags_on") if cmd.startswith("flags") else cleanup())
