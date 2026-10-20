"""Phase 10: Work Tracker (+CSV/Excel), soft delete + Admin recycle bin, Admin activity log."""
import csv
import io
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from auth import require
from core import User, audit, db, now_iso, oid, signed_query, verify_signed

router = APIRouter(prefix="/api")
staff = require("admin", "employee")
admin = require("admin")


# ---------------- soft delete / recycle bin ----------------
@router.delete("/projects/{pid}")
async def delete_project(pid: str, u: User = Depends(staff)):
    """Soft delete only: nothing is erased. The project disappears from dashboards, searches, queues and job lists."""
    info = {"at": now_iso(), "by": u.id, "by_name": u.name, "by_role": u.role}
    p = await db.projects.find_one_and_update({"_id": oid(pid), "deleted": None}, {"$set": {"deleted": info}})
    if not p:
        raise HTTPException(404, "Project not found")
    await db.ncs.update_many({"project_id": pid}, {"$set": {"project_deleted": True}})
    await db.notifications.update_many({"project_id": pid}, {"$set": {"hidden": True}})
    await audit("project.deleted", u.id, f"project:{pid}", {"ref": p["ref"], "address": p["address"], "role": u.role})
    return {"ok": True}


@router.get("/admin/deleted-projects")
async def deleted_projects(u: User = Depends(admin)):
    docs = await db.projects.find({"deleted": {"$ne": None}}, {"ref": 1, "address": 1, "postcode": 1, "deleted": 1, "nc_total": 1}).sort("deleted.at", -1).to_list(1000)
    return [{"id": str(d["_id"]), "ref": d["ref"], "address": d["address"], "postcode": d.get("postcode", ""),
             "nc_total": d.get("nc_total", 0), "deleted": d["deleted"]} for d in docs]


@router.post("/admin/projects/{pid}/restore")
async def restore_project(pid: str, u: User = Depends(admin)):
    """Restores the same record (no copies), so assignments, earnings and notifications are never duplicated."""
    from server import recompute_project
    p = await db.projects.find_one({"_id": oid(pid), "deleted": {"$ne": None}}, {"deleted": 1, "ref": 1})
    if not p:
        raise HTTPException(404, "Deleted project not found")
    entry = {**p["deleted"], "restored_at": now_iso(), "restored_by": u.id, "restored_by_name": u.name}
    r = await db.projects.update_one({"_id": p["_id"], "deleted": p["deleted"]}, {"$set": {"deleted": None}, "$push": {"deletion_history": entry}})
    if not r.modified_count:
        raise HTTPException(409, "Project was already restored")
    await db.ncs.update_many({"project_id": pid}, {"$set": {"project_deleted": False}})
    await db.notifications.update_many({"project_id": pid}, {"$set": {"hidden": False}})
    await recompute_project(pid)  # idempotent: settles only earnings that don't exist yet
    await audit("project.restored", u.id, f"project:{pid}", {"ref": p["ref"], "deleted_at": p["deleted"]["at"], "deleted_by": p["deleted"]["by"]})
    return {"ok": True}


# ---------------- activity log (admin, read-only) ----------------
DELETION_EVENTS = ["project.deleted", "project.restored", "evidence.discarded", "evidence.excluded", "evidence.restored"]


@router.get("/admin/activity")
async def activity(search: str = "", kind: str = "", page: int = 1, limit: int = Query(50, le=200), u: User = Depends(admin)):
    """Hash-chained audit log. There is no API to edit or delete entries."""
    q: dict = {}
    if kind == "deletion":
        q["event"] = {"$in": DELETION_EVENTS}
    elif kind:
        q["target"] = {"$regex": f"^{re.escape(kind)}:"}
    if search:
        rx = {"$regex": re.escape(search), "$options": "i"}
        pids = [f"project:{d['_id']}" for d in await db.projects.find({"$or": [{"ref": rx}, {"address": rx}]}, {"_id": 1}).to_list(500)]
        uids = [str(d["_id"]) for d in await db.users.find({"name": rx}, {"_id": 1}).to_list(200)]
        q["$or"] = [{"event": rx}, {"actor_name": rx}, {"actor_id": {"$in": uids}}, {"target": {"$in": pids}}]
    total = await db.audit.count_documents(q)
    docs = await db.audit.find(q, {"_id": 0}).sort("seq", -1).skip((page - 1) * limit).limit(limit).to_list(limit)
    users = {str(d["_id"]): d for d in await db.users.find({"_id": {"$in": [ObjectId(a) for a in {d.get("actor_id") for d in docs} if a and ObjectId.is_valid(a)]}}, {"name": 1, "role": 1}).to_list(500)}
    tid = lambda t: t.split(":", 1)[1] if ":" in t else ""  # noqa: E731
    ncs = {str(d["_id"]): d for d in await db.ncs.find({"_id": {"$in": [ObjectId(tid(d["target"])) for d in docs if d["target"].startswith("nc:") and ObjectId.is_valid(tid(d["target"]))]}}, {"number": 1, "project_id": 1}).to_list(500)}
    pids = {tid(d["target"]) for d in docs if d["target"].startswith("project:")} | {n["project_id"] for n in ncs.values()}
    projs = {str(d["_id"]): d["ref"] for d in await db.projects.find({"_id": {"$in": [ObjectId(p) for p in pids if ObjectId.is_valid(p)]}}, {"ref": 1}).to_list(500)}
    tusers = {str(d["_id"]): d["name"] for d in await db.users.find({"_id": {"$in": [ObjectId(tid(d["target"])) for d in docs if d["target"].startswith("user:") and ObjectId.is_valid(tid(d["target"]))]}}, {"name": 1}).to_list(500)}
    items = []
    for d in docs:
        rtype, t = (d["target"].split(":", 1) + [""])[:2]
        a = users.get(d.get("actor_id") or "", {})
        ref = projs.get(t) if rtype == "project" else (f"{projs.get(ncs[t]['project_id'], '?')} · {ncs[t]['number']}" if rtype == "nc" and t in ncs else (tusers.get(t) if rtype == "user" else None))
        items.append({"seq": d["seq"], "at": d["at"], "event": d["event"], "record_type": rtype, "record_id": t, "reference": ref,
                      "actor_id": d.get("actor_id"), "actor_name": d.get("actor_name") or a.get("name") or ("System" if not d.get("actor_id") else "Unknown"),
                      "actor_role": d.get("actor_role") or a.get("role") or ("system" if not d.get("actor_id") else None),
                      "data": d.get("data", {}), "hash": d["hash"]})
    return {"items": items, "total": total, "page": page, "has_more": page * limit < total}


# ---------------- work tracker ----------------
def _range(rng: str, anchor: Optional[str], frm: Optional[str], to: Optional[str]):
    try:
        today = date.fromisoformat(anchor) if anchor else datetime.now(timezone.utc).date()
        if rng == "week":
            start = today - timedelta(days=today.weekday())
            return start, start + timedelta(days=7)
        if rng == "month":
            start = today.replace(day=1)
            return start, (start + timedelta(days=32)).replace(day=1)
        if rng == "custom":
            s, e = date.fromisoformat(frm or ""), date.fromisoformat(to or "")
            if e < s:
                raise HTTPException(400, "End date is before start date")
            return s, e + timedelta(days=1)
    except ValueError:
        raise HTTPException(400, "Invalid date (use YYYY-MM-DD)")
    if rng == "all":
        return None, None
    raise HTTPException(400, "range must be week, month, custom or all")


def _days(a: Optional[str], b: Optional[str]) -> Optional[int]:
    if not a:
        return None
    end = datetime.fromisoformat(b) if b else datetime.now(timezone.utc)
    return max(0, (end - datetime.fromisoformat(a)).days)


async def tracker_rows(view: str, rng: str, anchor, frm, to, search: str, sort: str):
    if view not in ("completed", "resolved"):
        raise HTTPException(400, "view must be completed or resolved")
    start, end = _range(rng, anchor, frm, to)
    # One row per project, keyed on its FIRST recorded date, so reopened/re-completed projects are never double counted.
    if view == "completed":
        q: dict = {"deleted": None, "completed_at": {"$gte": start.isoformat(), "$lt": end.isoformat()} if start else {"$nin": [None, ""]}}
    else:
        q = {"deleted": None, "resolution_history.0": {"$exists": True}}
    docs = await db.projects.find(q).to_list(5000)
    if view == "resolved" and start:
        docs = [d for d in docs if start.isoformat() <= d["resolution_history"][0]["at"] < end.isoformat()]
    pids = [str(d["_id"]) for d in docs]
    per: dict = {}
    for n in await db.ncs.find({"project_id": {"$in": pids}, "assigned_installer_id": {"$ne": None}}, {"project_id": 1, "assigned_installer_id": 1}).to_list(20000):
        per.setdefault(n["project_id"], set()).add(n["assigned_installer_id"])
    names = {str(u["_id"]): u["name"] for u in await db.users.find({"_id": {"$in": [ObjectId(i) for s in per.values() for i in s]}}, {"name": 1}).to_list(1000)}
    rows = []
    for d in docs:
        pid = str(d["_id"])
        hist = d.get("resolution_history") or []
        resolved_at = (d.get("resolved") or {}).get("at") or (hist[-1]["at"] if hist else None)
        first_resolved = hist[0]["at"] if hist else None
        rows.append({
            "id": pid, "ref": d["ref"], "address": d["address"] + (f", {d['postcode']}" if d.get("postcode") else ""),
            "installers": sorted(names[i] for i in per.get(pid, set()) if i in names),
            "nc_total": d.get("nc_total", 0), "nc_done": d.get("nc_done", 0),
            "completed_at": d.get("completed_at"), "resolved_at": resolved_at, "first_resolved_at": first_resolved,
            "status": d.get("status"), "created_at": d.get("created_at"),
            "reopened": len(d.get("completion_history") or []) > 1 or d.get("status") not in ("completed", "trustmark_resolved"),
            # open from creation until TrustMark resolution (still counting if not resolved yet)
            "days_outstanding": _days(d.get("created_at"), resolved_at if d.get("resolved") else None),
        })
    s = search.lower().strip()
    if s:
        rows = [r for r in rows if s in f"{r['ref']} {r['address']} {' '.join(r['installers'])}".lower()]
    key = "completed_at" if view == "completed" else "first_resolved_at"
    if sort == "oldest":
        rows.sort(key=lambda r: r[key] or "")
    elif sort == "outstanding":
        rows.sort(key=lambda r: r["days_outstanding"] or 0, reverse=True)
    else:
        rows.sort(key=lambda r: r[key] or "", reverse=True)
    return rows, start, end


@router.get("/tracker")
async def tracker(view: str = "completed", range: str = "week", anchor: Optional[str] = None, start: Optional[str] = None,
                  end: Optional[str] = None, search: str = "", sort: str = "newest", u: User = Depends(staff)):
    rows, s, e = await tracker_rows(view, range, anchor, start, end, search, sort)
    return {"items": rows, "count": len(rows), "start": s.isoformat() if s else None, "end": (e - timedelta(days=1)).isoformat() if e else None}


def _canon(params: dict) -> str:
    return urlencode(sorted((k, v) for k, v in params.items() if v not in (None, "")))


@router.get("/tracker/export-link")
async def tracker_export_link(fmt: str = "csv", view: str = "completed", range: str = "week", anchor: Optional[str] = None,
                              start: Optional[str] = None, end: Optional[str] = None, search: str = "", sort: str = "newest",
                              u: User = Depends(staff)):
    if fmt not in ("csv", "xlsx"):
        raise HTTPException(400, "fmt must be csv or xlsx")
    qs = _canon({"fmt": fmt, "view": view, "range": range, "anchor": anchor, "start": start, "end": end, "search": search, "sort": sort})
    await tracker_rows(view, range, anchor, start, end, search, sort)  # validate params now
    return {"url": f"/api/tracker/export?{qs}&{signed_query('tracker:' + qs, 1)}"}


HEAD = ["Project ref", "Address", "Installer(s)", "Total NCs", "Approved NCs", "Completed", "TrustMark resolved", "Status", "Reopened", "Days outstanding"]


def _d(v):
    return v[:10] if v else ""


@router.get("/tracker/export")
async def tracker_export(exp: int, sig: str, fmt: str = "csv", view: str = "completed", range: str = "week", anchor: Optional[str] = None,
                         start: Optional[str] = None, end: Optional[str] = None, search: str = "", sort: str = "newest"):
    qs = _canon({"fmt": fmt, "view": view, "range": range, "anchor": anchor, "start": start, "end": end, "search": search, "sort": sort})
    if not verify_signed("tracker:" + qs, exp, sig):
        raise HTTPException(403, "Link expired or invalid")
    rows, _, _ = await tracker_rows(view, range, anchor, start, end, search, sort)
    data = [[r["ref"], r["address"], ", ".join(r["installers"]), r["nc_total"], r["nc_done"], _d(r["completed_at"]), _d(r["resolved_at"]),
             (r["status"] or "").replace("_", " "), "Yes" if r["reopened"] else "", r["days_outstanding"]] for r in rows]
    name = f"work-tracker-{view}-{datetime.now(timezone.utc).date().isoformat()}"
    if fmt == "xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Internally completed" if view == "completed" else "TrustMark resolved"
        ws.append(HEAD)
        for row in data:
            ws.append(row)
        for col, w in zip("ABCDEFGHIJ", (14, 48, 28, 10, 12, 12, 16, 18, 10, 16)):
            ws.column_dimensions[col].width = w
        buf = io.BytesIO()
        wb.save(buf)
        return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEAD)
    w.writerows(data)
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
