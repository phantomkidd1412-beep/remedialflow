# Iter44: removed AI photos never return + Reject/Replace sync with PDF selection. In-process ASGI (AI stubbed, synthetic colour photos).
# Usage: python qa_iter44_removed_sync.py [run|seed_ui|cleanup]. Never touches real projects/storage/Dropbox.
import asyncio
import json
import sys

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
import httpx  # noqa: E402
from bson import ObjectId  # noqa: E402

import types  # noqa: E402
q42 = types.ModuleType("q42")  # reuse iter42 helpers (palette, mk, ident, pdf_images, AI stubs) without running its CLI
_src = open("/app/backend/tests/qa_iter42_pdf_e2e.py").read()
exec(compile(_src[:_src.rindex("\ncmd = sys.argv")], "q42", "exec"), q42.__dict__)
from core import db, now_iso  # noqa: E402

PW = "QaCheck-7744x"
EMAILS = {"admin": "qa.i44.admin@example.com", "installer": "qa.i44.installer@example.com"}
REF = "QA-I44"
FLAGS = q42.FLAGS
BLOB = q42.BLOB
RES = q42.RES
ok = q42.ok


def items(p, during=True):
    out = [{"id": f"{p}-before", "title": "Gas tee before work", "stage": "before", "locations": [], "measurement": "", "criterion": "", "sources": []},
           {"id": f"{p}-tape", "title": "Tape showing 250mm", "stage": "verification", "locations": [], "measurement": "250mm", "criterion": "", "sources": []},
           {"id": f"{p}-after", "title": "Vent cover fitted", "stage": "after", "locations": [], "measurement": "", "criterion": "", "sources": []}]
    if during:
        out.append({"id": f"{p}-during", "title": "Sleeve being fitted", "stage": "during", "locations": [], "measurement": "", "criterion": "", "sources": []})
    return out


async def seed():
    from auth import ph
    await cleanup(quiet=True)
    uid = {}
    for role, email in EMAILS.items():
        uid[role] = str((await db.users.insert_one({"email": email, "name": f"QA I44 {role}", "role": role, "active": True, "password_hash": ph.hash(PW),
                                                     "token_version": 0, "created_at": now_iso()})).inserted_id)
    pid = str((await db.projects.insert_one({"ref": REF, "address": "QA TEST 44 Example Road", "postcode": "QA4 4AA", "installer_id": uid["installer"],
                                             "status": "active", "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
    spec = {"NC-01": ("a", ["before", "before", "verification", "after", "during"]), "NC-02": ("b", ["before", "verification", "after"]),
            "NC-03": ("c", ["before", "verification", "after"])}
    out, k = {"pid": pid, "uid": uid}, 0
    for num, (p, stages) in spec.items():
        nid = str((await db.ncs.insert_one({"project_id": pid, "number": num, "description": f"QA {num}", "original_text": "Gas tee clearance 250mm", "required_work": "",
                                            "status": "employee_review", "safety_critical": False, "assigned_installer_id": uid["installer"], "export_note": "QA note",
                                            "export_note_source": "staff", "checklist": {"v": 1, "status": "confirmed", "source": "staff", "profile": None,
                                                                                         "items": items(p, num == "NC-01"), "history": []}, "updated_at": now_iso()})).inserted_id)
        fids = []
        for s in stages:
            _id = ObjectId()
            BLOB[f"qa44/{_id}.jpg"] = q42.mk(k)
            await db.files.insert_one({"_id": _id, "project_id": pid, "nc_id": nid, "kind": "photo", "status": "submitted", "captured_at": f"2026-05-01 10:{k:02d}",
                                       "source": "in_app_camera", "sha256": f"qa44-{k}", "size": 1, "mime": "image/jpeg", "uploaded_by": uid["installer"],
                                       "server_received_at": f"2026-05-01T10:{k:02d}:00", "paths": {"stamped": f"qa44/{_id}.jpg"},
                                       "classification": {"stage": s, "location_id": None, "dup_of": None, "relevant": True}, "qa": "iter44"})
            fids.append((str(_id), k))
            k += 1
        out[num] = {"nid": nid, "files": fids}
    return out


async def cleanup(quiet=False):
    out = {}
    projs = [str(p["_id"]) async for p in db.projects.find({"ref": REF})]
    ncs = [str(n["_id"]) async for n in db.ncs.find({"project_id": {"$in": projs}})]
    users = [str(u["_id"]) async for u in db.users.find({"email": {"$in": list(EMAILS.values())}})]
    out["learning_examples"] = (await db.learning_examples.delete_many({"nc_id": {"$in": ncs}})).deleted_count
    out["nc_profiles"] = (await db.nc_profiles.delete_many({"source_nc": {"$in": ncs}})).deleted_count
    out["files"] = (await db.files.delete_many({"nc_id": {"$in": ncs}})).deleted_count
    out["ncs"] = (await db.ncs.delete_many({"_id": {"$in": [ObjectId(n) for n in ncs]}})).deleted_count
    for c in ("auto_packs", "dropbox_exports"):
        out[c] = (await db[c].delete_many({"project_id": {"$in": projs}})).deleted_count
    out["projects"] = (await db.projects.delete_many({"_id": {"$in": [ObjectId(p) for p in projs]}})).deleted_count
    for c in ("sessions", "refresh_tokens", "notifications"):
        if c in await db.list_collection_names():
            out[c] = (await db[c].delete_many({"user_id": {"$in": users}})).deleted_count
    out["users"] = (await db.users.delete_many({"email": {"$in": list(EMAILS.values())}})).deleted_count
    if not quiet:
        print("CLEANUP", out)


async def set_flags(v):
    await db.settings.update_one({"_id": "global"}, {"$set": {f: v for f in FLAGS}})
    s = await db.settings.find_one({"_id": "global"})
    print("FLAGS", {f: s.get(f) for f in FLAGS})


async def run():
    import recommend
    import server
    q42.install_ai_stubs()

    async def stub(nc_, its, pool, ex, docs):  # 'AI' picks pool[1] (unsure)
        q42.AI_CALLS.append("ai_pick(stub1)")
        return {i["id"]: {"index": 1 if len(pool) > 1 else 0, "reason": "Stub pick", "uncertain": True} for i in its}
    recommend.ai_pick = stub
    d = await seed()
    await set_flags(True)
    import core
    for a in ("_settings", "SETTINGS_CACHE"):
        if hasattr(core, a):
            setattr(core, a, None)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://qa")
    r = await c.post("/api/auth/login", json={"email": EMAILS["admin"], "password": PW})
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    n1 = d["NC-01"]["nid"]
    P = [f for f, _ in d["NC-01"]["files"]]  # P0 before, P1 before, P2 verification, P3 after, P4 during
    col = {f: k for f, k in d["NC-01"]["files"] + d["NC-02"]["files"] + d["NC-03"]["files"]}

    async def nc(nid):
        return await db.ncs.find_one({"_id": ObjectId(nid)})

    async def sel(nid):
        return (await nc(nid)).get("evidence_selection") or {}

    async def recs(nid):
        return {i["item_id"]: i for i in (await c.get(f"/api/ncs/{nid}/recommendations")).json()["items"]}

    async def auto(nid, mode):
        r = await c.post(f"/api/ncs/{nid}/evidence-selection/auto", json={"mode": mode})
        return r.status_code, r.json()

    # 1. Auto Select builds recs (stub) and selects one per requirement
    st, a = await auto(n1, "replace")
    ok("auto replace 200", st == 200, a.get("detail"))
    s = await sel(n1)
    ok("auto picks P1,P2,P3,P4 (AI before=P1)", s.get("ids") == [P[1], P[2], P[3], P[4]], [P.index(x) for x in s.get("ids", [])])
    # 2. Gallery deselect AI pick P3 -> removed + rejected_items
    r = await c.put(f"/api/ncs/{n1}/evidence-selection", json={"ids": [P[1], P[2], P[4]], "seq": 5})
    s = await sel(n1)
    ok("deselect -> removed has P3", P[3] in (s.get("removed") or []), s.get("removed"))
    f3 = await db.files.find_one({"_id": ObjectId(P[3])})
    ok("P3 rejected_items has a-after", "a-after" in (f3.get("rejected_items") or []), f3.get("rejected_items"))
    # 3. Forced cache rebuild + Auto Select add/replace never re-add P3
    r = await c.post(f"/api/ncs/{n1}/recommendations", json={"force": True})
    rc = await recs(n1)
    ok("rebuild: a-after not re-suggested P3", rc["a-after"]["file_id"] != P[3], rc["a-after"])
    for mode in ("add", "replace"):
        st, a = await auto(n1, mode)
        ok(f"auto {mode} after rebuild: P3 absent", P[3] not in (await sel(n1))["ids"], [P.index(x) for x in (await sel(n1))["ids"]])
    # 4. Checklist edit + re-confirm (new version -> new cache) then Auto Select
    its = items("a") + [{"id": None, "title": "Extra overview", "stage": "after", "locations": [], "measurement": "", "criterion": ""}]
    r = await c.put(f"/api/ncs/{n1}/checklist", json={"items": [{k: v for k, v in i.items() if k != "sources"} for i in its]})
    ok("checklist update 200", r.status_code == 200, r.text[:200])
    for mode in ("add", "replace"):
        st, a = await auto(n1, mode)
        ids = (await sel(n1))["ids"]
        ok(f"auto {mode} after checklist update: P3 absent", st == 200 and P[3] not in ids, f"{st} {[P.index(x) for x in ids]} {a.get('missing')}")
        rcx = await recs(n1)
        need = {v["title"] for v in rcx.values() if v["file_id"] is None or v["file_id"] in ((await sel(n1)).get("removed") or [])}
        ok(f"iter45 #3 auto {mode}: every requirement without a usable pick (incl. removed-photo pick) is in missing", "Extra overview" in need and need <= set(a.get("missing") or []), f"need={need} missing={a.get('missing')}")
    rc = await recs(n1)
    print("INFO after checklist update recs", {k: v["file_id"] and P.index(v["file_id"]) for k, v in rc.items()}, "(a removed photo may still be SUGGESTED for a NEW requirement; it is filtered + listed missing)")
    # revert checklist to the original 4 items for the rest of the flow
    r = await c.put(f"/api/ncs/{n1}/checklist", json={"items": [{k: v for k, v in i.items() if k != "sources"} for i in items("a")]})
    st, a = await auto(n1, "replace")
    # 5. Manual reselect P3 + drag reorder -> persists; Auto Select add keeps it
    s0 = await sel(n1)
    order = [P[2], P[4], P[3], P[1]]
    r = await c.put(f"/api/ncs/{n1}/evidence-selection", json={"ids": order, "seq": (s0.get("seq") or 0) + 1})
    s = await sel(n1)
    ok("manual reselect P3 persists + removed cleared", s["ids"] == order and P[3] not in (s.get("removed") or []), [P.index(x) for x in s["ids"]])
    st, a = await auto(n1, "add")
    ok("auto add keeps manual order incl P3", (await sel(n1))["ids"][:4] == order, [P.index(x) for x in (await sel(n1))["ids"]])
    r = await c.put(f"/api/ncs/{n1}/evidence-selection", json={"ids": order, "seq": (await sel(n1))["seq"] + 1})
    # 6. Reject 'during' (P4 pending rule pick) -> leaves selection immediately
    rc = await recs(n1)
    ok("during pending rule pick P4", rc["a-during"]["file_id"] == P[4] and rc["a-during"]["state"] == "pending", rc["a-during"])
    r = await c.put(f"/api/ncs/{n1}/recommendations/a-during", json={"action": "reject"})
    s = await sel(n1)
    ok("reject removes P4 from selection, order kept", s["ids"] == [P[2], P[3], P[1]] and P[4] in s["removed"], [P.index(x) for x in s["ids"]])
    # 7. Replace 'tape' (P2, first position) with P0 -> P0 takes position 0
    r = await c.put(f"/api/ncs/{n1}/recommendations/a-tape", json={"action": "replace", "file_id": P[0]})
    s = await sel(n1)
    ok("replace: P0 at P2's position, P2 removed", s["ids"] == [P[0], P[3], P[1]] and P[2] in s["removed"], [P.index(x) for x in s["ids"]])
    ok("replacement not in ai_ids", P[0] not in s.get("ai_ids", []), s.get("ai_ids"))
    t = (await recs(n1))["a-tape"]
    ok("replaced state/reason", t["state"] == "replaced" and t["reason"] == "Chosen by staff — awaiting confirmation" and t["source"] == "staff", t)
    ok("replaced flags: confirm + measurement", "Replacement chosen by staff; confirm it" in t["flags"] and any("Measurement" in x for x in t["flags"]), t["flags"])
    # 8. Auto Select add/replace after reject/replace: P2/P4 never return, replacement kept
    for mode in ("add", "replace"):
        st, a = await auto(n1, mode)
        ids = (await sel(n1))["ids"]
        ok(f"auto {mode} after reject/replace: no P2/P4, P0 kept", P[2] not in ids and P[4] not in ids and P[0] in ids, [P.index(x) for x in ids])
    t = (await recs(n1))["a-tape"]
    ok("replacement still 'replaced' after auto select (not Accepted by staff)", t["state"] == "replaced" and "Accepted" not in t["reason"], t["reason"])
    r = await c.put(f"/api/ncs/{n1}/evidence-selection", json={"ids": [P[0], P[3], P[1]], "seq": (await sel(n1))["seq"] + 1})
    # 9. Confirm replacement
    r = await c.put(f"/api/ncs/{n1}/recommendations/a-tape", json={"action": "accept"})
    t = (await recs(n1))["a-tape"]
    ok("confirm -> accepted / 'Replacement confirmed by staff'", t["state"] == "accepted" and t["reason"] == "Replacement confirmed by staff", t)
    ok("confirm does not change selection", (await sel(n1))["ids"] == [P[0], P[3], P[1]])
    # 10. Legacy NC-02 (no explicit selection): reject 'after' rule pick
    n2 = d["NC-02"]["nid"]
    Q = [f for f, _ in d["NC-02"]["files"]]
    p = await db.projects.find_one({"_id": ObjectId(d["pid"])})
    fp0 = await server.pack_fingerprint(d["pid"], p)
    r = await c.post(f"/api/ncs/{n2}/recommendations", json={"force": False})
    r = await c.put(f"/api/ncs/{n2}/recommendations/b-after", json={"action": "reject"})
    ok("legacy reject 200 + no selection created", r.status_code == 200 and "evidence_selection" not in await nc(n2), r.text[:120])
    fp1 = await server.pack_fingerprint(d["pid"], p)
    ok("pack fingerprint changes after rejection", fp0 != fp1)
    # 11. PDF output
    packs = await q42.build_pack(d["pid"])
    s1 = packs.get("NC-01", ([], ""))[0]
    ok("NC-01 PDF photos = final selection order P0,P3,P1", s1 == [col[P[0]], col[P[3]], col[P[1]]], s1)
    s2 = packs.get("NC-02", ([], ""))[0]
    ok("NC-02 legacy PDF excludes rejected Q2", col[Q[2]] not in s2 and s2, s2)
    # 12. Probe: AI-recommended photo Accepted (legacy) then manually selected then deselected -> Auto Select add?
    n3 = d["NC-03"]["nid"]
    R = [f for f, _ in d["NC-03"]["files"]]
    await c.post(f"/api/ncs/{n3}/recommendations", json={"force": False})
    await c.put(f"/api/ncs/{n3}/recommendations/c-before", json={"action": "accept"})
    await c.put(f"/api/ncs/{n3}/evidence-selection", json={"ids": [R[0]], "seq": 1})
    await c.put(f"/api/ncs/{n3}/evidence-selection", json={"ids": [], "seq": 2})
    st, a = await auto(n3, "add")
    ids = (await sel(n3))["ids"]
    ok("PROBE accepted rec deselected manually is not auto-reselected", R[0] not in ids, f"ids={[R.index(x) for x in ids]} removed={(await sel(n3)).get('removed')}")
    # 13. Probe: confirmed staff replacement P0 deselected in gallery -> Auto Select add?
    await c.put(f"/api/ncs/{n1}/evidence-selection", json={"ids": [P[3], P[1]], "seq": (await sel(n1))["seq"] + 1})
    st, a = await auto(n1, "add")
    ids = (await sel(n1))["ids"]
    ok("PROBE deselected staff replacement not auto-reselected", P[0] not in ids, f"ids={[P.index(x) for x in ids]} removed={(await sel(n1)).get('removed')}")
    le = await db.learning_examples.find_one({"nc_id": n1, "action": "deselect_staff", "rejected_file_id": P[0]})
    ok("iter45 #2 deselect staff replacement -> deselect_staff learning", bool(le), le)
    t = (await recs(n1))["a-tape"]
    ok("iter45 #2 staff replacement rec -> rejected after deselect", t["state"] == "rejected" and t["file_id"] is None, t)
    ok("iter45 #2/#3 auto add lists Tape requirement in missing", "Tape showing 250mm" in (a.get("missing") or []), a.get("missing"))
    # 15. iter45 #1: hand-ticked PENDING suggestion (not via Auto Select) then unticked (exact public-URL repro of iter44 HIGH)
    s_ = await sel(n2)
    rc2 = await recs(n2)
    ok("iter45 #1 NC-02 b-before pending pick Q0", rc2["b-before"]["file_id"] == Q[0] and rc2["b-before"]["state"] == "pending", rc2["b-before"])
    await c.put(f"/api/ncs/{n2}/evidence-selection", json={"ids": [Q[0]], "seq": 1})
    await c.put(f"/api/ncs/{n2}/evidence-selection", json={"ids": [], "seq": 2})
    s_ = await sel(n2)
    ok("iter45 #1 hand-ticked untick -> removed has Q0", Q[0] in (s_.get("removed") or []), s_)
    fq = await db.files.find_one({"_id": ObjectId(Q[0])})
    ok("iter45 #1 Q0 rejected_items has b-before", "b-before" in (fq.get("rejected_items") or []), fq.get("rejected_items"))
    ok("iter45 #1 deselect_ai learning written", bool(await db.learning_examples.find_one({"nc_id": n2, "action": "deselect_ai", "rejected_file_id": Q[0]})))
    t = (await recs(n2))["b-before"]
    ok("iter45 #1 rec b-before rejected", t["state"] == "rejected" and t["file_id"] != Q[0], t)
    for mode in ("add", "replace"):
        st, a = await auto(n2, mode)
        ids = (await sel(n2))["ids"]
        bb = (await recs(n2))["b-before"]
        ok(f"iter45 #1/#3 auto {mode}: Q0 absent; before req missing unless a different photo is suggested", st == 200 and Q[0] not in ids and ("Gas tee before work" in (a.get("missing") or []) or (bb["file_id"] and bb["file_id"] != Q[0])),
           f"{st} ids={[Q.index(x) for x in ids]} missing={a.get('missing')}")
    await c.post(f"/api/ncs/{n2}/recommendations", json={"force": True})
    st, a = await auto(n2, "add")
    print("DEBUG NC-02 recs", {k: (v["file_id"] and Q.index(v["file_id"]), v["state"], v["source"], v["reason"]) for k, v in (await recs(n2)).items()})
    ok("iter45 #1 after forced rebuild auto add: Q0 absent", Q[0] not in (await sel(n2))["ids"], [Q.index(x) for x in (await sel(n2))["ids"]])
    # 16. iter45 #4: replacement already selected while original (b-before AI pick) not selected -> order unchanged; replace with same photo -> unchanged
    orig = (await recs(n2))["b-before"]["file_id"]
    rest = [x for x in Q if x not in (orig, Q[2])][0]
    order = [Q[2], rest]
    await c.put(f"/api/ncs/{n2}/evidence-selection", json={"ids": order, "seq": (await sel(n2))["seq"] + 1})
    r = await c.put(f"/api/ncs/{n2}/recommendations/b-before", json={"action": "replace", "file_id": Q[2]})
    ok("iter45 #4 replace (orig not selected, new selected) keeps order", r.status_code == 200 and (await sel(n2))["ids"] == order, f"orig={orig and Q.index(orig)} {[Q.index(x) for x in (await sel(n2))['ids']]}")
    r = await c.put(f"/api/ncs/{n2}/recommendations/b-before", json={"action": "replace", "file_id": Q[2]})
    s_ = await sel(n2)
    ok("iter45 #4 replace with SAME photo keeps order + not removed", s_["ids"] == order and Q[2] not in s_.get("removed", []), [Q.index(x) for x in s_["ids"]])
    t = (await recs(n2))["b-before"]
    ok("iter45 #4 same-photo replace not 'Accepted by staff'", t["state"] == "replaced" and "Accepted" not in t["reason"], t["reason"])
    # 17. shared photo probe: replacing a requirement whose pick is ALSO another requirement's pick
    rc2 = await recs(n2)
    shared = [k for k, v in rc2.items() if v["file_id"] and sum(1 for w in rc2.values() if w["file_id"] == v["file_id"]) > 1]
    print("DEBUG shared picks", shared)
    print("AI calls:", q42.AI_CALLS)
    await c.aclose()
    await set_flags(False)
    await cleanup()
    print(f"RESULT {sum(x for _, x in RES)}/{len(RES)} passed")


async def seed_ui():
    import recommend
    q42.install_ai_stubs()

    async def stub(nc_, its, pool, ex, docs):
        return {i["id"]: {"index": 1 if len(pool) > 1 else 0, "reason": "Stub pick", "uncertain": True} for i in its}
    d = await seed()
    await set_flags(True)
    for num in ("NC-01", "NC-02", "NC-03"):
        await recommend.build(d[num]["nid"], ai=stub)
    print(json.dumps({"pid": d["pid"], **{k: {"nid": v["nid"], "files": [f for f, _ in v["files"]]} for k, v in d.items() if k.startswith("NC")}}))


cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
loop = asyncio.new_event_loop()
if cmd == "cleanup":
    loop.run_until_complete(cleanup())
    loop.run_until_complete(set_flags(False))
else:
    loop.run_until_complete({"run": run, "seed_ui": seed_ui}[cmd]())
