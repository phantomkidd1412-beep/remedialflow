# Iter49: analyse DOM snapshots from the upload-ordering UI run (/tmp/i49_snaps.json) against DB client_ids.
import json, os, re, sys
from pymongo import MongoClient
from dotenv import load_dotenv
load_dotenv('/app/backend/.env')
db = MongoClient(os.environ['MONGO_URL'])[os.environ['DB_NAME']]
seed = json.load(open('/tmp/i48_seed.json'))
nid = seed['NC-01']['nid']
files = list(db.files.find({"nc_id": nid}))
cid = {str(f['_id']): f.get('client_id') for f in files}
print("records", len(files), "client_ids", sorted(cid.values(), key=str))
d = json.load(open('/tmp/i49_snaps.json'))
def idx_of(t):
    if t.startswith("upload-tile-"): k = t[len("upload-tile-"):]
    else: k = cid.get(t[len("gallery-cell-"):], "?")
    m = re.match(r"up-\d+-(\d+)$", k or ""); return k, (int(m.group(1)) if m else None)
dup = order_bad = dupfid = 0; seen_states = set()
for s in d['snaps']:
    keys = [idx_of(t) for t in s['ids']]
    ks = [k for k, _ in keys]
    if len(ks) != len(set(ks)): dup += 1; print("DUP at", s['t'], s['ids'])
    reals = [t for t in s['ids'] if t.startswith("gallery-cell-")]
    if len(reals) != len(set(reals)): dupfid += 1
    ix = [i for _, i in keys]
    if ix != sorted(ix): order_bad += 1; print("ORDER at", s['t'], ix)
    seen_states.add(tuple(("U" if t.startswith("upload") else "R") + str(i) for t, (_, i) in zip(s['ids'], keys)))
print("snapshots", len(d['snaps']), "dup_key_snaps", dup, "dup_fid_snaps", dupfid, "out_of_order_snaps", order_bad)
print("distinct states:"); [print("  ", st) for st in sorted(seen_states, key=len)]
for name in ("order_failed", "order_final", "order_reload"):
    print(name, [idx_of(t)[1] for t in d[name]])
print("per client_id records:", {k: sum(1 for v in cid.values() if v == k) for k in set(cid.values())})
