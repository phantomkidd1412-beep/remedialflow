"""P0 (security) + P1 (lifecycle/dashboard) upgrade regression tests.

Covers the review request: /app/memory/UPGRADE_PLAN.md P0 + P1.
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = BASE_URL + "/api"

ADMIN = ("admin@remedialflow.dev", "AdminDemo!2026")
EMPLOYEE = ("employee@remedialflow.dev", "EmployeeDemo!2026")
INSTALLER = ("installer@remedialflow.dev", "InstallerDemo!2026")
INSTALLER2 = ("installer2@remedialflow.dev", "InstallerDemo!2026")

ALLOWED_ORIGIN = "https://field-evidence-app-1.preview.emergentagent.com"
EVIL_ORIGIN = "https://evil.example.com"


def _login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=15)
    assert r.status_code == 200, f"login {email} -> {r.status_code} {r.text}"
    return r.json()["access_token"]


def _bearer(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(scope="session")
def admin_token():
    return _login(*ADMIN)


@pytest.fixture(scope="session")
def employee_token():
    return _login(*EMPLOYEE)


@pytest.fixture(scope="session")
def installer_token():
    return _login(*INSTALLER)


@pytest.fixture(scope="session")
def installer2_token():
    return _login(*INSTALLER2)


# ============================================================================
# P0 - Security
# ============================================================================
class TestP0Security:
    def test_forgot_no_dev_reset_code(self):
        r = requests.post(f"{API}/auth/forgot", json={"email": EMPLOYEE[0]}, timeout=10)
        assert r.status_code == 200
        j = r.json()
        assert "dev_reset_code" not in j, f"dev_reset_code leaked: {j}"

    def test_all_demo_logins_still_work(self):
        for email, pw in [ADMIN, EMPLOYEE, INSTALLER, INSTALLER2]:
            r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=10)
            assert r.status_code == 200, f"{email}: {r.text}"

    def test_cors_allows_known_origin(self):
        r = requests.options(f"{API}/auth/login",
                             headers={"Origin": ALLOWED_ORIGIN,
                                      "Access-Control-Request-Method": "POST",
                                      "Access-Control-Request-Headers": "content-type"},
                             timeout=10)
        aco = r.headers.get("access-control-allow-origin", "")
        assert aco == ALLOWED_ORIGIN, f"ACAO={aco!r}"

    def test_cors_blocks_unknown_origin(self):
        r = requests.options(f"{API}/auth/login",
                             headers={"Origin": EVIL_ORIGIN,
                                      "Access-Control-Request-Method": "POST",
                                      "Access-Control-Request-Headers": "content-type"},
                             timeout=10)
        aco = r.headers.get("access-control-allow-origin", "")
        assert aco == "", f"evil origin got ACAO={aco!r}"

    def test_settings_installer_forbidden(self, installer_token):
        r = requests.get(f"{API}/settings", headers=_bearer(installer_token), timeout=10)
        assert r.status_code == 403, f"installer settings -> {r.status_code}"

    def test_settings_employee_ok(self, employee_token):
        r = requests.get(f"{API}/settings", headers=_bearer(employee_token), timeout=10)
        assert r.status_code == 200
        assert "default_job_rate" in r.json()


# ============================================================================
# Migration / data preservation
# ============================================================================
class TestMigrationPreservation:
    def test_projects_preserved(self, employee_token):
        r = requests.get(f"{API}/projects", params={"limit": 100},
                         headers=_bearer(employee_token), timeout=15)
        assert r.status_code == 200
        j = r.json()
        assert j["total"] >= 12, f"expected >=12 projects, got {j['total']}"
        # Only valid lifecycle statuses
        valid = {"unassigned", "in_progress", "completed", "trustmark_resolved"}
        for p in j["items"]:
            assert p["status"] in valid, f"invalid status {p['status']} on {p['ref']}"

    def test_no_legacy_nc_statuses(self, employee_token):
        # Fetch every project detail and scan NC statuses
        r = requests.get(f"{API}/projects", params={"limit": 100},
                         headers=_bearer(employee_token), timeout=15)
        legacy = []
        bad_fields_missing = []
        total_ncs = 0
        for p in r.json()["items"]:
            pd = requests.get(f"{API}/projects/{p['id']}",
                              headers=_bearer(employee_token), timeout=10).json()
            for n in pd["ncs"]:
                total_ncs += 1
                if n["status"] in ("ai_verified", "manual_review"):
                    legacy.append((p["ref"], n.get("number"), n["status"]))
                if "original_text" not in n:
                    bad_fields_missing.append((p["ref"], n.get("number")))
        assert not legacy, f"legacy statuses found: {legacy}"
        assert not bad_fields_missing, f"NCs missing original_text: {bad_fields_missing[:5]}"
        assert total_ncs >= 45, f"expected >=45 NCs, got {total_ncs}"

    def test_assigned_installer_inherited(self, employee_token, installer_token):
        # EF-1024 is Ian's project — all its NCs must have assigned_installer_id matching
        r = requests.get(f"{API}/projects", params={"search": "EF-1024"},
                         headers=_bearer(installer_token), timeout=10).json()
        pid = r["items"][0]["id"]
        installer_id = r["items"][0]["installer_id"]
        pd = requests.get(f"{API}/projects/{pid}",
                          headers=_bearer(employee_token), timeout=10).json()
        for n in pd["ncs"]:
            if any(h.get("from") for h in n.get("assignment_history", [])):
                continue  # individually reassigned later (P2) - inheritance no longer applies
            assert n.get("assigned_installer_id") == installer_id, \
                f"NC {n['number']} assigned_installer_id={n.get('assigned_installer_id')} vs project {installer_id}"


# ============================================================================
# P1 - Lifecycle
# ============================================================================
class TestP1Lifecycle:
    _state = {}

    def test_create_without_installer_is_unassigned(self, employee_token):
        d0 = requests.get(f"{API}/dashboard", headers=_bearer(employee_token), timeout=10).json()
        ref = f"TEST-P1-{uuid.uuid4().hex[:6].upper()}"
        body = {"ref": ref, "address": "1 Test Street, Testville", "postcode": "TE1 1ST",
                "ncs": [{"category": "insulation", "description": "TEST NC alpha original wording",
                         "required_work": "Install additional insulation per spec"},
                        {"category": "insulation", "description": "TEST NC beta original wording",
                         "required_work": "Seal around penetrations"}]}
        r = requests.post(f"{API}/projects", json=body,
                          headers=_bearer(employee_token), timeout=15)
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        pd = requests.get(f"{API}/projects/{pid}",
                          headers=_bearer(employee_token), timeout=10).json()
        assert pd["project"]["status"] == "unassigned"
        for n in pd["ncs"]:
            assert n.get("assigned_installer_id") in (None, "")

        d1 = requests.get(f"{API}/dashboard", headers=_bearer(employee_token), timeout=10).json()
        assert d1["unassigned"] == d0["unassigned"] + 1, f"unassigned {d0['unassigned']} -> {d1['unassigned']}"
        TestP1Lifecycle._state["pid"] = pid
        TestP1Lifecycle._state["ref"] = ref
        TestP1Lifecycle._state["d_before"] = d1

    def test_patch_installer_assigns_ncs(self, employee_token, admin_token, installer2_token):
        pid = TestP1Lifecycle._state["pid"]
        # Find installer2 id
        users = requests.get(f"{API}/admin/users", headers=_bearer(admin_token), timeout=10).json()
        inst2 = next(u for u in users if u["email"] == INSTALLER2[0])
        inst2_id = inst2["id"]
        r = requests.patch(f"{API}/projects/{pid}", json={"installer_id": inst2_id},
                           headers=_bearer(employee_token), timeout=10)
        assert r.status_code == 200
        pd = requests.get(f"{API}/projects/{pid}",
                          headers=_bearer(employee_token), timeout=10).json()
        assert pd["project"]["status"] == "in_progress"
        for n in pd["ncs"]:
            assert n["assigned_installer_id"] == inst2_id
            assert isinstance(n.get("assignment_history"), list) and len(n["assignment_history"]) >= 1
        TestP1Lifecycle._state["inst2_id"] = inst2_id

    def test_approve_all_marks_completed(self, employee_token):
        pid = TestP1Lifecycle._state["pid"]
        pd = requests.get(f"{API}/projects/{pid}",
                          headers=_bearer(employee_token), timeout=10).json()
        for n in pd["ncs"]:
            rr = requests.post(f"{API}/ncs/{n['id']}/review",
                               json={"action": "approve", "note": "TEST approve"},
                               headers=_bearer(employee_token), timeout=10)
            assert rr.status_code == 200, rr.text
        pd2 = requests.get(f"{API}/projects/{pid}",
                           headers=_bearer(employee_token), timeout=10).json()
        assert pd2["project"]["status"] == "completed"
        assert pd2["project"].get("completed_at"), "completed_at missing"
        for n in pd2["ncs"]:
            assert n["status"] == "approved"
            assert n.get("approved_by") and n.get("approved_at")

    def test_resolve_requires_staff(self, installer2_token):
        pid = TestP1Lifecycle._state["pid"]
        r = requests.post(f"{API}/projects/{pid}/resolve",
                          headers=_bearer(installer2_token), timeout=10)
        assert r.status_code == 403

    def test_resolve_marks_trustmark_resolved(self, employee_token):
        pid = TestP1Lifecycle._state["pid"]
        d0 = requests.get(f"{API}/dashboard", headers=_bearer(employee_token), timeout=10).json()
        r = requests.post(f"{API}/projects/{pid}/resolve",
                          headers=_bearer(employee_token), timeout=10)
        assert r.status_code == 200, r.text
        pd = requests.get(f"{API}/projects/{pid}",
                          headers=_bearer(employee_token), timeout=10).json()
        assert pd["project"]["status"] == "trustmark_resolved"
        assert pd["project"].get("resolved", {}).get("at")
        d1 = requests.get(f"{API}/dashboard", headers=_bearer(employee_token), timeout=10).json()
        assert d1["trustmark_resolved"] == d0["trustmark_resolved"] + 1

    def test_resolve_non_completed_400(self, employee_token):
        # Pick a project with lifecycle != completed
        projs = requests.get(f"{API}/projects", params={"limit": 100},
                             headers=_bearer(employee_token), timeout=10).json()["items"]
        target = next((p for p in projs if p["status"] in ("unassigned", "in_progress")), None)
        if not target:
            pytest.skip("no non-completed project to test")
        r = requests.post(f"{API}/projects/{target['id']}/resolve",
                          headers=_bearer(employee_token), timeout=10)
        assert r.status_code == 400

    def test_reject_nc_reopens_project(self, employee_token):
        pid = TestP1Lifecycle._state["pid"]
        pd = requests.get(f"{API}/projects/{pid}",
                          headers=_bearer(employee_token), timeout=10).json()
        nc_id = pd["ncs"][0]["id"]
        r = requests.post(f"{API}/ncs/{nc_id}/review",
                          json={"action": "reject", "note": "TEST reopen"},
                          headers=_bearer(employee_token), timeout=10)
        assert r.status_code == 200, r.text
        pd2 = requests.get(f"{API}/projects/{pid}",
                           headers=_bearer(employee_token), timeout=10).json()
        # Project should no longer be trustmark_resolved; resolved cleared; history kept
        assert pd2["project"]["status"] != "trustmark_resolved", f"status={pd2['project']['status']}"
        assert pd2["project"].get("resolved") in (None, {}), f"resolved not cleared: {pd2['project'].get('resolved')}"
        rh = pd2["project"].get("resolution_history") or []
        assert len(rh) >= 1, "resolution_history should be preserved"


# ============================================================================
# Counter consistency
# ============================================================================
class TestCounterConsistency:
    def test_lifecycle_counters_sum_to_total(self, employee_token):
        d = requests.get(f"{API}/dashboard", headers=_bearer(employee_token), timeout=10).json()
        total_lifecycle = d["unassigned"] + d["in_progress"] + d["completed"] + d["trustmark_resolved"]
        projs = requests.get(f"{API}/projects", params={"limit": 1},
                             headers=_bearer(employee_token), timeout=10).json()
        assert total_lifecycle == projs["total"], \
            f"sum lifecycle {total_lifecycle} != projects total {projs['total']} dashboard={d}"

    def test_projects_filter_by_lifecycle(self, employee_token):
        for s in ("unassigned", "in_progress", "completed", "trustmark_resolved", "awaiting_review"):
            r = requests.get(f"{API}/projects", params={"status": s, "limit": 100},
                             headers=_bearer(employee_token), timeout=10)
            assert r.status_code == 200, f"status={s}: {r.text}"
            items = r.json()["items"]
            if s == "awaiting_review":
                for p in items:
                    assert p.get("needs_review") is True
            else:
                for p in items:
                    assert p["status"] == s

    def test_projects_items_include_installer_names_and_needs_review(self, employee_token):
        r = requests.get(f"{API}/projects", params={"limit": 5},
                         headers=_bearer(employee_token), timeout=10).json()
        for p in r["items"]:
            assert "installer_names" in p and isinstance(p["installer_names"], list)
            assert "needs_review" in p
