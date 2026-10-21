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

## 8. Push notifications (pending)
- Provide `frontend/google-services.json` (package `com.emergent.fieldevidenceapp.pnhb44`) and add `"googleServicesFile": "./google-services.json"` to app.json android section.
- At build time supply APNs auth key (.p8) and Google service account JSON. EMERGENT_PUSH_KEY is set by the deployment pipeline (never edit).
- Push works only in real builds (not Expo Go / web). Cost: per recipient per push (Emergent credits).

## 9. Approved-later test: real reset email
- Needs your recipient address + approval. Steps: create/choose a user with that email, POST /api/auth/forgot, open link, set password. One email ≈ one recipient unit of credits.

## 10. Execution order on production (do not run without approval)
1. Back up DB (mongodump). 2. Set production env (section 1) incl. ADMIN_BOOTSTRAP_SECRET.
3. `python bootstrap_admin.py owner@… "Owner"` → verify login. 4. Remove ADMIN_BOOTSTRAP_SECRET.
5. `python disable_demo_accounts.py --yes` (refuses unless a real admin exists; deactivates, never deletes, revokes sessions).
6. Verify demo logins fail and Activity Log shows system.admin_bootstrapped + user.demo_disabled.

## 11. Dropbox evidence storage (production)
1. Dropbox App Console → RemedialFlow Evidence → Settings → Redirect URIs: add https://api.networksurveyltd.uk/api/admin/dropbox/callback
2. Production secrets: DROPBOX_APP_KEY=<app key>, DROPBOX_REDIRECT_URI=https://api.networksurveyltd.uk/api/admin/dropbox/callback (no app secret needed - PKCE).
3. After deploy: Admin → Settings → Evidence storage → Connect Dropbox → Allow. Status must show "✓ Dropbox connected".
4. Production uses a NEW database, so Dropbox must be connected again there. JWT_SECRET encrypts the stored token: changing JWT_SECRET later requires reconnecting.
