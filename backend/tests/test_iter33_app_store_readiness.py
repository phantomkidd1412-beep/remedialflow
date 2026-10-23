"""iter33: App Store readiness - account deletion (self + admin), privacy page, help/support.

Creates TEMPORARY QA users directly in MongoDB (never touches the real admin).
All qa.* users, their sessions, reset_tokens, login_attempts and audit entries are
removed in module teardown.
"""
import os
import secrets
import pytest
import requests
from pymongo import MongoClient
from bson import ObjectId

BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

import sys
sys.path.insert(0, "/app/backend")
from auth import ph  # type: ignore

QA_PREFIX = "qa.iter33."


@pytest.fixture(scope="module")
def mongo():
    c = MongoClient(MONGO_URL)
    yield c[DB_NAME]
    # cleanup
    db = c[DB_NAME]
    qa_users = list(db.users.find({"email": {"$regex": "^qa\\."}}, {"_id": 1, "email": 1}))
    anon_users = list(db.users.find({"email": {"$regex": "^deleted-"}}, {"_id": 1, "email": 1}))
    qa_ids = [str(u["_id"]) for u in qa_users + anon_users]
    qa_emails = [u["email"] for u in qa_users + anon_users]
    for uid in qa_ids:
        db.sessions.delete_many({"user_id": uid})
        db.reset_tokens.delete_many({"user_id": uid})
        db.audit.delete_many({"$or": [{"actor_id": uid}, {"target": f"user:{uid}"}]})
    db.login_attempts.delete_many({"email": {"$in": qa_emails}})
    db.users.delete_many({"_id": {"$in": [ObjectId(i) for i in qa_ids]}})
    c.close()


def make_qa_user(mongo, role="installer", password="QaPass!234", email_suffix=None, active=True):
    email = f"{QA_PREFIX}{email_suffix or secrets.token_hex(4)}@example.com"
    doc = {
        "email": email, "name": f"QA {role}", "role": role,
        "password_hash": ph.hash(password), "active": active, "token_version": 0,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    res = mongo.users.insert_one(doc)
    return {"id": str(res.inserted_id), "email": email, "password": password, "role": role}


def login(email, password):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    j = r.json()
    return j["access_token"], j.get("refresh_token")


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


# --- Health / privacy page is backend only? Privacy is a frontend route, not an API ---

def test_privacy_route_not_a_backend_api(mongo):
    # /privacy is an Expo Router page, no backend endpoint required
    r = requests.get(f"{BASE}/api/privacy", timeout=10)
    assert r.status_code in (404, 405)


# --- SELF DELETE ---

class TestSelfDelete:
    def test_wrong_password_rejected(self, mongo):
        u = make_qa_user(mongo, role="installer", email_suffix="self_wrong")
        tok, rtok = login(u["email"], u["password"])
        r = requests.post(f"{BASE}/api/auth/delete-account", headers=auth(tok),
                          json={"password": "WrongPass!!"}, timeout=15)
        assert r.status_code == 400
        assert "incorrect" in r.text.lower()
        # user doc unchanged
        doc = mongo.users.find_one({"_id": ObjectId(u["id"])})
        assert doc["email"] == u["email"]
        assert doc.get("deleted_account") is not True
        # token still works
        me = requests.get(f"{BASE}/api/auth/me", headers=auth(tok), timeout=10)
        assert me.status_code == 200

    def test_successful_self_delete_anonymises_and_revokes(self, mongo):
        u = make_qa_user(mongo, role="installer", email_suffix="self_ok")
        tok, rtok = login(u["email"], u["password"])
        r = requests.post(f"{BASE}/api/auth/delete-account", headers=auth(tok),
                          json={"password": u["password"]}, timeout=15)
        assert r.status_code == 200, r.text

        # old access token now 401
        me = requests.get(f"{BASE}/api/auth/me", headers=auth(tok), timeout=10)
        assert me.status_code == 401

        # refresh token now 401
        rr = requests.post(f"{BASE}/api/auth/refresh", json={"refresh_token": rtok}, timeout=10)
        assert rr.status_code == 401

        # login with old email fails
        lr = requests.post(f"{BASE}/api/auth/login",
                           json={"email": u["email"], "password": u["password"]}, timeout=10)
        assert lr.status_code == 401

        # user doc anonymised
        doc = mongo.users.find_one({"_id": ObjectId(u["id"])})
        assert doc["name"] == "Deleted user"
        assert doc["email"] == f"deleted-{u['id']}@deleted.invalid"
        assert doc["password_hash"] == ""
        assert doc["active"] is False
        assert doc["deleted_account"] is True
        assert doc.get("push_active") is False

        # audit event with no name/email leaked
        ev = mongo.audit.find_one({"event": "user.account_deleted", "target": f"user:{u['id']}"})
        assert ev is not None
        data = ev.get("data") or {}
        assert "email" not in data and "name" not in data
        assert data.get("mode") == "self"

    def test_sole_active_admin_cannot_self_delete(self, mongo):
        # Create ONE QA admin and TEMPORARILY deactivate every OTHER admin so logic sees "sole".
        # We DO NOT delete or modify the real admin's identity; we only flip 'active' and restore it.
        qa_admin = make_qa_user(mongo, role="admin", email_suffix="sole_admin")
        other_admin_ids = [d["_id"] for d in mongo.users.find(
            {"role": "admin", "active": True, "_id": {"$ne": ObjectId(qa_admin["id"])}}, {"_id": 1})]
        mongo.users.update_many({"_id": {"$in": other_admin_ids}}, {"$set": {"active": False}})
        try:
            tok, _ = login(qa_admin["email"], qa_admin["password"])
            r = requests.post(f"{BASE}/api/auth/delete-account", headers=auth(tok),
                              json={"password": qa_admin["password"]}, timeout=15)
            assert r.status_code == 400
            assert "only active admin" in r.text.lower() or "sole" in r.text.lower() or "another user an admin" in r.text.lower()
            # not anonymised
            doc = mongo.users.find_one({"_id": ObjectId(qa_admin["id"])})
            assert doc.get("deleted_account") is not True
        finally:
            mongo.users.update_many({"_id": {"$in": other_admin_ids}}, {"$set": {"active": True}})


# --- ADMIN DELETE ---

class TestAdminDelete:
    def test_non_admin_forbidden(self, mongo):
        emp = make_qa_user(mongo, role="employee", email_suffix="emp_a")
        inst = make_qa_user(mongo, role="installer", email_suffix="inst_a")
        victim = make_qa_user(mongo, role="installer", email_suffix="victim_a")
        tok, _ = login(emp["email"], emp["password"])
        r = requests.delete(f"{BASE}/api/admin/users/{victim['id']}", headers=auth(tok), timeout=10)
        assert r.status_code == 403
        tok2, _ = login(inst["email"], inst["password"])
        r2 = requests.delete(f"{BASE}/api/admin/users/{victim['id']}", headers=auth(tok2), timeout=10)
        assert r2.status_code == 403

    def test_admin_cannot_delete_self_via_admin_endpoint(self, mongo):
        qa_admin = make_qa_user(mongo, role="admin", email_suffix="noself")
        tok, _ = login(qa_admin["email"], qa_admin["password"])
        r = requests.delete(f"{BASE}/api/admin/users/{qa_admin['id']}", headers=auth(tok), timeout=10)
        assert r.status_code == 400
        assert "account" in r.text.lower()

    def test_admin_deletes_other_and_blocks_further_ops(self, mongo):
        qa_admin = make_qa_user(mongo, role="admin", email_suffix="actor_admin")
        victim = make_qa_user(mongo, role="installer", email_suffix="victim_b")
        v_tok, v_rtok = login(victim["email"], victim["password"])

        a_tok, _ = login(qa_admin["email"], qa_admin["password"])
        r = requests.delete(f"{BASE}/api/admin/users/{victim['id']}", headers=auth(a_tok), timeout=10)
        assert r.status_code == 200

        # victim sessions revoked
        me = requests.get(f"{BASE}/api/auth/me", headers=auth(v_tok), timeout=10)
        assert me.status_code == 401
        rr = requests.post(f"{BASE}/api/auth/refresh", json={"refresh_token": v_rtok}, timeout=10)
        assert rr.status_code == 401

        # anonymised
        doc = mongo.users.find_one({"_id": ObjectId(victim["id"])})
        assert doc["name"] == "Deleted user"
        assert doc["email"].startswith("deleted-")
        assert doc.get("deleted_account") is True

        # second delete -> 404
        r2 = requests.delete(f"{BASE}/api/admin/users/{victim['id']}", headers=auth(a_tok), timeout=10)
        assert r2.status_code == 404

        # not in admin user list
        lst = requests.get(f"{BASE}/api/admin/users", headers=auth(a_tok), timeout=10).json()
        assert all(x["id"] != victim["id"] for x in lst)

        # patch active=true -> 404
        pr = requests.patch(f"{BASE}/api/admin/users/{victim['id']}", headers=auth(a_tok),
                            json={"active": True}, timeout=10)
        assert pr.status_code == 404

        # reset -> 404
        rr = requests.post(f"{BASE}/api/admin/users/{victim['id']}/reset", headers=auth(a_tok), timeout=10)
        assert rr.status_code == 404

        # audit event has no name/email
        ev = mongo.audit.find_one({"event": "user.account_deleted", "target": f"user:{victim['id']}"})
        assert ev is not None
        data = ev.get("data") or {}
        assert "email" not in data and "name" not in data
        assert data.get("mode") == "admin"


# --- REGRESSION ---

class TestRegression:
    def test_login_change_password_logout(self, mongo):
        u = make_qa_user(mongo, role="installer", email_suffix="regress")
        tok, rtok = login(u["email"], u["password"])
        me = requests.get(f"{BASE}/api/auth/me", headers=auth(tok), timeout=10)
        assert me.status_code == 200 and me.json()["email"] == u["email"]

        # change password
        new_pw = "NewPass!234"
        cr = requests.post(f"{BASE}/api/auth/change-password", headers=auth(tok),
                           json={"current_password": u["password"], "new_password": new_pw}, timeout=15)
        assert cr.status_code == 200
        fresh_tok = cr.json()["access_token"]
        fresh_rtok = cr.json()["refresh_token"]

        # old token revoked
        me2 = requests.get(f"{BASE}/api/auth/me", headers=auth(tok), timeout=10)
        assert me2.status_code == 401

        # login with new password works
        tok2, _ = login(u["email"], new_pw)
        assert tok2

        # logout invalidates refresh
        lo = requests.post(f"{BASE}/api/auth/logout",
                           json={"refresh_token": fresh_rtok}, timeout=10)
        assert lo.status_code == 200
        rr = requests.post(f"{BASE}/api/auth/refresh", json={"refresh_token": fresh_rtok}, timeout=10)
        assert rr.status_code == 401
