# RemedialFlow Upgrade — Implementation Plan (awaiting approval)

## Current state (inspected 2026-10-19)
- Backend: FastAPI `server.py` (routes), `auth.py` (JWT + rotating refresh), `core.py` (models, signed URLs, hash-chained audit), `storage.py` (provider abstraction), `ai.py` (Gemini assessor + PDF extractor), `media.py` (stamping, ffmpeg frames, PDF image extraction).
- Frontend: Expo Router; installer tabs Jobs/Earnings/Profile; staff tabs Projects/Review/Account; camera.tsx, video.tsx, offline queue (`src/queue.tsx`).
- Data: 12 projects, 45 NCs, 69 evidence files, 9 submissions, 180 audit entries, 5 users, 2 earnings, 8 PDF imports.
- Current statuses — NC: open, assessing, ai_verified, more_evidence, manual_review, approved, rejected. Project: active, awaiting_review, completed.
- Reusable as-is: auth, storage, signed URLs, audit chain, stamping, offline queue, PDF extraction, AI pipeline, camera.

## Unified status model (used everywhere)
- **NC status:** `open` → `assessing` (AI Checking) → `more_evidence` | `employee_review` | `admin_review` | `approved` (Internally Approved; `approved_by` = "ai" or a user id) | `rejected`.
  - TrustMark rejection = `rejected` with `rejection_source = "trustmark"`.
- **Project lifecycle** (derived, stored): `unassigned` | `in_progress` | `completed` | `trustmark_resolved`.
- **Separate attention flag:** `needs_review` is true when any NC is in employee/admin review. Counters never double-count.
- **History fields:** `completed_at` (first completion), `completion_history[]`, `resolved_at`/`resolved_by`, `reopen_history[]`.

## Database migrations
These run once and are idempotent. They are additive only and never delete anything.
- **M1 (NCs):**
  - `assigned_installer_id` = the project's `installer_id`.
  - `assignment_history = []`.
  - `ai_verified` → `approved` (`approved_by: "ai"`).
  - `manual_review` → `employee_review`.
  - `original_text` = current description (exact wording kept).
- **M2 (projects):** recompute lifecycle and `needs_review`, then backfill `completed_at` from audit/earnings.
- **M3 (users):** `pay_type` = `per_job`, plus `default_rate` from settings.
- **M4 (earnings):** convert to per-(project, installer) scope with `agreed_rate`, `status` (pending/earned/paid) and `week`.
- **M5 (PDF images):** compute perceptual hash and size for existing `pdf_import` files, and flag logos/repeated images as `excluded: true`. Files stay in storage.
- **M6 (settings):**
  - `gallery_policy` → `accept`.
  - Add `auto_approve_low_risk`.
- New collections:
  - `notifications`
  - `migrations` (records which migrations have run)

## Phases (one at a time; each ends with testing-agent verification and your approval)
- **P0 Security fixes from the audit:**
  - Demo reset code hidden unless the server is in local dev mode.
  - Seed no longer resets changed passwords.
  - Strict CORS allowlist.
  - client_id string coercion.
  - Settings visible to staff only.
- **P1 Dashboards and project details:**
  - 5 counters: Unassigned / Work in Progress / Awaiting Review / Completed / TrustMark Resolved.
  - Save a project without an installer.
  - Mark TrustMark Resolved (manual, audited) and Reopen.
  - Mobile project cards with one page scroll (table kept for wide screens).
  - New project header with a compact action menu (PDF, Export, Audit, Add NC).
  - NC cards show full original wording.
- **P2 NC-level assignment:**
  - Assign a project to an installer; this defaults all of its NCs.
  - Reassign individual NCs, recorded in audit and history.
  - Installer APIs return only that installer's own NCs (enforced on the backend).
  - Notification for reassignment.
- **P3 AI and approval:**
  - Practical assessor prompt with risk class (low / standard / safety).
  - Auto-approve low-risk when confidence ≥ threshold and the admin toggle is on.
  - Gallery photos and missing GPS are flagged but don't escalate on their own.
  - Safety-critical NCs always go to employee review.
  - Escalate to Admin (employees only), plus an Admin Review queue with Approve / More / Reject / Return.
- **P4 Review queue:**
  - Grouped by project, with search.
  - Opening a project shows only its pending NCs and auto-advances to the next one.
  - Readable action buttons; the note stays optional.
- **P5 Installer workflow and notifications:**
  - Sections: New / Active / Action Required / Completed.
  - Rejections show their source (Employee or TrustMark).
  - In-app notification centre with unread badge, deduplication, and tap to open the NC.
- **P6 Camera and viewer:**
  - Camera: pinch zoom, flash, autofocus, tap-to-focus.
  - Full-screen viewer: swipe, pinch/double-tap zoom, counter, no metadata panel for installers. Metadata remains for staff audit.
- **P7 PDF extraction:**
  - Logo/decoration filter (repeat detection plus size/aspect heuristics), also applied to existing projects without re-upload.
  - Extraction summary ("10 NCs detected").
  - Duplicate project detection, with a merge/update flow that confirms ambiguous NC matches.
- **P8 Evidence Pack:**
  - `{REF}_Evidence_Pack.zip` with one PDF per NC (built with PyMuPDF, already installed).
  - Videos/ folder.
  - Best-photo selection: near-duplicate removal, sharpness score, and an AI ranking pass. Excluded photos stay in storage.
- **P9 Earnings:**
  - Per-user pay type: per job or daily wage.
  - Per-job agreed rate, with admin override.
  - Each installer earns when their own scope is approved, with no duplicates.
  - Weekly summaries; admin can mark paid by week.
- **P10 Work Tracker:**
  - Weekly, monthly and custom ranges.
  - CSV and Excel export (openpyxl).
  - Ageing sorts.
- **P11 Offline hardening:**
  - Duplicate-safe sync (already idempotent via client_id).
  - Integrity checks; approved evidence can't be deleted.
- **P12 Admin account cleanup:**
  - Advanced section for Export JSON.
  - Typography pass that replaces small monospace body text.

## Platform limits to know
- **Tap-to-focus / exposure:** expo-camera (works in Expo Go) supports autofocus, zoom, flash and a refocus-on-tap workaround, but has no true focus-point or exposure API. Real point-focus and exposure need react-native-vision-camera, which only works in a development/production build (not Expo Go).
- **Push notifications:** they need the Emergent push integration, `google-services.json` from Firebase and a native build. In-app notifications work everywhere.
- **Scope:** this is a large upgrade, so it will run over several sessions, one phase per session.
