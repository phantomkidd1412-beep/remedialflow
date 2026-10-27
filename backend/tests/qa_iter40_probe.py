# Iter40: API probe for evidence selection retest (synthetic QA project QA-KBUI-40). Usage: python qa_iter40_probe.py [show|highseq|reverse|installer|login_test]
import json
import os
import sys

import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ["EXPO_BACKEND_URL"]).rstrip("/")
PW = "QaCheck-7731x"


def login(email):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": email, "password": PW})
    r.raise_for_status()
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {r.json().get('access_token') or r.json().get('token')}"
    return s


def project(s):
    pid = next(p["id"] for p in s.get(f"{BASE}/api/projects").json() if p.get("ref") == "QA-KBUI-40") if not os.environ.get("QA_PID") else os.environ["QA_PID"]
    p = s.get(f"{BASE}/api/projects/{pid}").json()
    return pid, p, next(n for n in p["ncs"] if n["number"] == "NC-01")


cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
if cmd == "installer":
    s = login("qa.kbui.installer@example.com")
    pid = os.environ["QA_PID"]
    r = s.get(f"{BASE}/api/projects/{pid}")
    print("installer GET", r.status_code, "evidence_selection present:", "evidence_selection" in r.text)
    sys.exit(0)
s = login("qa.kbui.admin@example.com")
pid, p, n = project(s)
print("pid", pid, "nc", n["id"], "photos", [f["id"][-2:] for f in n["photos"]])
print("evidence_selection", json.dumps(n.get("evidence_selection")))
if cmd == "highseq":  # another 'device' with a far-ahead clock stores seq=9999999999999
    ids = (n.get("evidence_selection") or {}).get("ids") or []
    r = s.put(f"{BASE}/api/ncs/{n['id']}/evidence-selection", json={"ids": ids, "seq": 9999999999999})
    print("PUT high seq", r.status_code, r.json())
if cmd == "reverse":
    sel = n["evidence_selection"]
    r = s.put(f"{BASE}/api/ncs/{n['id']}/evidence-selection", json={"ids": list(reversed(sel["ids"])), "seq": (sel.get("seq") or 0) + 1})
    print("PUT reverse", r.status_code, r.json().get("ids"))
if cmd == "lowseq":
    r = s.put(f"{BASE}/api/ncs/{n['id']}/evidence-selection", json={"ids": [], "seq": 1700000000000})
    print("PUT low seq", r.status_code, r.json().get("stale"), r.json().get("seq"))
