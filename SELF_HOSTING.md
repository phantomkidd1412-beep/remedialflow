# RemedialFlow – Self-Hosting Guide

RemedialFlow consists of:

| Part | Tech | Folder |
|---|---|---|
| Mobile + web app | Expo SDK 57 / React Native / Expo Router (Metro, single-page web output) | `frontend/` |
| API | Python 3.11, FastAPI, Motor (MongoDB) | `backend/` |
| Database | MongoDB 6+ | – |
| Evidence storage | Dropbox (Scoped App, App-folder, PKCE OAuth) | `backend/storage.py`, `backend/dropbox_link.py` |

All API routes are under `/api`. Web, Android and iOS share one backend, one database and one Dropbox app folder.

---

## 1. Emergent-managed services you must replace

The code currently uses three Emergent-managed services that will **not** work outside Emergent:

| Feature | File | Current | Replace with |
|---|---|---|---|
| AI evidence review + TrustMark PDF extraction | `backend/ai.py` | `emergentintegrations` + `EMERGENT_LLM_KEY` | Google Gemini API (`google-genai` SDK) with your own `GEMINI_API_KEY`; keep the `LlmAssessor`/`PdfExtractor` interfaces |
| Password-reset email | `backend/emailer.py` | `integrations.emergentagent.com` + `EMERGENT_EMAIL_KEY` | Resend, Postmark, SendGrid or SMTP; only the send function needs changing |
| Push notifications | `backend/push.py` | Emergent push proxy + `EMERGENT_PUSH_KEY` | Expo Push Service (`https://exp.host/--/api/v2/push/send`) using Expo push tokens, or Firebase (Android) + APNs (iOS) |
| Legacy evidence (pre-Dropbox) | `backend/storage.py` `EmergentStorage` | Emergent object storage | Set `STORAGE_PROVIDER=dropbox`. Legacy objects are only readable inside Emergent — export them first if you need them |

Also remove these from `backend/requirements.txt` when you replace them: `emergentintegrations`, and the `litellm @ https://customer-assets.emergentagent.com/...` line (install `litellm` from PyPI if still needed).
They are served from a private index (`extra-index-url`) that is not available outside Emergent.

Until replaced: AI review falls back to human review, reset emails won't send, and pushes are skipped. Everything else works.

---

## 2. Windows local development

### Prerequisites
- Git, **Python 3.11**, **Node.js 20 LTS or newer**, Yarn 1 (`npm i -g yarn`)
- MongoDB Community Server 7 (with MongoDB Compass) **or** Docker Desktop
- Expo Go on your phone (for quick testing)

### MongoDB
```powershell
# Option A – Docker
docker run -d --name rf-mongo -p 27017:27017 -v rf-mongo-data:/data/db mongo:7
# Option B – installed service: it listens on mongodb://localhost:27017
```

### Backend
```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt     # after removing the Emergent-only lines (section 1)
copy .env.example .env               # then edit values (section 6)
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
```
For local development set `APP_ENV=development`, `APP_PUBLIC_URL=http://localhost:8081`, `CORS_ORIGINS=http://localhost:8081`.
Health check: http://localhost:8001/health

Create the first Admin (one-time; disabled after use):
```powershell
$env:ADMIN_BOOTSTRAP_SECRET="a-random-string-of-24+chars"
python bootstrap_admin.py you@company.co.uk "Your Name"
```

### Frontend
```powershell
cd frontend
copy .env.example .env      # EXPO_PUBLIC_BACKEND_URL=http://<your-PC-LAN-IP>:8001
yarn install
yarn start                  # press w for web, or scan the QR code with Expo Go
```
Use your PC's LAN IP (not `localhost`) so phones on the same Wi-Fi can reach the API.
Note: browsers only send the web sign-in cookie over HTTPS (`secure=True`), so for persistent web sessions locally either use HTTPS (e.g. a dev tunnel) or expect to sign in again after a refresh.

### Tests
```powershell
cd backend
pytest tests -q     # integration tests expect a running API and test accounts; see each file's header
```

---

## 3. Hosting the FastAPI backend

Any Linux host with Python 3.11 works (VPS, AWS/Azure/GCP VM, Render, Railway, Fly.io, Docker).

**Requirements**
- 2 vCPU / 4 GB RAM minimum (video conversion and PDF processing use FFmpeg/PyMuPDF; install `ffmpeg` on the host)
- Persistent-enough temp disk for `UPLOAD_TMP_DIR` (chunked uploads) and `MEDIA_CACHE_DIR`
- HTTPS (Let's Encrypt via Caddy/Nginx, or the platform's TLS)
- Request body limit ≥ 110 MB on the proxy (uploads are chunked, but keep headroom); timeouts ≥ 300 s for exports

**Run command**
```bash
uvicorn server:app --host 0.0.0.0 --port 8001 --workers 2 --proxy-headers --forwarded-allow-ips="*"
```
Run behind a reverse proxy (Nginx/Caddy) that forwards `X-Forwarded-For` and `X-Forwarded-Proto`; rate limiting of failed logins uses the client IP.

**Docker (example)**
```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
EXPOSE 8001
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2", "--proxy-headers"]
```

**Recommended domain**: `api.networksurveyltd.uk` (CNAME/A record only for `api`; root/www/MX untouched).
Hosting the web app at `app.networksurveyltd.uk` and the API at `api.networksurveyltd.uk` keeps both on the **same site**, so the existing secure web sign-in cookie (`SameSite=Strict`, HttpOnly, Secure) and CSRF protections work with **no code change**.

On startup the backend creates indexes and runs `migrations.py` automatically. In `APP_ENV=production` it **refuses to start** if `JWT_SECRET` is weak, `APP_PUBLIC_URL` isn't HTTPS, `CORS_ORIGINS` contains `*`, or demo flags are on.

---

## 4. MongoDB

- Use **MongoDB Atlas** (M10+ for production backups) or a self-managed replica set.
- Create a dedicated database user with `readWrite` on the RemedialFlow database only; restrict network access to the API server IPs.
- Connection string goes in `MONGO_URL`; database name in `DB_NAME`.
- Main collections: `users`, `projects`, `ncs`, `files`, `submissions`, `earnings`, `notifications`, `audit`, `imports`, `auto_packs`, `dropbox_exports`, `upload_sessions`, `sessions`, `reset_tokens`, `login_attempts`, `rate_limits`, `settings`, `secure_settings` (encrypted Dropbox token), `migrations`, `decorations`, `system`.

**Moving data from Emergent**: request a production database export from support@emergent.sh (job ID), then:
```bash
mongorestore --uri "$MONGO_URL" --nsInclude "<db>.*" ./dump
```
After restoring, reconnect Dropbox (section 5) — the stored token is encrypted with the old `JWT_SECRET`.

---

## 5. Dropbox

1. In the Dropbox App Console use your existing app (Scoped access, **App folder**). Required scopes (Permissions tab): `files.metadata.read`, `files.content.read`, `files.content.write` (the app requests exactly these).
2. **Redirect URIs** → add `https://api.networksurveyltd.uk/api/admin/dropbox/callback` (exactly, no trailing slash).
3. Backend env: `STORAGE_PROVIDER=dropbox`, `DROPBOX_APP_KEY=<app key>`, `DROPBOX_REDIRECT_URI=<same URL>`. No app secret is needed (PKCE).
4. Sign in as Admin → **Settings → Evidence Storage → Connect Dropbox**. Check the callback URL shown matches step 2.
5. Verify: upload one photo; it should appear in `/Apps/<your app>/remedialflow/{Address, POSTCODE}/Media/`.

Folder layout: `remedialflow/{Address, POSTCODE}/Media/` (all installer originals, never overwritten), `remedialflow/{Address, POSTCODE}/{Project Ref}/` (manual + automatic evidence-pack ZIPs), `remedialflow/_system/` (thumbnails, playback copies, AI frames, report images). The app never deletes Dropbox files.
If you reconnect the **same app and account**, existing Dropbox file IDs in MongoDB keep working.

---

## 6. Environment variables

See `backend/.env.example` and `frontend/.env.example`. Production essentials:

| Variable | Value |
|---|---|
| `APP_ENV` | `production` |
| `DEMO_SEED` / `DEMO_MODE` | `false` / `false` |
| `EXPOSE_RESET_CODES` | unset |
| `MONGO_URL`, `DB_NAME` | your database |
| `JWT_SECRET` | 64+ random chars (`python -c "import secrets;print(secrets.token_urlsafe(48))"`) |
| `URL_SIGNING_SECRET` | optional separate secret for signed media/export links |
| `ACCESS_TOKEN_MINUTES` / `REFRESH_TOKEN_DAYS` / `RESET_LINK_MINUTES` | `15` / `30` / `30` |
| `APP_PUBLIC_URL` | `https://app.networksurveyltd.uk` |
| `CORS_ORIGINS` | `https://app.networksurveyltd.uk` |
| `STORAGE_PROVIDER`, `DROPBOX_APP_KEY`, `DROPBOX_REDIRECT_URI` | section 5 |
| `MAX_VIDEO_MB` | `500` (default) |
| `UPLOAD_TMP_DIR`, `MEDIA_CACHE_DIR` | writable paths |
| AI / email / push keys | your replacement providers (section 1) |
| `ADMIN_BOOTSTRAP_SECRET` | only while creating the first Admin, then remove |

Frontend (baked in at build time): `EXPO_PUBLIC_BACKEND_URL=https://api.networksurveyltd.uk`.

---

## 7. Expo web deployment

```bash
cd frontend
EXPO_PUBLIC_BACKEND_URL=https://api.networksurveyltd.uk npx expo export --platform web
# output: frontend/dist  (single-page app)
```
Host `dist/` on any static host (Netlify, Cloudflare Pages, Azure Static Web Apps, S3+CloudFront, Nginx).
Add a single-page fallback so every route serves `index.html`, e.g. Netlify `dist/_redirects`:
```
/*  /index.html  200
```
DNS: one CNAME for `app` → your static host. Do not change the root, `www` or MX records; the existing company website is unaffected.

---

## 8. Android and iOS builds (EAS)

1. Create an Expo account and install the CLI: `npm i -g eas-cli`, then `eas login`.
2. In `frontend/`: `eas init` (links the project), then `eas build:configure` (creates `eas.json`).
3. Change identifiers in `app.json` to your own (currently `com.emergent.fieldevidenceapp.pnhb44`) — e.g. `uk.networksurvey.remedialflow`. A different identifier means a new store listing; keep the old one only if you have transferred the existing listing.
4. Set `EXPO_PUBLIC_BACKEND_URL` for builds (EAS environment variables or `eas.json` `env`).
5. Build:
   ```bash
   eas build --platform android --profile production   # .aab for Play Store
   eas build --platform ios --profile production       # requires Apple Developer account ($99/yr)
   eas submit --platform android|ios
   ```
6. Push notifications: add `google-services.json` (Android, Firebase) and APNs key (iOS, via EAS credentials), and reference them in `app.json`.
7. Store review: provide a reviewer account; Apple requires an in-app account-deletion option.

Costs: EAS free tier covers occasional builds; paid plans from about $19/month. Google Play: $25 one-off. Apple: $99/year.

---

## 9. Backups

| What | How | Retention |
|---|---|---|
| MongoDB | Atlas continuous backup/snapshots, or nightly `mongodump --uri "$MONGO_URL" --gzip --archive=rf-$(date +%F).gz` to off-site storage | 30 daily + 12 monthly |
| Dropbox evidence | Dropbox version history/file recovery (plan-dependent); optionally a periodic copy of `/Apps/<app>/remedialflow` to separate storage | per business/TrustMark requirements |
| Config | Keep `.env` values in a password manager/secret store, not in Git | – |

Test a restore into a separate database at least quarterly. After restoring to a server with a different `JWT_SECRET`, reconnect Dropbox and expect users to sign in again.

---

## 10. Security checklist

- HTTPS everywhere; HSTS on both `app.` and `api.` domains.
- Strong unique `JWT_SECRET` (and `URL_SIGNING_SECRET`); rotate by redeploying (signs everyone out; reconnect Dropbox).
- `CORS_ORIGINS` lists only `https://app.networksurveyltd.uk`; never `*`.
- MongoDB not exposed to the internet; least-privilege DB user; encryption at rest (Atlas default).
- Secrets only in the host's secret manager / `.env` (already git-ignored); never in the frontend (`EXPO_PUBLIC_*` values are public).
- Keep demo flags off; never re-run `bootstrap_admin.py` once an Admin exists.
- Role permissions are enforced server-side (Admin / Employee / Installer); installers cannot alter submitted evidence; Employees cannot see rates.
- Built-in protections: failed-login rate limiting, server-side session revocation, single-use reset links, CSRF protection for the web cookie, security headers, log redaction, signed time-limited media URLs.
- Patch regularly: `pip list --outdated`, `yarn outdated`; keep the OS and FFmpeg updated.
- Monitor `/health` and API error logs; alert on repeated 5xx or failed-login spikes.
