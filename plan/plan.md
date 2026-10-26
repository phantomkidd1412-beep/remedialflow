# RemedialFlow — NC Evidence Knowledge Index & Master Documents Library

Each NC gets its own staff-confirmed evidence checklist, and staff mark the best photo for each requirement. Those choices become reusable NC-type profiles. A separate library of technical guidance PDFs gives the AI relevant passages during assessments.

## Who it's for
- **Admins:** upload and approve technical documents, approve reusable NC-type profiles and control the feature switches.
- **Employees (staff reviewers):** confirm checklists, mark the best photos and accept or correct AI suggestions.
- **Installers:** see no change. They get no new screens, buttons or requirements.

## Audit findings (current code, read-only)
| Area | What exists today | Gap |
|---|---|---|
| Document uploads and storage | TrustMark report PDF import with text extraction and AI parsing, Dropbox/system storage, and up to 6 example photos per guidance entry | No general technical-document upload |
| Indexing and retrieval | The AI Guidance Library holds typed, admin-approved entries (evidence required, photos to collect, references…). Up to 3 entries are matched by keywords or measure. | No PDF documents, no passage index and no page citations |
| AI uses guidance in assessments | Yes. Approved guidance entries (up to 3) are added to the assessment prompt. | Only typed entries, no document passages |
| Persistent per-NC checklist | No. Each NC stores a location scope (Phase 1) and an AI assessment result only. | Needs a checklist per NC: requirements, measurements, completion criteria and references |
| Photo sorting and coverage use a checklist | Coverage uses locations, stages and a simple "test/measure" keyword check | Must also check each checklist item |
| PDF selection by requirement | One clearest photo per stage, or per location and stage. Staff "include/exclude" is respected. | Needs one photo per checklist item, with staff-marked photos taking priority |
| Learning from staff | Employee feedback can draft guidance entries, which an admin approves | No store of staff-approved photo examples and no NC-type profiles |

**Reused, not rebuilt:**
- Location scope (Phase 1)
- Photo sorting into location, stage and duplicates (Phase 2)
- Coverage rules (Phase 3)
- One-photo-per-step PDF selection and layout (Phase 4 and the concise-PDF fix)
- Staff review panel, staff corrections and the "Location-Aware Evidence" switch (Phase 5)
- AI Guidance Library, guidance matching and audit log

**Areas that change:**
- **Backend:**
  - Checklist and profile records
  - Assessment hook in the existing assessment flow
  - Coverage rules
  - PDF photo planner and pack selection
  - Guidance matching (to add document passages)
  - New document library module
- **Frontend (staff and admin only):**
  - Staff review panel, extended with checklist items and best-photo marking
  - NC card (to show the checklist)
  - Admin guidance screens, extended with a Documents tab and a Profiles list
- **Unchanged:** installer screens

## Core features and experience
1. **NC evidence checklist.**
   - Each NC gets a short list of requirements, for example: "Before: gas tee and clearance visible", "Measurement: tape showing ≥250 mm above tee", "Completed: vent cover fitted".
   - Each item has a stage (before, during, completed, measurement/test), the location(s) it applies to, any required measurement and a short completion criterion.
   - For NC types with no profile yet, the AI drafts the checklist from the NC wording, the TrustMark report text and matching approved guidance. Staff confirm or edit it before it's used.
   - The checklist stays with the NC and every edit is recorded.
2. **Staff best-photo marking.**
   - In the existing review panel, staff tap a photo and assign it to a checklist item: Before, During, Completed, Measurement/Test or Exclude.
   - Marked photos override any AI choice. Original photos are never altered.
3. **Checklist-driven PDF.**
   - The evidence pack shows one photo per checklist item, using the staff-marked photo first, otherwise the best AI-matched photo.
   - Remaining steps fall back to the current one-per-stage rule.
   - The existing layout, timestamps, TrustMark photos and "no internal wording" rule are kept.
   - Checklist items with no photo are never printed as complete.
4. **Coverage per checklist item.**
   - An item counts as covered only by a staff-marked or confidently matched photo.
   - A missing measurement/test item, a safety-critical NC or uncertain evidence always goes to staff review.
   - Coverage never approves an NC on its own.
5. **Reusable NC-type profiles.**
   - When staff finish marking an NC, they can save it as a profile: NC wording pattern, checklist, linked guidance and the staff-approved example photos for each item.
   - An admin approves a profile before it is reused.
6. **AI photo recommendations (later phase).**
   - For new NCs matching an approved profile, the AI suggests the best photo per checklist item using the stored examples and the guidance.
   - Staff accept or correct the suggestions, and corrections update the stored examples.
   - The model is never retrained.
7. **Master Documents Library (later phase).**
   - Admins upload guidance PDFs and their text is indexed by page.
   - Assessments and checklist drafting receive the most relevant passages, with document and page citations shown to staff.
   - From a document, the AI can draft new guidance entries for admin approval.
   - Technical guidance and evidence-selection profiles are kept separate.

## User flow
1. An installer submits evidence as today.
2. If the switch is ON, the NC has a checklist: taken from an approved profile, or AI-drafted and marked "needs staff confirmation".
3. A staff member opens the NC, confirms the checklist and marks the best photo for each item, or excludes photos.
4. The panel shows covered and missing items. Missing items can be requested from the installer through the existing "More evidence required" message.
5. Staff approve the NC through the existing review controls. The approval rules are unchanged.
6. The evidence pack prints one photo per checklist item.
7. Optionally, staff save the NC as a profile. An admin approves it, and future matching NCs start from that checklist.

## UI/UX feel
- **Same staff panel:** the checklist appears inside the existing staff review panel as a compact list with a tick or a gap marker per item.
- **One tap per photo:** a dropdown sets the checklist item or Exclude, matching the current location and stage pickers.
- **Admin screens:** Profiles and Documents are new tabs beside the existing Guidance Library, with the same look.
- **Nothing for installers:** no new text or steps.
- **Confirmation step:** AI drafts are always labelled "AI draft — needs confirmation" until a staff member confirms them.

## Approved requirements for Phase 1
- **Ready for the documents library:** every checklist item and profile can hold a guidance source: document ID, document version, clause reference and page number. In Phase 1 these come only from existing guidance entries, or staff type them. There are no document uploads.
- **Staff effort shrinks over time:** staff mark photos only until approved examples exist. Once an NC confidently matches an approved profile, its checklist is filled in automatically and the system's existing fixed selection rules are applied: staff-marked photos first, then the stored photo stage and location, then the clearest photo. Staff then only review exceptions: a missing measurement, a safety-critical NC, uncertain evidence or an uncertain profile match. Phase 1 does **not** claim to recognise the best photo by comparing it with staff-approved examples; that capability arrives only in Phase 3. Staff-marked photos always take priority, and the existing photo-selection fallback stays available.
- **Corrections are saved but don't change profiles:**
  - Every staff correction is saved as a learning example (which photo, which checklist item, what was corrected).
  - An approved profile is never changed automatically. Corrections build up as a proposed improvement that an admin reviews and approves, creating a new profile version. Earlier versions are kept.
- **Profile matching uses five signals:** defect type, affected component, technical requirement, measure and location context. Similar wording alone never counts as a match. Matches are rated:
  - **Confident:** the profile is used.
  - **Uncertain:** staff must confirm it.
  - **No match:** a new checklist is drafted.
- **PDF:** one photo per distinct checklist requirement. Two measurements, or two locations, mean two photos, even within the same stage. The planner applies these in order:
  1. Staff overrides come first, with include/exclude always respected.
  2. The timestamped copy of each photo is used.
  3. Captions are short and name the requirement shown.
  4. Requirements with no photo are left out silently, with no "complete" claims.
  5. No internal AI wording appears in the PDF.
- **Credit control:** a checklist is drafted with one AI call only for an NC with no confident profile match. Matched NCs reuse the profile and existing photo sorting at no AI cost. Historical NCs are never processed in bulk.
- **Unchanged:** approval rules, safety safeguards, employee sign-off and installer workflows.

## Files to change in Phase 1
- **New backend module:** NC checklists, learning examples, NC-type profiles and profile matching. Staff and admin access only.
- **Backend, existing files:**
  - **Assessment hook:** attaches a checklist when the switch is ON, sending uncertain matches and incomplete required items to staff review.
  - **Settings:** checklist and profile data stored on the NC, with no new switch.
  - **Coverage rules:** covered or missing checked for each checklist item.
  - **Location-aware PDF planner:** one slot per checklist requirement.
  - **Existing-layout PDF path:** uses the checklist when one exists, otherwise current behaviour.
  - **Staff review module:** marking a photo against a checklist item records a learning example.
  - **Guidance matching:** lets profiles reference guidance entries.
- **Frontend (staff and admin only):**
  - **Staff review panel:** checklist list, a picker to assign each photo to a requirement, and confirming the profile match.
  - **New admin screen:** NC-type profiles, with approval of proposed improvements and version history.
  - **Admin guidance screen:** a link to the profiles screen.
- **Tests:** a new synthetic test file for checklists, matching, learning examples and checklist-driven PDFs.
- **Not touched:** installer screens, upload flow, Dropbox storage, the desktop project and existing guidance records.

## Compatibility confirmation
- **Existing NCs:** they get no checklist until the switch is ON and staff open them, or a new assessment runs. Without a checklist they behave exactly as today.
- **Photographs and upload history:** never changed. Learning examples only store references to them.
- **Approvals:** existing decisions are never re-run or changed. New assessments follow the same approval rules, and a checklist can only send an NC to staff review, never approve it.
- **PDF downloads:**
  - **Switch OFF:** identical to today (the concise-PDF rules).
  - **Switch ON, NC with no checklist:** also identical to today.
  - **Saved packs:** existing ones are not regenerated.
- **Switch:** the "Location-Aware Evidence" switch stays OFF. Nothing is activated, deployed or published.

## Implementation phases
**Phase 1 — MVP (built now): NC checklist, staff marking and checklist-driven PDF**
- **Checklists:** a checklist per NC, AI-drafted from the NC wording, TrustMark text and matching approved guidance, with staff confirmation and edit history.
- **Photo marking:** staff assign the best photo per checklist item in the existing review panel.
- **Coverage:** checks each checklist item. A missing measurement/test item, safety or uncertainty routes to staff review.
- **PDF:** one photo per checklist item, staff-marked first, falling back to the current rules.
- **Profiles:** "Save as profile" stores the checklist and example photos. An admin approves it, and approved profiles pre-fill the checklist for matching new NCs.
- **Matching and learning:** profiles are matched on the five signals, and uncertain matches need staff confirmation. Staff corrections are saved as learning examples and become proposed profile improvements, which an admin approves as a new profile version.
- **Guidance sources:** checklist items and profiles can hold guidance sources (document ID, version, clause, page), ready for Phase 2.
- **Switch:** all of this sits behind the existing "Location-Aware Evidence" switch, OFF by default.

**Phase 2 — Master Documents Library**
- Admins upload PDFs with text indexed by page, and documents are approved before use.
- The most relevant passages, with citations, feed into assessments and checklist drafting.
- The AI can draft guidance entries from a document for admin approval.

**Phase 3 — AI photo recommendations from profiles**
- For NCs with a matching profile, the AI recommends a photo per checklist item, compared against the stored staff-approved examples.
- Staff accept or correct each recommendation, and corrections update the examples.
- A small report shows how often recommendations were accepted.

## Assumptions
- **One switch:** the existing "Location-Aware Evidence" switch also controls these features, with no separate switch.
- **Credits:** checklist drafting costs one AI call per NC, only when staff open the review panel or when the switch is ON during an assessment. Historical NCs are never processed in bulk.
- **Profile matching:** a new NC is matched to a profile using the same keyword and measure approach as today's guidance matching, plus the NC's location type. No new search service is added in Phase 1.
- **Example photos:** these are references to existing evidence files, not copies. Original evidence is never moved or changed.
- **No retraining:** stored examples are only shown to the AI as context (in Phase 3), never used to train the model.
- **Unchanged rules:** NC approval decisions, safety rules, employee sign-off and installer workflows stay as they are.
- **Staff priority:** staff-marked photos always win over AI selections in both the PDF and coverage.
- **Existing packs:** these are not regenerated. New packs use the checklist only when the switch is ON.
- **Desktop app:** the new staff and admin screens need the same small desktop update as Phase 5, made after approval.
- **No deployment:** nothing is deployed, published or rebuilt without separate approval.
