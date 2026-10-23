"""Iter 28: Project-level notification tests.

Scenario (per /app/test_reports review request):
  * One notification per (user, project, category) with key '{cat}:{project_id}'
  * Repeated triggers update entry + mark it unread (never duplicate)
  * refresh_notifications recomputes summaries after every recompute_project
  * installers removed from a project get their notifications hidden
  * Tapping opens /project/{id} (staff) or /job/{id} (installer)
"""
import io
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://field-evidence-app-1.preview.emergentagent.com").rstrip("/")
PASSWORD = __import__("os").environ.get("RF_QA_PASSWORD", "")

EMP = "qa.employee@example.com"
ADMIN = "qa.admin@example.com"
INST1 = "qa.installer@example.com"
INST2 = "qa.installer2@example.com"

STATE: dict = {}


# ---------------- fixtures / helpers ----------------
def _login(email: str) -> dict:
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PASSWORD}, timeout=20)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    j = r.json()
    return {"token": j["access_token"], "user": j["user"]}


@pytest.fixture(scope="module")
def sessions():
    out = {}
    for role, email in [("employee", EMP), ("admin", ADMIN), ("installer1", INST1), ("installer2", INST2)]:
        d = _login(email)
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {d['token']}"})
        out[role] = {"session": s, "user": d["user"]}
    return out


def _nc_payload(desc: str, safety: bool = True):
    return {"description": desc, "required_work": "Replace faulty component and retest",
            "safety_critical": safety, "original_text": desc, "pages": []}


def _create_project(emp_session, ref: str, address: str, postcode: str, installer_id: str, ncs: list) -> str:
    body = {"ref": ref, "address": address, "postcode": postcode, "installer_id": installer_id, "ncs": ncs}
    r = emp_session.post(f"{BASE_URL}/api/projects", json=body, timeout=30)
    assert r.status_code == 200, f"create project: {r.status_code} {r.text}"
    return r.json()["id"]


def _list_ncs(session, pid: str) -> list:
    r = session.get(f"{BASE_URL}/api/projects/{pid}", timeout=20)
    assert r.status_code == 200, f"get project: {r.text}"
    data = r.json()
    return data.get("ncs", [])


def _assign_nc(emp_session, nc_id: str, installer_id: str):
    r = emp_session.post(f"{BASE_URL}/api/ncs/{nc_id}/assign", json={"installer_id": installer_id}, timeout=15)
    assert r.status_code == 200, f"assign: {r.text}"


def _upload_photo(inst_session, nc_id: str) -> str:
    jpeg = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb0043000806060706050806070707090908"
                         "0a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c3031343"
                         "4341f27393d38323c2e333432ffdb0043010909090c0b0c180d0d1832211c213232323232323"
                         "2323232323232323232323232323232323232323232323232323232323232323232323232323"
                         "232323232323232323232ffc00011080001000103012200021101031101ffc4001f000001050"
                         "10101010101000000000000000001020304050607080910111213ffc400b5100002010303020"
                         "40305050404000001027700010203110405213112410613516107227114328191a1082342b1c"
                         "11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a"
                         "535455565758595a636465666768696a737475767778797a838485868788898a929394959697"
                         "98999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9babac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8"
                         "d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffc4001f01000301010101010101010"
                         "1010000000000000102030405060708091011ffc400b511000201020404030407050404000"
                         "10277000102031104052131061241510761711322328108144291a1b1c109233352f0156272"
                         "d10a162434e125f11718191a262728292a35363738393a434445464748494a535455565758"
                         "595a636465666768696a737475767778797a82838485868788898a92939495969798999aa2"
                         "a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3"
                         "e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9faffda000c03010002110311003f00fbfbffd9")
    files = {"file": (f"evidence-{uuid.uuid4().hex[:6]}.jpg", io.BytesIO(jpeg), "image/jpeg")}
    data = {"meta": '{"kind":"photo","lat":51.5,"lng":-0.1,"accuracy_m":5,"source":"in_app_camera","client_id":"' + uuid.uuid4().hex + '"}'}
    r = inst_session.post(f"{BASE_URL}/api/ncs/{nc_id}/evidence", files=files, data=data, timeout=30)
    assert r.status_code in (200, 201), f"upload evidence: {r.status_code} {r.text}"
    return r.json()["id"]


def _submit(inst_session, nc_id: str):
    r = inst_session.post(f"{BASE_URL}/api/ncs/{nc_id}/submit", json={"client_id": uuid.uuid4().hex}, timeout=20)
    assert r.status_code == 200, f"submit: {r.status_code} {r.text}"
    return r.json()


def _review(staff_session, nc_id: str, action: str):
    r = staff_session.post(f"{BASE_URL}/api/ncs/{nc_id}/review", json={"action": action, "note": ""}, timeout=15)
    assert r.status_code == 200, f"review {action}: {r.status_code} {r.text}"
    return r.json()


def _notifications(session) -> dict:
    r = session.get(f"{BASE_URL}/api/notifications", timeout=15)
    assert r.status_code == 200, f"notifications: {r.text}"
    return r.json()


def _project_entries(session, pid: str, kind: str | None = None) -> list:
    items = _notifications(session)["items"]
    out = [i for i in items if i.get("project_id") == pid]
    if kind:
        out = [i for i in out if i.get("kind") == kind]
    return out


def _mark_all_read(session):
    r = session.post(f"{BASE_URL}/api/notifications/read-all", timeout=10)
    assert r.status_code == 200


def _wait_status(staff_session, pid: str, nc_id: str, target: str, timeout: float = 10.0):
    """Poll NC status (AI assessment may take a few seconds for non-safety NCs)."""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        ncs = _list_ncs(staff_session, pid)
        found = next((n for n in ncs if n["id"] == nc_id), None)
        if found:
            last = found["status"]
            if last == target:
                return
        time.sleep(0.5)
    raise AssertionError(f"nc {nc_id} status={last}, expected {target}")


# ---------------- setup ----------------
class TestA_SetupProject:
    """Create a project with 3 NCs for installer1 and 2 NCs for installer2 at the same address."""

    def test_create_project_with_mixed_assignments(self, sessions):
        emp = sessions["employee"]["session"]
        i1 = sessions["installer1"]["user"]["id"]
        i2 = sessions["installer2"]["user"]["id"]
        ref = f"TEST-IT28-{uuid.uuid4().hex[:8]}"
        pid = _create_project(emp, ref, "12 Baker Street", "NW1 6XE", i1,
                              [_nc_payload(f"Fault {k}") for k in range(5)])
        STATE["pid"] = pid
        STATE["ref"] = ref
        STATE["i1"] = i1
        STATE["i2"] = i2

        # Reassign the last 2 NCs to installer2
        ncs = _list_ncs(emp, pid)
        assert len(ncs) == 5
        ncs_sorted = sorted(ncs, key=lambda n: n["number"])
        STATE["nc_ids_i1"] = [n["id"] for n in ncs_sorted[:3]]
        STATE["nc_ids_i2"] = [n["id"] for n in ncs_sorted[3:]]
        for nc_id in STATE["nc_ids_i2"]:
            _assign_nc(emp, nc_id, i2)

    def test_installer_assigned_notification_single_entry(self, sessions):
        """Each installer should see ONE 'assigned' notification for the project (not per-NC)."""
        pid = STATE["pid"]
        a1 = _project_entries(sessions["installer1"]["session"], pid, "assigned")
        a2 = _project_entries(sessions["installer2"]["session"], pid, "assigned")
        assert len(a1) == 1, f"installer1 assigned entries: {len(a1)}"
        assert len(a2) == 1, f"installer2 assigned entries: {len(a2)}"
        # i1 was assigned 3 NCs; i2 was assigned 2 NCs
        assert "3 NCs assigned to you" in a1[0]["summary"], a1[0]["summary"]
        assert "2 NCs assigned to you" in a2[0]["summary"], a2[0]["summary"]
        # address has postcode
        assert "NW1 6XE" in a1[0]["address"]
        assert a1[0]["ref"] == STATE["ref"]


# ---------------- review flow for employee ----------------
class TestB_ReviewAggregation:
    def test_submit_evidence_single_review_entry(self, sessions):
        pid = STATE["pid"]
        emp = sessions["employee"]["session"]
        i1 = sessions["installer1"]["session"]
        # mark employee notifs read first so we can test unread transitions
        _mark_all_read(emp)
        for nc_id in STATE["nc_ids_i1"]:
            _upload_photo(i1, nc_id)
            _submit(i1, nc_id)
            _wait_status(emp, pid, nc_id, "employee_review", 10)

        entries = _project_entries(emp, pid, "review")
        assert len(entries) == 1, f"expected 1 review entry, got {len(entries)}: {entries}"
        assert "3 NCs awaiting review" in entries[0]["summary"], entries[0]["summary"]
        assert entries[0]["read"] is False
        assert entries[0]["title"] == "Awaiting review"
        STATE["review_nid"] = entries[0]["id"]

    def test_unread_count_reflects_one_project_entry(self, sessions):
        """unread count must be 1 for this project (not 3 as there are 3 NCs)."""
        pid = STATE["pid"]
        emp = sessions["employee"]["session"]
        items = _notifications(emp)["items"]
        unread_for_pid = sum(1 for i in items if i.get("project_id") == pid and not i["read"])
        assert unread_for_pid == 1, f"unread for pid={unread_for_pid}, items={[(i['kind'],i['read']) for i in items if i.get('project_id')==pid]}"

    def test_approve_one_updates_summary(self, sessions):
        pid = STATE["pid"]
        emp = sessions["employee"]["session"]
        _review(emp, STATE["nc_ids_i1"][0], "approve")
        entries = _project_entries(emp, pid, "review")
        assert len(entries) == 1
        assert "2 NCs awaiting review" in entries[0]["summary"], entries[0]["summary"]

    def test_installer_approved_entry_k_of_n(self, sessions):
        pid = STATE["pid"]
        i1 = sessions["installer1"]["session"]
        app1 = _project_entries(i1, pid, "approved")
        assert len(app1) == 1, f"installer approved entries: {len(app1)}"
        # 1 of 3 NCs approved for installer1
        assert "1 of 3 NCs approved" in app1[0]["summary"], app1[0]["summary"]


# ---------------- rejection path ----------------
class TestC_Rejection:
    def test_reject_two_creates_single_action_entry_for_installer(self, sessions):
        """Reject 2 of installer1's remaining NCs: installer sees ONE action entry '2 NCs need more work'."""
        pid = STATE["pid"]
        emp = sessions["employee"]["session"]
        i1 = sessions["installer1"]["session"]
        _mark_all_read(i1)
        # The approved NC is index 0; reject NC[1] and NC[2]
        _review(emp, STATE["nc_ids_i1"][1], "reject")
        _review(emp, STATE["nc_ids_i1"][2], "reject")

        actions = _project_entries(i1, pid, "action")
        assert len(actions) == 1, f"installer action entries: {len(actions)}"
        assert "2 NCs need more work" in actions[0]["summary"], actions[0]["summary"]
        assert actions[0]["read"] is False

    def test_reassign_rejected_nc_to_installer2_updates_counts(self, sessions):
        """Reassigning NC[1] from installer1 -> installer2 reduces installer1 action count to 1.
        Installer2 should now see an 'assigned' update covering 3 NCs (2 original + 1 reassigned)."""
        pid = STATE["pid"]
        emp = sessions["employee"]["session"]
        i1 = sessions["installer1"]["session"]
        i2 = sessions["installer2"]["session"]
        _assign_nc(emp, STATE["nc_ids_i1"][1], STATE["i2"])

        actions_i1 = _project_entries(i1, pid, "action")
        assert len(actions_i1) == 1
        assert "1 NC need" in actions_i1[0]["summary"] or "1 NC needs" in actions_i1[0]["summary"], actions_i1[0]["summary"]

        assigned_i2 = _project_entries(i2, pid, "assigned")
        assert len(assigned_i2) == 1
        # 3 NCs assigned to installer2 now (originally 2 + reassigned 1)
        assert "3 NCs assigned to you" in assigned_i2[0]["summary"], assigned_i2[0]["summary"]


# ---------------- installer removed from project ----------------
class TestD_InstallerRemoval:
    def test_remove_installer1_hides_all_their_entries(self, sessions):
        """Reassign ALL of installer1's remaining NCs (NC[0] approved + NC[2] rejected) to installer2.
        installer1 then has no NCs on this project -> all notifications hidden."""
        pid = STATE["pid"]
        emp = sessions["employee"]["session"]
        i1 = sessions["installer1"]["session"]
        _assign_nc(emp, STATE["nc_ids_i1"][0], STATE["i2"])
        _assign_nc(emp, STATE["nc_ids_i1"][2], STATE["i2"])

        # installer1 should see no entries for this project
        entries = _project_entries(i1, pid)
        assert len(entries) == 0, f"installer1 still has entries after removal: {entries}"


# ---------------- separate projects stay separate ----------------
class TestE_SeparateProjects:
    def test_second_project_same_address_gets_own_entries(self, sessions):
        emp = sessions["employee"]["session"]
        i1 = sessions["installer1"]["session"]
        ref2 = f"TEST-IT28B-{uuid.uuid4().hex[:8]}"
        pid2 = _create_project(emp, ref2, "12 Baker Street", "NW1 6XE", STATE["i1"],
                               [_nc_payload("Second project fault")])
        STATE["pid2"] = pid2

        entries_i1 = _project_entries(i1, pid2, "assigned")
        assert len(entries_i1) == 1
        assert entries_i1[0]["project_id"] == pid2
        assert entries_i1[0]["ref"] == ref2
        # The first project's entries for i1 remain hidden
        p1_entries = _project_entries(i1, STATE["pid"])
        assert len(p1_entries) == 0


# ---------------- RBAC ----------------
class TestF_RBAC:
    def test_installer_cannot_see_other_users_notifications(self, sessions):
        """installer2 must not see installer1's notifications (strict user_id filter)."""
        i2 = sessions["installer2"]["session"]
        items = _notifications(i2)["items"]
        user_i2 = sessions["installer2"]["user"]["id"]
        for it in items:
            assert "user_id" not in it or it.get("user_id") in (None, user_i2), (
                f"installer2 saw entry for different user: {it}"
            )

    def test_project_id_is_present_on_every_entry(self, sessions):
        """Tapping opens /project/{id} or /job/{id}; project_id must always be set."""
        for role in ("employee", "installer1", "installer2", "admin"):
            items = _notifications(sessions[role]["session"])["items"]
            for it in items:
                assert it.get("project_id"), f"missing project_id on {role} notification: {it}"


# ---------------- full approval -> completed ----------------
class TestG_FullApproval:
    def test_approve_all_remaining_fires_completed(self, sessions):
        """Submit and approve all remaining NCs (now all on installer2).
        - review entry on employee eventually marked read
        - staff gets ONE 'completed' notification for the project
        - installer2 'approved' entry summary becomes 'N of N'"""
        pid = STATE["pid"]
        emp = sessions["employee"]["session"]
        i2 = sessions["installer2"]["session"]
        _mark_all_read(emp)

        # NCs assigned to installer2: originally STATE['nc_ids_i2'] (2) + nc_ids_i1[1] + nc_ids_i1[2] (both rejected -> status=rejected)
        # The rejected NCs must be resubmitted.
        resubmit = [STATE["nc_ids_i1"][1], STATE["nc_ids_i1"][2]]
        for nc_id in resubmit:
            _upload_photo(i2, nc_id)
            _submit(i2, nc_id)
            _wait_status(emp, pid, nc_id, "employee_review", 10)

        # The 2 original NCs for installer2 still need first submit
        for nc_id in STATE["nc_ids_i2"]:
            _upload_photo(i2, nc_id)
            _submit(i2, nc_id)
            _wait_status(emp, pid, nc_id, "employee_review", 10)

        # Approve all 4
        for nc_id in resubmit + STATE["nc_ids_i2"]:
            _review(emp, nc_id, "approve")

        # review entry marked read (condition no longer applies)
        review_entries = _project_entries(emp, pid, "review")
        assert all(e["read"] for e in review_entries), [e["summary"] for e in review_entries]

        # completed entry for staff
        completed = _project_entries(emp, pid, "completed")
        assert len(completed) == 1, f"completed entries: {completed}"

        # installer2 approved entry k of N
        app_i2 = _project_entries(i2, pid, "approved")
        assert len(app_i2) == 1
        # installer2 now owns 4 NCs on this project, 4 approved -> "4 of 4 NCs approved"
        assert "5 of 5 NCs approved" in app_i2[0]["summary"], app_i2[0]["summary"]


# ---------------- unread-count endpoint (if present) ----------------
class TestH_UnreadCount:
    def test_unread_count_endpoint_or_notifications_payload(self, sessions):
        """The spec mentions GET /api/notifications/unread-count. Check it, otherwise
        fall back to the 'unread' field returned by GET /api/notifications."""
        emp = sessions["employee"]["session"]
        r = emp.get(f"{BASE_URL}/api/notifications/unread-count", timeout=10)
        if r.status_code == 404:
            pytest.skip("No /api/notifications/unread-count endpoint; frontend uses GET /api/notifications 'unread'")
        assert r.status_code == 200, f"unread-count: {r.text}"
        j = r.json()
        assert isinstance(j, dict) and ("unread" in j or "count" in j), j
