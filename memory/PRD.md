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

## Pre-deploy final check (iteration 16, green)
- CSRF: auth.cookie_session (X-Client: web + Origin allowlist) on cookie refresh/logout; SameSite=Strict; native unaffected. Note: preview ingress rewrites SameSite to None; verify production.
- Push (Emergent relay, backend/push.py; frontend src/push.ts + app/_layout.tsx): generic text, authenticated registration, push_active cleared on logout, inactive users skipped, only new notifications push; employees get review_needed. Needs google-services.json (pending from user) + build-time APNs .p8 and Google service account JSON. EMERGENT_PUSH_KEY=placeholder until deploy.
- Security audit: CONDITIONAL PASS; SEC-001 (change-password session revocation) fixed; discard_evidence NC/uploader-scoped. Remaining P3: CORS methods/headers "*", uppercase setup-code entropy.

## Security audit follow-up (post Phase 12) — agent-tested (iteration 17)
- Fresh full audit: no Critical/High. Fixed both Mediums: spreadsheet formula injection in Work Tracker CSV/XLSX (phase10._safe), duplicate earnings race (unique index earnings(project_id, installer_id) + DuplicateKeyError handling).
- Hardening: CSP on /api/*; TrustMark report PDFs served as attachment; dedicated derived URL-signing key (optional URL_SIGNING_SECRET env); file links 60-75 min, export/report/tracker links 10 min; signed links 404 for soft-deleted projects; admin setup/reset codes 16 chars.
- Regression: 260 passed / 4 skipped. Nothing deployed, no email sent, no accounts disabled.

## Fixes: evidence submit state + deleted-project reimport (iteration 19, agent-tested)
- Installer NC badge derived from queue (evidenceState): Pending Upload / Uploading / Submitting (only while request runs) / Submitted / Upload Failed (+error, Retry) / server status ("Awaiting Review" for assessing). NetInfo reconnect auto-sync; request timeouts reset stuck items to pending; survives restarts.
- Submit API: optional {client_id, expect}; 409 until all expected uploads arrive (AI not triggered); idempotent retries; atomic claim prevents double assessment.
- Deleted-project reimport: duplicate.deleted flag; POST /api/projects/restore-match (staff; import owner or exact ref; server recomputes match); Restore and continue import -> merge screen; audit project.restored with role/via; recycle bin stays admin-only.
- Backend regression 278 passed / 4 skipped.

## Gallery video upload + simplified NC PDF (iteration 20, agent-tested)
- Installer Gallery picker: photos + videos (multi-select), iOS passthrough/original representation, MP4/MOV (server byte-sniffed), one active video per NC (first selected used). Uses existing offline queue/retry/client_id dedupe. Per-item upload % (XHR on web, expo-file-system createUploadTask on native).
- Gallery stamps never claim GPS; missing EXIF date -> "DATE TAKEN: NOT AVAILABLE"; EXIF date marked "FROM FILE, NOT VERIFIED".
- NC PDF: address, Project/NC reference, NON-COMPLIANCE (original wording), original TrustMark photos, remediation photos, optional staff "PDF remediation note" (PUT /api/ncs/{id}/export-note). No approval/AI/status info. Corrupt images skipped. ZIP + Videos/ unchanged.

## Video evidence fixes (iteration 21, agent-tested)
- Root cause: no HTTP Range support on file serving (iOS AVPlayer needs 206) + iPhone HEVC .MOV not decodable on web/many Android + UI showed only one "active" video.
- Server builds universal H.264/AAC faststart "playback" copy (lossless remux when already H.264; transcode otherwise), originals untouched; startup backfill; byte-range streaming with disk cache; GET /api/evidence/{fid}/play returns fresh signed URLs (authz via get_nc).
- No duration limit; MAX_VIDEO_MB env (default 500) via GET /api/config/media shown in UI; size_bytes integrity check (422 -> auto retry).
- Multiple videos per NC (nc.videos), never superseded; all in ZIP Videos/. Simplified video viewer (player + Close only).
- Note: headless test Chromium lacks H.264, so web playback is verified only in real browsers.

## Operational workflow improvements (iterations 21-22, agent-tested)
- Admin Deleted Projects: search (ref/address/postcode), Restore, Permanently delete (admin only, typed-ref confirmation, POST /api/admin/projects/{pid}/purge; earnings kept with project_purged; audit project.purged). Storage objects can't be deleted via the managed storage API (DB records removed).
- Evidence Rules & Payments: installer dropdown -> editable default rate -> Save (PATCH /api/admin/users/{uid}); rates frozen per project at assignment, earned/paid unchanged.
- Employees: no rates/earnings/payment settings (API strips/403s + UI hidden).
- Installer Earnings: Potential (estimated) / Payment pending / Paid + summary, per installer's own NCs.
- Installer Jobs: Rejected/Revisit, Current week (incl. overdue), Next week, Upcoming, Not yet scheduled, Completed. Optional per-installer schedule date (PUT /api/projects/{pid}/schedule/{iid}), set on staff project page.
- "Project Progress: X of Y NCs Completed"; status label "Internally completed" (only when all NCs approved).
- project_ready in-app + push notification to employees/admins once all NCs have server-submitted evidence; latch ready_notified_at re-armed on reject/reopen.
- Approve button -> green "Approved" state, disabled while processing.

## Final UX improvements (iteration 22, agent-tested)
- No fixed photo limit per NC (gallery selectionLimit 0; horizontal FlatList rows; export PDF up to 60 relevant photos/NC after near-duplicate removal). 500 MB video limit unchanged.
- Installer Earnings: 3 summary cards (Potential / Pending payment / Total paid) + 3 tabs; /api/earnings items include installer's NC numbers.
- Installer Jobs: tappable summary cards (Revisit required, This week, Next week, Unscheduled, Completed) filter the list; one card per project with own-NC count/progress.
- Theme: Light / Dark / Auto on every role's Account/Profile screen; stored on device (AsyncStorage rf.themePref), persists across logout.

## Real-report Phase 7 validation + email delivery test (iteration 24, agent-tested)
- Real TrustMark PDF P419977 (280 Dallow Road, LU1 1TD): 3 NCs extracted with original wording; 9 images -> 5 kept, 4 logos filtered.
- Bug fixed: duplicate check matched neighbouring house numbers (281/283 Dallow Rd) by postcode+address; now skipped when house numbers differ. Exact-reference duplicates still flagged.
- Verified: duplicate by reference, merge without duplicate NCs, hide/restore TrustMark image, deleted-project restore-match, admin recycle bin restore. Test projects purged.
- One-off email delivery test sent to info@networksurveyltd.uk (no reset link, no account created).
- Tap-to-pick DatePickerSheet for scheduled visits.

## Dropbox primary evidence storage (iteration 25, agent-tested)
- storage.HybridStorage: new files -> Dropbox App folder (path remedialflow/projects/<pid>/ncs/<nc>/submissions/<sub>/<fid>/<variant>.<ext>, mode add, autorename off); refs stored as "dbx:<id>"; old Emergent paths still read. No delete/move/overwrite anywhere.
- Admin OAuth PKCE (App key only, no secret): POST /api/admin/dropbox/connect, GET /api/admin/dropbox/callback, GET /api/admin/dropbox/status; refresh token Fernet-encrypted in db.secure_settings. Env: DROPBOX_APP_KEY, DROPBOX_REDIRECT_URI (production must add https://api.<domain>/api/admin/dropbox/callback in Dropbox console + env, then reconnect).
- Chunked resumable uploads for videos (8 MB): /api/uploads, /uploads/{id}/chunk?offset=, /uploads/{id}/complete; verified 125 MB via external ingress.
- If Dropbox not connected, new files fall back to Emergent storage.

## Dropbox folder organisation (iteration 26, preview only)
- Installer photo/video originals: /remedialflow/{Address, POSTCODE}/Media/{stamp}_{ref}_{NC}_{kind}_{fileid}.ext (append-only, incl. discarded/rejected)
- Evidence ZIP export copies: /remedialflow/{Address, POSTCODE}/{Ref}/{Ref}_Evidence_Pack_{stamp}_{6hex}.zip (db.dropbox_exports), download unchanged
- Derived files/report images/imports: /remedialflow/_system/...
- Folder fixed on first use in projects.dropbox_folder; same-address projects share folder; access still per project/NC in Mongo
- Pre-change files left in place and readable. Tested: iteration_26 (15/15 + iter25 regression)

## Auto evidence pack + PDF remediation details (iteration 27, preview)
- recompute_project -> queue_auto_pack(pid, cycle=len(completion_history)) when all NCs approved; db.auto_packs _id "{pid}:{cycle}" (dedupe), statuses pending/running/saved/failed/skipped
- Saved to /remedialflow/{folder}/{Ref}/{Ref}_Evidence_Pack_{stamp}_v{cycle}-{hex}.zip; GET/POST /api/projects/{pid}/evidence-packs[/retry] (staff); startup retry
- NC PDF: "REMEDIATION DETAILS - WORK CARRIED OUT" (export_note or "No details recorded.") before remediation photos
- Tested iteration_27 11/11 + iter26 15/15

## Pre-production reset (done, user-approved)
- All test data collections emptied; kept settings, secure_settings (Dropbox), migrations, decorations, system
- Dropbox /remedialflow (623 test files) deleted with explicit approval; app/connection intact
- DEMO_SEED=false; pre-reset Mongo dump at /tmp/rf_pre_reset_backup (preview server only)
- Permanent Admin info@networksurveyltd.uk (syed) via bootstrap_admin.py with random unknown password; owner sets password via Forgot password email

## Final UI refresh + project-level notifications (iteration 28, preview)
- theme.ts retuned (soft tints + AA on* colours, BORDER=1, radius, system body font), ui primitives/dashboard/jobs/notifications restyled
- Notifications: one per (user, project, category) key "{cat}:{pid}" unique; categories review/escalated/action/approved/assigned/project_ready/completed/rate_changed; refresh_notifications after each recompute
- Temp QA accounts/projects and Dropbox test files removed after verification; only owner admin remains

## Pre-launch Phase 1 + Phase 3 (iteration 29, preview)
- Login demo chips removed; Download evidence (cached by fingerprint, no Dropbox) vs POST /api/projects/{pid}/export-dropbox (idempotent per fingerprint, concurrency claim)
- Notifications: project_ready notification removed (audit only); single "Ready for review" per project; identical re-processing silent
- Review screen: Previous/Next, NC x of y, auto-advance across properties, "All reviews completed"
- New NCs requires_stages: pre/mid/final photos + video mandatory before submit; installer_note; AI statuses (ai_approved, ai_verified_signoff, more_evidence, employee_review, potential_noncompliance, unavailable), requests to installer, works_description -> export_note (source ai, staff edits win), best photo per stage
- PDF: REMEDIAL WORKS CARRIED OUT + one photo per stage; staff pdf_include override (PUT /api/evidence/{fid}/pdf-include); VIDEO EVIDENCE line
- Settings: auto_approve_risks, human_signoff_required
- Pending: Phase 2 (compression/upload UX, native build), Phase 4 (batch upload, web, push)

## Phase 2 + Phase 4 (iteration 30, preview)
- Queue: per-attempt photo compression (expo-image-manipulator, <=2048px, JPEG 0.82, skip <=1280px), "Original quality" option, statuses Pending/Compressing/Uploading/Synced/Failed - Retry; video camera 1080p 8 Mbps; iOS gallery video H.264 1080p export
- Backend variant uploads in parallel; push deep links /job/{pid} | /project/{pid}; hourly deadline reminders (day before scheduled visit)
- Staff batch upload /batch/{pid}: drag-drop (web), pick, NC + stage per file, progress/retry/cancel via shared queue
- Needs native build: compression, camera settings, push (google-services.json)

## Final corrections (iteration 31, preview)
- Installer NC card: generic TrustMark "Required work" boilerplate hidden; Pre/Mid/Final picker + checklist removed; only remediation video (+1 photo) mandatory for new NCs
- PDF: Gemini arrange_photos at pack build (nc.pdf_ai cached by photo set; null stage when unsure; uncertain flag); staff stage labels via PUT /api/evidence/{fid}/pdf-include {stage} win; works description auto-drafted into export_note (source ai) when empty
- Auto-approval: ai.blocked reasons shown to staff; SAFETY_RE on NC text forces human sign-off (AI risk label varied between runs); approved NCs notify installer
- Root cause of "no auto-approval": Admin setting auto_verify = OFF in DB (left as user set it)
- Real Gemini verified in-process on QA clones of real NC-01 evidence (scenarios B, C, D, E, video-missing; A with risk relabelled)

## Iteration 32 — NC interface, project video setting, AI Evidence Guidance (agent-tested, preview)
- Safety Critical toggle (SafetyToggle.tsx): staff toggle on staff NC card via PUT /api/ncs/{id}/safety (audited); orange icon/border/bg when on; installer read-only chip when on; safety guard in run_assessment unchanged
- Duplicate "Required work"/"Required remedial work" box removed from installer + staff NC cards (data kept)
- Project.require_video (default True; older projects treated ON): set at creation (new.tsx), toggle on project page (PATCH /api/projects/{pid}); ncs[].video_required = requires_stages && require_video; submit + auto-approval + Gemini prompt respect it; ai.video_required recorded
- AI Evidence Guidance (backend/guidance.py, app/admin/guidance/*): admin CRUD, approve/disable/soft-delete, versions/history, example photos (re-encoded, no EXIF) in Dropbox _system/guidance; only approved entries matching NC keywords (or measure if no keywords), max 3, sent to Gemini as context (+ up to 2 example images); ai.guidance records {id, version, title}
- Review: optional internal reason + "Suggest as AI guidance" → DRAFT entry (source employee_feedback)
- Tests: iteration_32 23/23; real Gemini: video-OFF photo-only low-risk NC auto-approved (auto_verify temporarily on, restored OFF); approved vent guidance + 1 photo → specific requests

## Iteration 33 — App Store readiness fixes (agent-tested, preview)
- /privacy public in-app privacy policy (app/privacy.tsx); linked from login + all account screens
- Help & contact (mailto info@networksurveyltd.uk, src/support.ts) on login + ProfileView
- Account deletion: POST /api/auth/delete-account (password), DELETE /api/admin/users/{uid}; anonymise_user in auth.py keeps _id (evidence/audit linked as "Deleted user"), revokes sessions, blocks sole-admin self-delete
- app.json ios.config.usesNonExemptEncryption=false; react-native-webview removed

## App Store submission prep
- Public privacy page GET /api/privacy (backend/privacy_page.py, own CSP allowing inline styles); copy in memory/remedialflow-privacy-policy.html
- Submission pack: memory/APP_STORE_SUBMISSION.md (screenshots via TestFlight, review contact, content rights, demo setup)
- Flag: app icon is Emergent template logo -> needs own 1024 icon + new build
- Icon/splash/adaptive/favicon replaced with approved RF design (memory/icon-proposal); iPad support decision pending
- iPhone-only from Build 104 (ios.supportsTablet=false)
- Deploy fix: removed .env ignore rules from .gitignore (deployment needs backend/.env + frontend/.env)

## Location-aware PDF — Phase 1 (scope foundation, preview only, flag OFF by default)
- backend/scope.py: ncs.scope schema, rule_extract + Gemini ai_extract (quote-verified), merge, coverage (complete/incomplete/unverified via files.location_id), gate_reason (blocks auto-approval only), GET /api/ncs/{id}/scope, POST /api/ncs/{id}/scope/extract
- Settings.location_scope_enabled (default False); run_assessment hook only when enabled; guidance.scope_type optional
- StaffNCCard read-only scope line; tests/test_phase1_scope.py (15 pass)

## Location-aware PDF — Phase 2 (photo classification, preview only, flag OFF by default)
- backend/photo_class.py: files.classification {location_id/label/group/uncertain, component, stage before|during|after|verification|uncertain, relevant, dup_group/dup_of, phash, source ai|staff|mixed, staff overrides}; files.location_id mirror
- Gemini per-NC batch (16) + deterministic build(): union of AI-declared same-location links only, labels must be scope labels (never invented), contradictory groups -> uncertain, duplicates = same location + stage + component + dHash<=6
- Settings.photo_classification_enabled (False); background task after run_assessment when enabled; endpoints GET /api/ncs/{id}/photos/classification, POST /api/ncs/{id}/photos/classify (409 off), PUT /api/evidence/{fid}/classification (staff wins)
- tests: tests/_loop.py shared loop fixes combined-suite errors; test_phase2_photos.py; combined phase1+iter34+phase2 = 38 passed

## Phase 2 real-photo validation (preview, read-only) — Jun 2026
- Dry run on 46 real preview photos (3 NCs, 280 Dallow Road); no DB writes, flags stayed off.
- Fixes: door "threshold sill" no longer read as a window (scope.py); "front of the side elevation" = one elevation (scope.py);
  later batches (>16 photos) now get up to 6 reference photos from earlier batches so locations/component names link across batches (photo_class.py).
- Tests: 58 passed combined (1 intermittent API login-test failure seen once, passed on re-run). QA data removed; real files classified 0.

## Phase 3 — Coverage-gated assessment (preview only, flag OFF) — Jun 2026
- New `backend/coverage.py`; flag `Settings.coverage_check_enabled` (default False). Hook in `server.run_assessment`.
- complete / incomplete / unverified from Phase 1 scope + Phase 2 tags. Only "after"/"verification" photos demonstrate work;
  one location counts once (location_id or location_group); untagged photos only raise max-possible coverage.
- Incomplete (confirmed) + AI verified/more-evidence + not safety -> existing "More evidence required" with named missing locations.
  Unverified -> Employee Review. Never approves by itself. Test/measurement/certificate NCs need a verification photo.
- `ncs.coverage` stored (hidden from installers); GET /api/ncs/{id}/coverage (staff, read-only).
- Tests: 80 passed serially (`-n 0`, needed: xdist workers race on global settings flags). Real-photo dry run: 3 NCs, no writes.

## Phase 4 — Smart PDF photo selection (preview only, flag OFF) — Jun 2026
- New `backend/smart_pdf.py`; flag `Settings.smart_pdf_enabled` (default False). Hook in `server.pdf_photo_groups`.
- Used only when every candidate photo already has Phase 2 tags and staff haven't hand-picked photos; otherwise existing behaviour.
  No AI calls in the smart path. No per-NC cap. Sections: location (scope order) -> stage (before/during/completed/measurement/supporting);
  unnamed groups "Location not identified (n)"; untagged "Other photographs". Duplicates dropped only if Phase 2 dup_of target is shown
  and staff didn't force-include. Captions from stored labels only. One coverage line for multi-location NCs.
- `evidence_pack.nc_pdf` accepts optional (jpeg, caption) entries and `coverage_note`; legacy input renders as before.
- Tests: test_phase4_smart_pdf.py 10 passed; consolidated Phases 1–4 + iter34/35 API: 82 passed (real-AI tests skipped for credits).

## Phase 5 — Staff review controls (preview only, flag OFF) — Jun 2026
- New `backend/staff_review.py` (flag `staff_review_enabled`): GET /api/ncs/{id}/evidence-review, POST .../evidence-review/classify
  (explicit, skips AI when nothing new unless force), PUT /api/ncs/{id}/scope (staff count + labels + required basis; history kept).
  Photo corrections reuse PUT /api/evidence/{fid}/classification and /pdf-include.
- Phase 3 evaluate now only re-classifies when untagged photos exist. Phase 4 staff stage correction wins in PDF.
- Pack fingerprint includes tags + scope only when smart_pdf_enabled -> corrections produce a new pack version; old versions untouched.
- Frontend: `src/components/EvidenceReview.tsx` in StaffNCCard (renders nothing when flag off). No installer changes.
- Tests: test_phase5_staff_review.py 8 passed; consolidated Phases 1–5 + iter34/35: 90 passed.

## Pre-production integration check (preview) — Jun 2026
- Browser check of staff panel with all 5 flags on (synthetic QA data, metadata only); flags restored OFF, QA data removed.
- Changes: location/stage now dropdown sheets + PDF include chip in panel; review shows labels from previewed scope;
  smart PDF uses rule-based scope when none stored (`smart_pdf.scope_for`); coverage note now also for unknown count /
  unconfirmed single-location NCs. Phase 4+5 tests: 18 passed.
- Enable/disable: PUT /api/settings (admin, audited) with the 5 flags; recommended next: one combined toggle in existing Admin Settings.

## Location-Aware Evidence master switch — Jun 2026
- Admin Settings -> EVIDENCE ACCEPTANCE: "Location-Aware Evidence" switch (src/components/LocationEvidenceSwitch.tsx).
- GET/PUT /api/settings/location-evidence (admin only): sets all 5 flags together, state on/off/partial, audited
  (settings.location_evidence_enabled / _disabled). PUT /api/settings now never changes the 5 flags.
- Tests: tests/test_location_evidence_switch.py 4 passed. Flags OFF.
- Open finding (not changed): coverage note only printed on the smart-PDF path; staff hand-picked photos or untagged photos
  fall back to legacy PDF with no coverage line even if nc.coverage is incomplete/unverified.

## PDF coverage safeguard — Jun 2026
- With smart_pdf_enabled (Location-Aware Evidence ON) `server.pdf_photo_groups` sets one `coverage_note` on EVERY path
  (smart or existing layout, incl. staff hand-picked). Wording (`smart_pdf.coverage_note`): complete multi -> "Evidence shown for all N …";
  complete single -> none; incomplete -> "…X of N… Outstanding: …"; unverified -> "Evidence coverage has not been confirmed.";
  any untagged photo / no scope -> "Evidence coverage has not been assessed." Switch OFF -> unchanged.
- Tests: test_pdf_coverage_safeguard.py (12) + Phase 4/5 targeted = 29 passed.

## Concise evidence PDF fix — Jun 2026
- Both PDF layouts: ONE clearest photo (evidence_pack.best, sharpness) per evidence step — per stage, or per (location, stage) for
  confirmed multi-location NCs. Staff-picked (pdf_include True) photos always kept; excluded never shown. AI "extra" photos dropped when
  near-identical (dHash <= 10) to a shown photo. TrustMark originals: near-identical copies shown once.
- No internal wording in PDFs: coverage line removed (coverage stays in staff panel); no "Location not identified" headings;
  captions only for measurement/test component. Single-photo steps laid out two per row.
- Stamped (timestamped) copy still preferred. No AI calls added. Samples: /app/test_reports/samples/*.pdf.
- Tests: test_concise_pdf.py (10) + phase3/4/5 targeted: all passing.

## Knowledge Index Phase 1 — NC checklist, best-photo marking, profiles (preview, switch OFF) — Jun 2026
- New `backend/checklist.py`: ncs.checklist (draft|confirmed|auto; source ai|profile|staff; items with stage, locations,
  measurement, criterion, sources {guidance_id, doc_id, doc_version, clause, page}); files.checklist_item (staff best photo);
  db.learning_examples (every marking); db.nc_profiles (versioned: proposed -> approved -> superseded/retired).
- Profile match: 5 rule signals (defect, component, requirement, measure, location); component must overlap;
  confident (>=4) -> checklist auto-filled, no AI; uncertain (2-3) -> draft + staff review; none -> ONE AI draft.
- Assessment hook (switch ON, approved branch only): uncertain match / missing items on confirmed|auto checklist -> staff review.
  Unconfirmed AI drafts never judge. Never approves.
- PDF (switch ON + checklist): one photo per requirement (staff-marked first, else matched stage/location), caption = requirement;
  missing silently omitted; falls back to concise rules. Pack fingerprint includes checklist + markings.
- Endpoints: GET/PUT /api/ncs/{id}/checklist, POST .../checklist/draft, POST .../checklist/save-profile,
  PUT /api/evidence/{fid}/checklist-item, GET /api/admin/profiles, PUT /api/admin/profiles/{id}/status.
- Frontend: src/components/ChecklistPanel.tsx (in EvidenceReview, + "BEST FOR…" photo picker), app/admin/profiles.tsx,
  link on admin guidance screen. Tests: tests/test_checklist.py (8); consolidated targeted 64 passed. Browser-checked.


## Knowledge Index Phase 2 — Master Documents Library (preview, switch OFF) — Jun 2026
- New `backend/documents.py`: db.tech_docs (revision per upload: key lineage, rev, title, publisher, version, pub_date, category
  pas|trustmark|building_regs|manufacturer|other, status draft|approved|superseded|retired, history), db.tech_doc_pages (PyMuPDF text
  per page, Mongo text index; no AI on upload). PDF stored via existing HybridStorage (files kind=techdoc, signed file_url).
- Retrieval (keyword, approved only): when switch ON — assessment adds extracts to AI guidance text; AI checklist draft gets [D#]
  extracts, items cite them -> sources {doc_id, doc_version, clause, page}; AI measurements not in NC/approved text are removed + noted.
  Clause numbers only read from page text. Flags: missing (shown only) / conflicting values across docs (-> staff review) /
  checklist measurement not found. Optional staff "AI search" = ONE call that can only pick among keyword candidates.
- Admin "Draft guidance" from ≤6 chosen pages = ONE AI call -> DRAFT db.guidance (source.type=document); invented clauses dropped.
- Endpoints: POST/GET /api/admin/documents, GET/PUT /api/admin/documents/{id}, PUT .../status, POST .../replace,
  GET .../search?q=, POST .../draft-guidance, GET /api/documents/{id}/pages/{n} (staff), GET /api/ncs/{id}/references,
  POST /api/ncs/{id}/references/refresh {ai}.
- Frontend: app/admin/documents/index.tsx + [id].tsx, src/components/DocUpload.tsx, ReferencesPanel.tsx (in EvidenceReview),
  checklist item source lines + notes; link from AI Guidance screen. Installer UI unchanged.
- Tests: tests/test_documents.py (7) + test_documents_http.py (12, testing agent) + targeted regression = 73 passed.
- Limitations: no OCR for scanned PDFs; no document delete (append-only); library admin screens usable while switch OFF
  (no effect on assessments); keyword retrieval may miss differently-worded passages.

## Knowledge Index Phase 3 — AI photo recommendations (preview, switch OFF) — Jun 2026
- New `backend/recommend.py`: ncs.photo_recs (cached by checklist v + photo ids + photo tags + profile version). Staff press only.
  Needs confirmed/auto checklist. Rules first (unique unmarked photo with matching stage/location, staff-rejected excluded);
  ONE AI call only for unsettled requirements (≤12 NC photos + ≤4 approved-profile good/bad example images + approved doc extracts).
  Staff-marked photos always win and are never sent to AI. Suggestions never mark photos / change PDF or coverage until Accepted.
- Flags per requirement (staff attention): no photo, AI unsure, measurement/test must be checked by staff, no approved example.
- Actions: PUT /api/ncs/{id}/recommendations/{item} accept|reject|replace -> normal staff marking + learning_examples
  (accept_rec/reject_rec/replace_rec with rejected_file_id). POST /api/ncs/{id}/recommendations (run, {force}),
  GET same, POST .../propose-profile -> NEW proposed profile version (examples + bad_examples merged with approved base;
  repeat press updates the same pending proposal). Only APPROVED profiles supply examples.
- Frontend: src/components/RecommendationsPanel.tsx in EvidenceReview; admin profiles show good/rejected counts + "Update of vN".
- Tests: test_recommend.py (9) + test_recommend_http.py (10, testing agent) + regression; 70 + 22 passed.

