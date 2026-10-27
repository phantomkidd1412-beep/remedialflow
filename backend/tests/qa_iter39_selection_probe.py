# Iter39: probe helper for evidence selection (state inspection / direct PUT). Usage: python qa_iter39_selection_probe.py [show|reverse|installer]
import os, sys, json, requests

BASE = os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ["EXPO_BACKEND_URL"]
BASE = BASE.rstrip("/")
PID = "6adfe3dfceb25c827be8c20c"
PW = "QaCheck-7731x"


def login(email):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": email, "password": PW})
    r.raise_for_status()
    t = r.json().get("access_token") or r.json().get("token")
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {t}"
    return s


def nc01(s):
    p = s.get(f"{BASE}/api/projects/{PID}").json()
    return next(n for n in p["ncs"] if n["number"] == "NC-01"), p


cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
if cmd == "installer":
    s = login("qa.kbui.installer@example.com")
    r = s.get(f"{BASE}/api/projects/{PID}")
    print("status", r.status_code)
    txt = r.text
    print("evidence_selection present:", "evidence_selection" in txt)
    sys.exit(0)

s = login("qa.kbui.admin@example.com")
n, p = nc01(s)
print("nc id", n["id"], "photos", [f["id"] for f in n["photos"]])
print("evidence_selection", json.dumps(n.get("evidence_selection")))
if cmd == "reverse":
    ids = list(reversed(n["evidence_selection"]["ids"]))
    r = s.put(f"{BASE}/api/ncs/{n['id']}/evidence-selection", json={"ids": ids, "seq": (n["evidence_selection"].get("seq") or 0) + 1})
    print("PUT", r.status_code, r.json().get("ids"))
if cmd == "stale":  # a save from a device whose Date.now()-seeded counter is lower is silently ignored (200 + stale)
    r = s.put(f"{BASE}/api/ncs/{n['id']}/evidence-selection", json={"ids": [], "seq": 1700000000000})
    print("PUT low seq", r.status_code, r.json())
    print("after:", nc01(s)[0]["evidence_selection"]["ids"])
