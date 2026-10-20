# RemedialFlow — PRD

## Original problem statement
Minimal, high-performance fieldwork app for TrustMark NC remediation. Roles: Admin, Employee, Installer. Installer tabs Jobs | Earnings | Profile; built-in geotagged camera with visible stamp; original + stamped copies; one active video per NC with versioning; offline queue; background AI assessment (AI Verified / More Evidence Required / Manual Review Required); minimal staff dashboard (4 counts + searchable project table); private storage, hashes, immutable audit, export.

## User choices
- AI: Gemini 3 Flash (`gemini-3-flash-preview`) via Emergent LLM key; swappable via AI_PROVIDER/AI_MODEL (backend/ai.py `Assessor`)
- Auth: JWT email/password, admin-only user creation, no public registration, refresh rotation + server sessions, HttpOnly cookie on web
- Storage: Emergent Object Storage behind `StorageProvider` (backend/storage.py), so S3/Supabase can be added later
- Location Not Verified policy: accept + flag for employee review (configurable)
- Earnings: fixed rate per job (default from settings, per-project override), earned when all NCs approved

## Architecture
- Backend FastAPI: core.py (models, signed URLs, hash-chained audit), auth.py, storage.py, ai.py, media.py (Pillow stamping, ffmpeg frames), server.py (routes, seed)
- Evidence paths: remedialflow/projects/{pid}/ncs/{nc}/submissions/{sid}/{file}/{variant}
- Frontend Expo Router: (installer) tabs, (staff) tabs, job/[id], camera, video, project/[id], project/new, admin/users, admin/settings; src/queue.tsx offline queue

## Implemented (2026-10-19)
- Everything above. Testing: backend 24/24 pass, frontend flows pass (iteration_1)
- Create project from inspection PDF: AI (Gemini 3 Flash) extracts ref, address, postcode, NC count + NCs (description, work, safety flag, pages); report photos attached as TrustMark references; PDF stored with project (signed view link). Iteration_2: 9/9 backend + UI pass

- Fix (2026-10-19): web PDF/Export buttons caused "refused to connect" in preview iframe (window.open). Now same-origin blob download. Iteration_3 pass
- Upgrade P0 (security) + P1 (dashboards/project details) done 2026-10-19: migrations 001/002, unified status model, lifecycle + needs_review, TrustMark Resolved, 5 counters, mobile cards, project action menu. Iteration_4: 19/19 backend + UI pass. Next: P2 NC-level assignment (see UPGRADE_PLAN.md)
- Upgrade P2 (NC assignment + in-app notifications), P3 (low-risk AI approval, escalation, admin review), P4 (grouped review queue, auto-advance), weekly earnings with per-installer rates/daily wage — done 2026-10-19, iteration_5 pass (33/35, 2 skip). Migration 003.
- Upgrade P5 (installer sections, TrustMark rejection/reopen), P6 (camera flash/zoom/tap-refocus, swipe+zoom PhotoViewer), P8 (Evidence Pack ZIP: NC PDFs before/after, logo filter, near-dup removal, Videos/), weekly payments by installer — done 2026-10-19, iteration_6: 26/26 backend pass.
- Remaining: P7 (PDF logo filter at import + duplicate project detection), P10 (Work Tracker CSV/Excel), P11-P12 (offline hardening, admin cleanup/typography). AI-ranked photo selection uses sharpness/dedupe heuristics (no AI call yet).

## Backlog
- P1: Email delivery for reset/setup codes (currently shown to admin / dev code)
- P1: Chunked/resumable upload for very large videos
- P2: S3 / Supabase StorageProvider implementations
- P2: Reports screen (per installer / per period)
- Before production: set DEMO_SEED=false, DEMO_MODE=false, delete demo accounts

## P7 PDF clean-up (done, agent-tested iteration 7)
- Logo/decoration filter on import (banner aspect >3:1, flat-colour graphics, running headers on 3+ pages & ≥half of pages); real photos reused 2-3 times are kept.
- Migration 004 hid logos on existing projects retroactively (files kept; `excluded`, `exclude_reason`). Staff can show/restore or long-press to hide; `manual_keep` respected by re-clean (`POST /projects/{pid}/clean-report-photos`).
- Import summary: "N NCs detected (report states M) · X photos kept · Y skipped".
- Duplicate detection (same ref, or same postcode + similar address) → `/project/merge` screen: match=Update by default, ambiguous must be chosen, new=Add. Old wording kept in `text_history`, old report in `report_history`; nothing deleted. Duplicate report photos skipped via 256-bit dHash.
- Backend module: `backend/report_import.py`.
- P7 revision (iteration 8): filter made conservative. Hidden ONLY: 'repeated' (same non-photographic image on a white/black background, 3+ pages and >= half of pages, same aspect) or 'known_logo' (matches a fingerprint learned in db.decorations, hash dist<=10 + aspect within 12%). Single-occurrence images (technical diagrams, annotated/measurement photos) always kept. Migrations 005-007 re-applied with reset; manual choices kept. Stale legacy tests updated; full regression 132 passed / 4 data-conditional skips. Phase 7 awaiting user approval with real TrustMark reports.

## P9 Earnings pay types (implemented, awaiting test/approval)
- Existing: per-user pay_type per_job|daily + default_rate (Admin → Users); rate frozen per installer at first assignment; daily-wage = paid outside the app (user choice), no job earnings.
- New: admin-only PUT /api/projects/{pid}/rates/{iid} {rate, reason?} overrides one installer's agreed rate; unpaid earned amount follows (amount_history, overridden flag); paid → 409; daily → 400; unassigned installer → 400. rate_history on project, audit project.rate_overridden, installer notified (rate_changed).
- Project detail (staff) returns rate_rows; installers only see their own rate. UI: "Installer rates" card with Edit sheet (admin); payments view shows per-job amounts and "(rate changed)".

## P10 (implemented, awaiting approval)
- Work Tracker `/tracker` (admin+employee; Account → Work tracker): views completed (keyed on first `completed_at`) / resolved (keyed on first `resolution_history` entry) → one row per project, reopened flagged, never double counted. Ranges week/month (prev/next) /custom/all; search ref/address/installer; sort newest/oldest/longest outstanding (days from creation to resolution, still counting if unresolved). CSV + Excel via signed `/api/tracker/export-link` → `/api/tracker/export` (openpyxl).
- Soft delete: `DELETE /api/projects/{pid}` (admin+employee) sets `project.deleted {at,by,by_name,by_role}`, `ncs.project_deleted=true`, notifications hidden. Hidden from dashboard, lists/search, review queues, installer jobs/pending earnings; get_project/live_project 404; notify() and settle_earnings() skip deleted projects. Nothing erased.
- Recycle bin `/admin/deleted` (admin): `GET /api/admin/deleted-projects`, `POST /api/admin/projects/{pid}/restore` (atomic, same record, pushes deletion_history, recompute is idempotent).
- Activity log `/admin/activity` (admin): `GET /api/admin/activity?search&kind=deletion|project|nc|user`; audit entries now snapshot actor_name/actor_role server-side; no edit/delete API for audit.
- Module: backend/phase10.py; migration 008_soft_delete (additive + indexes).

## P11 Offline evidence & sync (implemented, awaiting approval)
- Existing queue (src/queue.tsx) kept: files copied to app-private Paths.document/evidence, persisted queue, sequential sync, 15s interval + AppState active resume, idempotent server dedupe by client_id, item removed only after server 2xx.
- Added: items owned by userId (only owner's items shown/synced; shared phone safe); statuses Pending Upload / Uploading / Uploaded (4s, then released) / Upload Failed; SyncBar "Uploading X of Y", RETRY for failed; 403/404/409 on sync → failed with actionable message, evidence kept; two-step delete for local items; logout warns and KEEPS evidence (no delete option).
- Offline project cache src/offlineCache.ts: per-user, only data previously loaded online (job list page 1 + project detail); 403/404 drops the cached copy; cleared on logout. Tokens remain in SecureStore.
- Backend unchanged: get_nc enforces current assignment (403) and deleted project (404); approved NC upload 409; discard only drafts; evidence hide/restore/discard audited.
- P11 fix: app/_layout.tsx setDefaultOptions now carries networkMode 'offlineFirst' + retry skipping offline/4xx (previously it overrode query-client defaults, pausing queries offline). Verified iteration 14.

## P12 Production security (implemented, awaiting approval; NOT deployed)
- backend/security.py: Mongo rate limiter (forgot 3/15min per email+IP, 20/h per IP; reset 10/15min per IP; 30 failed logins/IP/15min + existing 8/email), security headers, log redaction of tokens/sig/passwords, check_production_config (APP_ENV=production refuses DEMO_SEED, weak JWT_SECRET, non-https APP_PUBLIC_URL, CORS *). Docs/openapi disabled in production.
- Reset: emailed single-use link (Emergent managed Resend, backend/emailer.py w/ guardrail gate), atomic claim, all other tokens superseded, sessions revoked; generic response; frontend app/reset.tsx; admin setup codes still work.
- bootstrap_admin.py (one-time CLI, secret + db.system marker), disable_demo_accounts.py (deactivate only, owner approval). Demo logins refused in production.
- Uploads: magic-byte type validation (jpg/png/heic/webp, mp4/mov), server-chosen extension. notify() skips inactive users.
- Runbook: memory/DEPLOYMENT.md.
