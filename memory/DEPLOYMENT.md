# RemedialFlow — Production Deployment Runbook (Phase 12)

Nothing in this file has been executed against production. Every step needs the owner's approval.

## 1. Production environment variables (set in the deployment secret manager, never in git)
| Variable | Value |
|---|---|
| `APP_ENV` | `production` (server refuses to boot with unsafe settings) |
| `JWT_SECRET` | new random value, >= 32 chars (`openssl rand -hex 32`) — do NOT reuse the preview one |
| `DEMO_SEED` | `false` |
| `DEMO_MODE` | `false` |
| `EXPOSE_RESET_CODES` | unset |
| `CORS_ORIGINS` | the exact production web origin(s), no `*` |
| `APP_PUBLIC_URL` | `https://<production app domain>` (used in reset-email links) |
| `EMAIL_FROM_NAME` | `RemedialFlow` |
| `EMERGENT_EMAIL_KEY` | provisioned by the platform |
| `EMERGENT_LLM_KEY` | provisioned by the platform (Gemini 3 Flash) |
| `MONGO_URL`, `DB_NAME` | production database (platform managed) |
| `ADMIN_BOOTSTRAP_SECRET` | random >= 24 chars, only until the first Admin exists, then remove |
| `ACCESS_TOKEN_MINUTES` / `REFRESH_TOKEN_DAYS` | 15 / 30 (defaults) |

## 2. First Admin (one-time, owner only)
```
cd backend
ADMIN_BOOTSTRAP_SECRET=... python bootstrap_admin.py owner@yourcompany.co.uk "Owner Name"
```
Prompts for the secret and password (>= 12 chars). Writes `system.admin_bootstrap`; any second run is refused.
There is no HTTP bootstrap endpoint. Remove `ADMIN_BOOTSTRAP_SECRET` afterwards.

## 3. Demo accounts
In `APP_ENV=production` demo accounts (`*@remedialflow.dev`) are refused at login and get no reset emails.
To deactivate them permanently (no deletion): `python disable_demo_accounts.py --yes` (refuses unless a real Admin exists).
Mobile builds never show demo shortcuts (`__DEV__` only).

## 4. Data, storage, backups
- Evidence originals/stamped copies/videos/PDFs live in Emergent Managed Object Storage (private; only short-lived signed URLs, issued after an authorisation check) — survives restarts/redeploys.
- Migrations (`backend/migrations.py`) are additive and idempotent; recorded in `db.migrations`. No migration deletes data.
- Backups: before each deployment take `mongodump --uri "$MONGO_URL" --db "$DB_NAME" --out backup-$(date +%F)`; keep daily dumps 30 days; test a restore (`mongorestore --nsFrom/--nsTo` into a scratch DB) monthly. JSON metadata export: Admin → Settings → Export.
- Never run seed/demo or test scripts against production.

## 5. Monitoring
- Logs redact tokens, signatures, passwords and Bearer headers (`security.RedactSecrets`). Email logs never include recipient or link.
- 5xx responses return generic messages. Review backend logs after deploy; add an external uptime check on `/api/health`.

## 6. Mobile builds
- iOS Info.plist: camera, microphone, location-when-in-use, photo library usage strings present (`app.json`).
- Android: CAMERA, RECORD_AUDIO, ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION.
- Notifications are in-app only (no push => no lock-screen exposure). Push would need Firebase `google-services.json` + approval.
- Build via the platform Publish button only after approval.

## 7. Recurring costs (usage based, Emergent credits)
- Gemini 3 Flash AI assessments + PDF imports (per call).
- Transactional email (per recipient; reset links only).
- Object storage + hosting per the Emergent plan. Apple developer account (USD 99/yr) and Google Play (one-off USD 25) if publishing to stores.
