# Extension Enhancements Backlog

This document is the implementation backlog for the Job Autofill Copilot
browser extension. It records what was observed during the first live
Greenhouse test (Canonical application) and defines the next changes in
small, testable parts.

## Product guardrails

- Never invent facts, experience, qualifications, demographic information, or
  consent responses.
- A user must review all generated and filled values before submitting an
  application. The extension must never submit an application on the user's
  behalf.
- Treat a user's manually written answer as private data. Only save it to
  reusable memory after an explicit user action.
- Sensitive, voluntary, or legal/compliance fields require an explicit saved
  template value and must otherwise remain unfilled.

## Current live-test baseline

- The extension opens, syncs the signed-in user, reads the selected resume and
  template, scans a live Greenhouse application form, and fills standard text
  fields.
- The Canonical test filled **6 of 42** detected fields: first name, last name,
  email, country code, phone, website, and LinkedIn profile.
- The panel currently displays only an aggregate count (`Filled 6/42`). It does
  not show which fields were filled, skipped, refused, or failed to inject.
- The backend's form-understanding and answer-generation endpoints are reached
  by the live extension. The backend's existing zero-hallucination refusal
  behavior is working as intended when it lacks grounded evidence.
- Ashby live test: **4 of 11** fields filled. Email was correct; an open-text
  startup-experience answer was generated; the single Name field was skipped;
  LinkedIn was filled with a placeholder value rather than the parsed resume
  value; resume upload was skipped.
- Workday live test: the current extension did not fill any observed fields.

## Enhancement matrix

| ID | Area | Current limitation | Enhancement | Status |
|---|---|---|---|---|
| E1 | Autofill results | Only an overall filled count is visible. A user cannot tell why a field was skipped or whether an answer was refused for lack of evidence. | Add a per-field progress and result view with pending, filled, skipped, refused, failed, and needs-review states; show field label, source, confidence, and a safe reason. | Planned |
| E2 | Template-backed fields | The current client only passes a simple string at `answers_json[mapped_field]` to the injector. It has no clear UI for coverage, exact option matching, consent fields, or custom values. | Add template coverage and explicit mapped values for supported fields. Show missing template values, validate select values against detected options, and allow explicit user-selected answers for allowed sensitive fields. | Planned |
| E3 | ATS controls and repeated groups | The injector supports native text inputs, textareas, native selects, checkboxes/radios, and contenteditable fields. It does not reliably handle Greenhouse custom controls, dependent dropdowns, repeated education/employment groups, or date widgets. | Add platform adapters, starting with Greenhouse. Support repeated education entries, dependent select choices, and robust event sequences; report individual failures instead of silently counting only successes. | Planned |
| E4 | Resume attachment | No upload is attempted. The extension has only resume metadata/parsed data, not a user-approved binary resume file or an authenticated download path. | Add explicit resume-file attachment. The user selects which stored resume may be attached; the extension fetches that approved file through a secure authenticated endpoint, constructs a browser `File`/`DataTransfer` payload where the ATS permits it, dispatches required events, and reports the outcome. Never read arbitrary local paths or silently attach a file. | Planned - requires backend download endpoint and ATS validation |
| E5 | Answer memory | Manually typed answers and final user edits on an ATS site are not captured. The extension does not create an application record, save answers, or call the memory-ingestion endpoint. Therefore later similar questions cannot retrieve them. | Add a reviewable "Save approved answers to memory" flow. Create/associate an application, save only selected final answers, ingest them into semantic memory, and label future retrieved answers as `past answer` with source and confidence. | Planned |
| E6 | Grounded-answer review | Grounded generation and refusal already exist in the backend, but the panel does not make refusals or uncertainty visible enough for a user to act on them. | Keep the zero-hallucination rule; add a visible "Needs your answer" state, refusal reason, edit box, and optional save-to-template/save-to-memory actions. | Enhancement to existing capability |
| E7 | Submission readiness | The extension does not provide a final review of required fields or a submission gate. | Add a read-only required-fields checklist and an "Open final review" action. Do not automate submission. | Planned |
| E8 | Profile-value precedence and name composition | Direct fills currently use the selected template value only. A stale placeholder in a template can overwrite a correct parsed-resume link; a single `Name` field is skipped when only `first_name` and `last_name` exist. | Define value precedence and validation: explicit non-placeholder template value, then parsed primary-resume value, then a safe derived full name from first + last name. Flag placeholder/example URLs and never inject them. | Planned |
| E9 | Workday adapter | Workday was detected but the live test filled no fields. Its dynamic components, nested frames, custom controls, and event model are beyond the current generic injector. | Capture a reproducible Workday form fixture and build a dedicated adapter that can scan relevant same-origin document/frame content, resolve Workday controls, set values with the required native events, and expose per-field diagnostics. | Planned - requires live Workday validation |

## Explicit behavior for the Canonical questions

The following questions must remain blank unless the user has supplied a
grounded answer in their template or approved answer memory:

- Modern Linux/Ubuntu middleware and software stacks.
- Ubuntu/Debian packaging and packaging policy.
- Leading or contributing to open-source projects.

The system may retrieve an approved past answer for a similar question, but it
must show that source and leave the final decision to the user. It must not
infer expertise merely because the job description requests it.

## Live-test findings to preserve

### Ashby

- The extension correctly filled email and generated a grounded long-form
  answer for the high-growth-startup question.
- The generated answer must still be reviewed: a role being labelled an
  "internship" or evidence of fast-paced work does not by itself establish a
  high-growth startup unless that claim is in the user-approved source data.
- The incorrect LinkedIn value (`linkedin.com/in/your-real-linkedin`) points
  to an example/placeholder stored in the selected template. The current
  direct-fill path prefers that template string over parsed resume data. This
  is tracked as E8.
- The Ashby form's single `Name` field needs a derived full name when the
  profile stores first and last name separately. This is tracked as E8.

### Workday

- Platform detection alone did not yield a usable fill. The current scanner
  and injector operate on a single document's ordinary DOM nodes; many Workday
  forms use delayed rendering, custom controls, and/or embedded document
  contexts. This is tracked as E9.

## Sensitive and voluntary fields

Gender, race/ethnicity, veteran status, disability status, nationality,
privacy acknowledgements, and declarations are not inferred from a resume.
They can only be filled when all of the following are true:

1. The user has explicitly stored a value in a template or selected it during
   the current review.
2. The detected ATS option exactly matches the stored/selected value.
3. The result is shown for review before the user submits.

## Implementation order

Build and test each item independently:

1. **E1 - Per-field results:** establish clear observability before expanding
   automation.
2. **E6 - Refusal/review states:** expose grounded answers and missing context
   in that results UI.
3. **E2 - Template coverage:** make direct, user-owned values dependable.
4. **E3 - Greenhouse adapter:** implement and live-test repeated education and
   custom dropdowns against the Canonical form.
5. **E4 - Resume attachment:** add the secure backend file-download contract
   and test attachment on a supported ATS.
6. **E8 - Profile-value precedence:** prevent stale template placeholders and
   support a safe single-name fallback.
7. **E5 - Approved answer memory:** implement explicit save, ingest, retrieval,
   and provenance UI.
8. **E9 - Workday adapter:** build only after the generic result diagnostics
   make its failures measurable.
9. **E7 - Final review:** show remaining required fields without submitting.

## Definition of done for every enhancement

- Works in a live Greenhouse application test and has an automated/unit test
  where the DOM/API behavior can be reproduced.
- Produces a user-visible result for every attempted field.
- Does not auto-submit applications.
- Does not claim unsupported experience or fill sensitive fields by inference.
- Persists only data the user explicitly approved for persistence.
