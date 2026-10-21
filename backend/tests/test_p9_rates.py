"""Phase 9 - Admin per-project rate override + Earnings pay types.

Covers PUT /api/projects/{pid}/rates/{iid}, GET /api/projects/{pid}.rate_rows,
GET /api/admin/payments.jobs[].overridden flag, GET /api/earnings for installer.

Hits the public preview URL from EXPO_PUBLIC_BACKEND_URL. Uses TEST- prefixed
projects; the real P419977 and EF-1024 are never mutated.
"""
import os
import uuid

import pytest
import requests


def _read_env(path: str, key: str) -> str:
    try:
        for line in open(path):
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ""


BASE_URL = (_read_env("/app/frontend/.env", "EXPO_PUBLIC_BACKEND_URL")
            or os.environ.get("EXPO_PUBLIC_BACKEND_URL", "")).rstrip("/")
API = f"{BASE_URL}/api"

CREDS = {
    "admin": ("admin@remedialflow.dev", "AdminDemo!2026"),
    "employee": ("employee@remedialflow.dev", "EmployeeDemo!2026"),
    "installer_a": ("installer@remedialflow.dev", "InstallerDemo!2026"),
    "installer_b": ("installer2@remedialflow.dev", "InstallerDemo!2026"),
}


def _login(role: str) -> str:
    email, pw = CREDS[role]
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=20)
    assert r.status_code == 200, f"login {role} -> {r.status_code} {r.text[:200]}"
    return r.json()["access_token"]


def _hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


STATE: dict = {}


@pytest.fixture(scope="module")
def tokens():
    assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL not set"
    return {k: _login(k) for k in CREDS}


@pytest.fixture(scope="module")
def ids(tokens):
    r = requests.get(f"{API}/installers", headers=_hdr(tokens["admin"]), timeout=15)
    assert r.status_code == 200
    installers = {d["email"]: d["id"] for d in r.json()}
    return {
        "A": installers["installer@remedialflow.dev"],
        "B": installers["installer2@remedialflow.dev"],
    }


def _create_project(tokens, ids_, installer="A", ref=None, ncs=None):
    ref = ref or f"TEST-P9-{uuid.uuid4().hex[:6].upper()}"
    ncs = ncs or [
        {"description": "Loft insulation gaps near hatch", "required_work": "Top up loft insulation", "safety_critical": False},
        {"description": "External wall vent partially blocked", "required_work": "Clear vent", "safety_critical": False},
    ]
    r = requests.post(f"{API}/projects", headers=_hdr(tokens["admin"]), json={
        "ref": ref, "address": f"{ref} Rates Rd", "postcode": "TE9 1EN",
        "installer_id": ids_[installer], "ncs": ncs}, timeout=25)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    d = requests.get(f"{API}/projects/{pid}", headers=_hdr(tokens["admin"]), timeout=15).json()
    return pid, ref, [n["id"] for n in d["ncs"]]


# =====================================================================
# 1) rate_rows exposure on GET /projects/{pid}
# =====================================================================
class TestRateRowsVisibility:
    def test_01_set_installer_a_rate_150_per_job(self, tokens, ids):
        r = requests.patch(f"{API}/admin/users/{ids['A']}", headers=_hdr(tokens["admin"]),
                           json={"default_rate": 150, "pay_type": "per_job"}, timeout=15)
        assert r.status_code == 200

    def test_02_staff_sees_rate_rows_installer_hidden(self, tokens, ids):
        pid, ref, _ncs = _create_project(tokens, ids, installer="A")
        STATE["pid"] = pid
        STATE["ref"] = ref
        # admin
        d = requests.get(f"{API}/projects/{pid}", headers=_hdr(tokens["admin"]), timeout=15).json()
        assert "rate_rows" in d["project"], "admin must get rate_rows"
        rows = d["project"]["rate_rows"]
        assert len(rows) == 1, rows
        row = rows[0]
        assert row["installer_id"] == ids["A"]
        assert row["pay_type"] == "per_job"
        assert row["rate"] == 150
        assert row["earning"] is None
        # employee: must NOT see rate_rows / rates (iter22 — payment info is Admin-only)
        d = requests.get(f"{API}/projects/{pid}", headers=_hdr(tokens["employee"]), timeout=15).json()
        assert "rate_rows" not in d["project"], "employee must NOT see rate_rows"
        assert "rates" not in d["project"] or not d["project"].get("rates"), "employee must NOT see rates"
        # installer
        d = requests.get(f"{API}/projects/{pid}", headers=_hdr(tokens["installer_a"]), timeout=15).json()
        assert "rate_rows" not in d["project"], "installer must NOT see rate_rows"
        assert "rate_history" not in d["project"], "installer must NOT see rate_history"
        rates = d["project"].get("rates") or {}
        assert set(rates.keys()) <= {ids["A"]}, f"installer should see only own id in rates, got {rates}"


# =====================================================================
# 2) Rate override happy path + audit + history + notification
# =====================================================================
class TestRateOverride:
    def test_01_admin_override_rate_200_without_reason(self, tokens, ids):
        pid = STATE["pid"]
        r = requests.put(f"{API}/projects/{pid}/rates/{ids['A']}", headers=_hdr(tokens["admin"]),
                         json={"rate": 200}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["rate"] == 200
        assert body["earning_updated"] is False  # no earning yet

    def test_02_rate_row_reflects_new_rate(self, tokens, ids):
        d = requests.get(f"{API}/projects/{STATE['pid']}", headers=_hdr(tokens["admin"]), timeout=15).json()
        rows = d["project"]["rate_rows"]
        assert rows[0]["rate"] == 200
        hist = d["project"].get("rate_history") or []
        assert hist and hist[-1]["new"] == 200 and hist[-1]["old"] == 150
        assert hist[-1]["reason"] in (None, "")

    def test_03_override_with_reason_persists(self, tokens, ids):
        r = requests.put(f"{API}/projects/{STATE['pid']}/rates/{ids['A']}", headers=_hdr(tokens["admin"]),
                         json={"rate": 225, "reason": "Extra scaffolding required"}, timeout=15)
        assert r.status_code == 200
        d = requests.get(f"{API}/projects/{STATE['pid']}", headers=_hdr(tokens["admin"]), timeout=15).json()
        hist = d["project"]["rate_history"]
        assert hist[-1]["new"] == 225 and hist[-1]["old"] == 200
        assert hist[-1]["reason"] == "Extra scaffolding required"
        assert hist[-1]["by"] and hist[-1]["at"]

    def test_04_installer_got_rate_changed_notification(self, tokens):
        r = requests.get(f"{API}/notifications", headers=_hdr(tokens["installer_a"]), timeout=15)
        assert r.status_code == 200
        hits = [i for i in r.json()["items"] if i["project_id"] == STATE["pid"] and i["kind"] == "rate_changed"]
        assert hits, "installer must get rate_changed notification"
        assert "225" in hits[0]["body"] or STATE["ref"] in hits[0]["body"]

    def test_05_audit_contains_project_rate_overridden(self, tokens):
        r = requests.get(f"{API}/projects/{STATE['pid']}/audit", headers=_hdr(tokens["admin"]), timeout=15)
        assert r.status_code == 200
        events = [a["event"] for a in r.json()]
        assert "project.rate_overridden" in events


# =====================================================================
# 3) Permission + validation errors
# =====================================================================
class TestRateOverrideErrors:
    def test_01_employee_forbidden(self, tokens, ids):
        r = requests.put(f"{API}/projects/{STATE['pid']}/rates/{ids['A']}", headers=_hdr(tokens["employee"]),
                         json={"rate": 300}, timeout=15)
        assert r.status_code == 403, r.status_code

    def test_02_installer_forbidden(self, tokens, ids):
        r = requests.put(f"{API}/projects/{STATE['pid']}/rates/{ids['A']}", headers=_hdr(tokens["installer_a"]),
                         json={"rate": 300}, timeout=15)
        assert r.status_code == 403

    def test_03_negative_rate_422(self, tokens, ids):
        r = requests.put(f"{API}/projects/{STATE['pid']}/rates/{ids['A']}", headers=_hdr(tokens["admin"]),
                         json={"rate": -1}, timeout=15)
        assert r.status_code == 422, r.status_code

    def test_04_unknown_installer_404(self, tokens):
        bogus = "6553" + "0" * 20  # 24-char hex ObjectId that won't exist
        r = requests.put(f"{API}/projects/{STATE['pid']}/rates/{bogus}", headers=_hdr(tokens["admin"]),
                         json={"rate": 300}, timeout=15)
        assert r.status_code == 404, r.status_code

    def test_05_not_assigned_installer_400(self, tokens, ids):
        # installer B is not on this project
        r = requests.put(f"{API}/projects/{STATE['pid']}/rates/{ids['B']}", headers=_hdr(tokens["admin"]),
                         json={"rate": 300}, timeout=15)
        assert r.status_code == 400, r.status_code

    def test_06_daily_wage_installer_400(self, tokens, ids):
        # Flip A to daily -> override must 400 -> restore after
        try:
            r = requests.patch(f"{API}/admin/users/{ids['A']}", headers=_hdr(tokens["admin"]),
                               json={"pay_type": "daily"}, timeout=15)
            assert r.status_code == 200
            r = requests.put(f"{API}/projects/{STATE['pid']}/rates/{ids['A']}", headers=_hdr(tokens["admin"]),
                             json={"rate": 300}, timeout=15)
            assert r.status_code == 400, r.text
        finally:
            r = requests.patch(f"{API}/admin/users/{ids['A']}", headers=_hdr(tokens["admin"]),
                               json={"pay_type": "per_job"}, timeout=15)
            assert r.status_code == 200


# =====================================================================
# 4) New earnings after override use the overridden rate
# =====================================================================
class TestOverrideFlowsIntoEarnings:
    def test_01_new_project_override_then_approve_earns_at_override(self, tokens, ids):
        pid, ref, ncs = _create_project(tokens, ids, installer="A", ref=f"TEST-P9A-{uuid.uuid4().hex[:6].upper()}")
        STATE["flow_pid"] = pid
        STATE["flow_ref"] = ref
        # Override rate BEFORE any approval -> new earning should use this
        r = requests.put(f"{API}/projects/{pid}/rates/{ids['A']}", headers=_hdr(tokens["admin"]),
                         json={"rate": 275, "reason": "Access"}, timeout=15)
        assert r.status_code == 200 and r.json()["earning_updated"] is False
        # Approve all NCs via admin (shortcut path used by other suites)
        for nid in ncs:
            r = requests.post(f"{API}/ncs/{nid}/review", headers=_hdr(tokens["admin"]),
                              json={"action": "approve", "note": ""}, timeout=15)
            assert r.status_code == 200
        # Installer A should now have earning at 275 on this project
        data = requests.get(f"{API}/earnings", headers=_hdr(tokens["installer_a"]), timeout=15).json()
        earned = [i for i in data["items"] if i["project_id"] == pid]
        assert earned, "no earning for A after approving all NCs"
        assert earned[0]["amount"] == 275, earned[0]
        assert earned[0]["status"] == "earned"
        STATE["flow_eid"] = earned[0].get("id")

    def test_02_override_after_earned_updates_amount_and_flag(self, tokens, ids):
        pid = STATE["flow_pid"]
        r = requests.put(f"{API}/projects/{pid}/rates/{ids['A']}", headers=_hdr(tokens["admin"]),
                         json={"rate": 320}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rate"] == 320
        assert body["earning_updated"] is True
        # Earning should now reflect 320 + overridden flag
        data = requests.get(f"{API}/earnings", headers=_hdr(tokens["installer_a"]), timeout=15).json()
        earned = [i for i in data["items"] if i["project_id"] == pid]
        assert earned and earned[0]["amount"] == 320
        assert earned[0].get("overridden") is True
        hist = earned[0].get("amount_history") or []
        assert hist and hist[-1]["new"] == 320 and hist[-1]["old"] == 275

    def test_03_paid_rate_change_returns_409(self, tokens, ids):
        pid = STATE["flow_pid"]
        # Pay this installer's week
        data = requests.get(f"{API}/earnings", headers=_hdr(tokens["installer_a"]), timeout=15).json()
        earned = [i for i in data["items"] if i["project_id"] == pid][0]
        week = earned["week"]
        r = requests.post(f"{API}/admin/payments/mark-paid", headers=_hdr(tokens["admin"]),
                          json={"installer_id": ids["A"], "week": week}, timeout=15)
        assert r.status_code == 200
        # Now override must 409
        r = requests.put(f"{API}/projects/{pid}/rates/{ids['A']}", headers=_hdr(tokens["admin"]),
                         json={"rate": 500}, timeout=15)
        assert r.status_code == 409, r.text
        # amount unchanged
        data = requests.get(f"{API}/earnings", headers=_hdr(tokens["installer_a"]), timeout=15).json()
        earned = [i for i in data["items"] if i["project_id"] == pid][0]
        assert earned["amount"] == 320 and earned["status"] == "paid"


# =====================================================================
# 5) Two installers on same project have independent rates/earnings
# =====================================================================
class TestTwoInstallersIndependent:
    def test_01_two_installers_shared_project_independent_rates(self, tokens, ids):
        pid, ref, ncs = _create_project(tokens, ids, installer="A", ref=f"TEST-P9DUAL-{uuid.uuid4().hex[:6].upper()}", ncs=[
            {"description": "Loft insulation gaps near hatch", "required_work": "Top up loft insulation", "safety_critical": False},
            {"description": "External vent blocked", "required_work": "Clear vent", "safety_critical": False},
            {"description": "Pipe lagging missing", "required_work": "Lag pipes", "safety_critical": False},
        ])
        # Reassign NC-03 to B (B must be per_job for earning)
        r = requests.patch(f"{API}/admin/users/{ids['B']}", headers=_hdr(tokens["admin"]),
                           json={"pay_type": "per_job", "default_rate": 180}, timeout=15)
        assert r.status_code == 200
        r = requests.post(f"{API}/ncs/{ncs[2]}/assign", headers=_hdr(tokens["admin"]),
                          json={"installer_id": ids["B"]}, timeout=15)
        assert r.status_code == 200
        # Override A to 400, B to 220
        for iid, rate in ((ids["A"], 400), (ids["B"], 220)):
            r = requests.put(f"{API}/projects/{pid}/rates/{iid}", headers=_hdr(tokens["admin"]),
                             json={"rate": rate}, timeout=15)
            assert r.status_code == 200, r.text
        # Approve all NCs
        for nid in ncs:
            r = requests.post(f"{API}/ncs/{nid}/review", headers=_hdr(tokens["admin"]),
                              json={"action": "approve", "note": ""}, timeout=15)
            assert r.status_code == 200
        # Each installer should have exactly one earning on this project, at own rate
        for tok, iid, expect in (("installer_a", ids["A"], 400), ("installer_b", ids["B"], 220)):
            data = requests.get(f"{API}/earnings", headers=_hdr(tokens[tok]), timeout=15).json()
            own = [i for i in data["items"] if i["project_id"] == pid]
            assert len(own) == 1, f"{tok} should have exactly 1 earning on project, got {own}"
            assert own[0]["installer_id"] == iid
            assert own[0]["amount"] == expect
        # rate_rows must contain both independently
        d = requests.get(f"{API}/projects/{pid}", headers=_hdr(tokens["admin"]), timeout=15).json()
        rows = {r["installer_id"]: r for r in d["project"]["rate_rows"]}
        assert rows[ids["A"]]["rate"] == 400
        assert rows[ids["B"]]["rate"] == 220
        assert rows[ids["A"]]["earning"]["amount"] == 400
        assert rows[ids["B"]]["earning"]["amount"] == 220


# =====================================================================
# 6) Admin payments overridden flag + daily skip
# =====================================================================
class TestAdminPaymentsOverriddenFlag:
    def test_01_admin_payments_jobs_contain_overridden_true(self, tokens, ids):
        pid = STATE["flow_pid"]  # paid earning at 320, previously overridden
        r = requests.get(f"{API}/admin/payments", headers=_hdr(tokens["admin"]), timeout=15)
        assert r.status_code == 200
        groups = r.json()
        # Find any group whose jobs include the flow_ref with overridden=True
        hits = []
        for g in groups:
            for j in g["jobs"]:
                if j["ref"] == STATE["flow_ref"]:
                    hits.append(j)
        assert hits, f"flow_ref {STATE['flow_ref']} missing from /admin/payments"
        assert any(j.get("overridden") is True for j in hits), f"no overridden=True on jobs: {hits}"


# =====================================================================
# 99) Restore demo installer rate (user already restored A to 150 externally
#     per iteration notes; keep this to belt-and-braces)
# =====================================================================
class TestRestoreDemoInstaller:
    def test_99_restore_a_default_rate_150(self, tokens, ids):
        r = requests.patch(f"{API}/admin/users/{ids['A']}", headers=_hdr(tokens["admin"]),
                           json={"default_rate": 150, "pay_type": "per_job"}, timeout=15)
        assert r.status_code == 200
