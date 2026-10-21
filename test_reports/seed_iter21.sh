#!/bin/bash
# Seed data for iter 21 frontend testing
set -e
BASE="https://field-evidence-app-1.preview.emergentagent.com"
AT() { curl -sS -X POST $BASE/api/auth/login -H "Content-Type: application/json" -d "{\"email\":\"$1\",\"password\":\"$2\"}" | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])"; }
AT_ADMIN=$(AT admin@remedialflow.dev AdminDemo!2026)
AT_EMP=$(AT employee@remedialflow.dev EmployeeDemo!2026)
AT_INS=$(AT installer@remedialflow.dev InstallerDemo!2026)
echo "ADMIN_TOKEN=${AT_ADMIN:0:12}... EMP=${AT_EMP:0:12}... INS=${AT_INS:0:12}..."

# 1. Create a throwaway project and soft-delete it so /admin/deleted has a row
NEWP=$(curl -sS -X POST $BASE/api/projects -H "Content-Type: application/json" -H "Authorization: Bearer $AT_EMP" -d '{"ref":"ITER21-THROWAWAY","address":"1 Test Street","postcode":"TE1 1ST","ncs":[{"description":"Test NC","required_work":"Fix it","safety_critical":false}]}')
PID=$(python3 -c "import json;print(json.loads('''$NEWP''')['id'])")
echo "Created throwaway project id=$PID"
curl -sS -X DELETE $BASE/api/projects/$PID -H "Authorization: Bearer $AT_EMP" | head -c 200; echo
# Verify in deleted list
curl -sS $BASE/api/admin/deleted-projects -H "Authorization: Bearer $AT_ADMIN" | python3 -c "import json,sys;d=json.load(sys.stdin);print('deleted count',len(d));[print(x['ref'],x['id']) for x in d[:5]]"

# 2. Find an installer project for scheduling test
PROJ=$(curl -sS "$BASE/api/projects" -H "Authorization: Bearer $AT_INS" | python3 -c "import json,sys;d=json.load(sys.stdin);[print(p['id'],p['ref']) for p in d['items'] if p['status']=='in_progress' and p.get('my_statuses') and 'open' in p['my_statuses']][:3]")
echo "Candidate in_progress projects for installer:"
echo "$PROJ"
# Clear schedule for all installers so we can set them in UI test
FIRST_ID=$(echo "$PROJ" | head -1 | awk '{print $1}')
FIRST_REF=$(echo "$PROJ" | head -1 | awk '{print $2}')
echo "SCHEDTEST_ID=$FIRST_ID SCHEDTEST_REF=$FIRST_REF" > /tmp/iter21_ctx.sh
echo "DEL_PID=$PID DEL_REF=ITER21-THROWAWAY" >> /tmp/iter21_ctx.sh
# Also pick a completed one (fully approved) for status badge test
COMP=$(curl -sS "$BASE/api/projects?status=completed" -H "Authorization: Bearer $AT_INS" | python3 -c "import json,sys;d=json.load(sys.stdin);items=[p for p in d['items'] if p['status']=='completed'];print(items[0]['id'] if items else '', items[0]['ref'] if items else '')")
echo "COMPLETED_ID=$(echo $COMP | awk '{print $1}') COMPLETED_REF=$(echo $COMP | awk '{print $2}')" >> /tmp/iter21_ctx.sh
cat /tmp/iter21_ctx.sh
