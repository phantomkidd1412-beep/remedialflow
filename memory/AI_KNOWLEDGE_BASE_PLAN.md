# RemedialFlow — AI Knowledge Base & Location-Aware Evidence PDF (DESIGN ONLY, not implemented)

Status: design approved for planning. No code, AI, PDF or deployment changes have been made.

## 0. Current behaviour (verified in code) — why it falls short
- `backend/ai.py` `PHOTO_SYSTEM` / `arrange_photos()` chooses **at most ONE photo per stage** (pre/mid/final) plus up to 2 "extra".
  That caps an NC at ~5 photos, so it shows one representative window or room instead of covering every location.
  This conflicts with the coverage requirement and probably explains part of the "PDF changed" report.
- `server.py` (evidence pack ~L1790–1830) caches the arrangement per NC in `ncs.pdf_ai` (keyed by the evidence set).
  Staff overrides are `files.pdf_include` (in/out/auto) and `files.stage` with `stage_source=staff`.
- The assessment (`run_assessment`) checks overall sufficiency, but it does not count locations.

## 1. Knowledge Base (extends the existing AI Evidence Guidance library)
New optional fields on `guidance` entries (admin-approved, versioned like today):
- `scope_type`: per_window | per_habitable_room | per_wet_room | per_elevation | per_junction | per_component | single
- `location_label`: wording for each location, e.g. "Window {n}", "Bedroom {n}", "Front elevation"
- `stages_required`: subset of [pre, mid, final, verification], each marked mandatory or optional
- `count_hint`: how to work out the number of locations (e.g. "count windows listed in NC/report", "habitable rooms in property")
- `duplicate_rule`: what counts as a duplicate for this measure (default: same location + same component + same stage + near-identical view)
The seed library starts EMPTY. Admins write and approve every entry; the AI never invents standards.

## 2. Pipeline (server-side only; installer workflow unchanged)
Step A — Scope (once per NC, cached):
- Input: original TrustMark NC text and the report page for that NC (already stored), plus the matched approved guidance.
- Output `nc.scope = {scope_type, expected_count | null, expected_labels[], source: "nc_text"|"report"|"guidance"|"unknown", confidence}`.
- If the count can't be established reliably: `expected_count = null`, flagged "coverage unverified". Coverage is never assumed complete.

Step B — Per-photo understanding (batched, cached per file hash):
- For each photo: `{location_label, component_id (e.g. "window: kitchen, left"), stage, quality, view_signature, notes}`.
- Location cues: content, NC wording, installer note, capture order/time and GPS cluster (all already captured; no new installer input).

Step C — Grouping and de-duplication (deterministic code, not the AI):
- Group by `location_label` → `component_id` → `stage`.
- Within one group, keep the best photo. A photo is a duplicate ONLY IF it has the same location, component and stage and a near-identical view
  (perceptual-hash distance below the threshold AND the AI says "same view"). Different locations are never duplicates.
- Excluded duplicates are kept, with the reason and a link to the photo kept.

Step D — Coverage check:
- Compare the locations covered against `expected_count` / `expected_labels`, and the stages covered against `stages_required`.
- Output `nc.coverage = {covered: n, expected: m|null, missing_locations[], missing_stages{loc: [...]}, uncertain[]}`.
- When something is missing, or the expected count is unknown, the NC is NOT auto-approved. Existing rules apply: AI requests more evidence,
  or it goes to staff review. Thresholds and safety rules are not weakened.

Step E — Installer message (existing "More evidence required" flow only):
- Plain wording, e.g. "Please add a completed photo of the end cap on 2 more external windows (Window 7 and Window 8)."

## 3. PDF organisation
- One NC section, then one sub-section per location, in a stable order (expected_labels order, then by capture time):
  "Window 1 — Before / After", "Bedroom 2 — Completed ventilation".
- Each location shows only the stages it needs; no blank stage boxes; one short works description per NC (no repeated text per location).
- If coverage is incomplete or unverified, a clear line in the NC section: "Evidence shown for 6 of 8 windows — 2 outstanding" or
  "Number of affected locations could not be confirmed". The PDF never implies full coverage.
- TrustMark reference photos stay separate, as today. Download Evidence, Export to Dropbox and duplicate-export prevention are unchanged.

## 4. Admin / staff review (staff NC card; no installer UI)
- A "PDF evidence plan" panel listing:
  - the locations identified (editable labels, add/remove a location, set the expected count)
  - the photos chosen for each location and stage
  - missing or uncertain locations
  - photos excluded as duplicates (one tap to restore)
- Staff edits are stored as overrides (`files.pdf_location`, `files.stage` with `source=staff`, `ncs.scope_override`). They always beat the AI,
  and the AI never overwrites them.
- Audit events for every override. The AI result is versioned (`pdf_ai.version`, model, guidance ids used).

## 5. Safety and compatibility
- Historical PDFs and NC decisions are not regenerated or changed. The new logic applies only when a pack is next built, and existing staff overrides are kept.
- If the AI is unavailable, fall back to today's arrangement plus the "coverage unverified" note.
- Cost and latency: Step B is cached per file, and Steps A and C are cheap. Batch up to about 16 images per call.
- No new installer screens, fields or buttons.

## 6. Implementation phases (each tested in preview before any deployment)
1. Data model and Knowledge Base fields; scope extraction (Step A) and a read-only coverage panel for staff.
2. Per-photo location tagging plus de-duplication (Steps B and C), with excluded-duplicate review and restore.
3. Coverage-gated assessment and specific installer requests (Steps D and E), with no threshold changes.
4. Location-organised PDF layout and incomplete-coverage wording.
5. Staff override editing for locations, scope and include/exclude, plus audit.
Test scenarios: 8-window end caps with 6 documented; ventilation in 4 habitable rooms; 2 wet-room fans; EWI on 2 elevations;
near-identical duplicates at the same window; similar vents in different bedrooms (must both be kept); count unknown.
