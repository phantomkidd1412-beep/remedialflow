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
