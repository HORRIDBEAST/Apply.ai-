# Frontend Context — Job Autofill Copilot (Website + Extension)

This is the planning/context document for the UI layer (website + Plasmo
browser extension) that sits on top of the already-complete FastAPI backend.
Read this before writing any frontend code. It captures: what reference
product we're modeling the UX after (Simplify), what our backend actually
supports today, where the two diverge, and what needs to be decided before
implementation starts.

Status: **context only — no frontend code has been written yet.**
`apps/` in the repo is an empty placeholder directory; the "Browser Extension
Scaffold (BUILT)" section in `META_PROMPT.md` describes a planned state, not
reality — verified by listing the directory (empty) during this planning
session.

---

## 1. Reference product: Simplify (simplify.jobs)

The user wants our extension + website to look and feel like Simplify's,
reusing its UX patterns where they map onto our own backend's actual
capabilities. Simplify has three distinct surfaces:

### 1a. Extension popup (toolbar icon, clicked on any page — not a job form)
A launcher menu, not a workspace. Six entries, each navigating to a feature:
- Super Search — search many job boards at once
- Save jobs to your tracker
- Add contacts — save recruiters/people found on LinkedIn
- Optimize your LinkedIn — profile score + tips
- Autofill applications — "Let Simplify fill out job applications for you"
- Tailor your resume — adjust resume to match a specific job in one click

### 1b. Extension side panel (injected into an actual job application page)
This is the active workspace while on an ATS form (seen on an Oracle HCM
apply page in the screenshots). Three tabs at the top:
- **Autofill** — primary tab. Shows the resume currently selected
  (`RAJAS_DESHPANDE_resume`, expandable/viewable), a "Tailor Resume" action,
  a Cover Letter field ("No Field Found" when the page has none), a Skills
  summary with a filled-count badge (`Filled (0/0)`), and a big primary
  "Autofill This Page" button at the top.
- **Resume Score** — shows the selected resume, a "Tailor Resume" action,
  and a prompt ("Want to add the job description?") to score/tailor the
  resume against a specific JD.
- **Profile** — a read-only, click-to-copy reference view of the user's
  own data: name/location/contact block, Education, Experience (with bullet
  points), etc. Explicitly framed as "Click any block of text below to copy
  it — Reference your profile to fill out your application." Each section
  has an edit (pencil) icon and the panel ends with a big "Edit Profile"
  button.

### 1c. Website (app.simplify.jobs style dashboard)
Full web app, separate from the extension:
- Top nav: Matches · Jobs · Job Tracker · Documents · Services · Refer
- Account menu (top right): Profile, Settings, Give feedback, Help &
  support, Sign out
- Dashboard home: welcome banner with a gamified "streak" counter, quick
  stat cards (best matches, jobs added this week, jobs with salary data,
  remote jobs, etc.), a "Get started" onboarding checklist with progress
  (e.g. "2/6"), a profile-completion badge ("Profile 100% complete — You
  are an All-Star Applicant"), and a running autofill-time-saved counter
  ("You saved 36 hours so far!").

---

## 2. What our backend actually supports today (ground truth)

All of this is implemented, tested via Postman/curl, and confirmed working
with real data during backend hardening (see README.md's "Bugs found and
fixed" changelog for the trail). The frontend must be built against this —
not against assumed/aspirational endpoints.

### Auth
- Clerk JWT verification (`backend/api/core/auth.py`), JWKS fetched from
  `api.clerk.com` with secret-key auth, cached in Redis.
- `MOCK_AUTH_BYPASS` env flag (default `false`) — real auth is enforced by
  default now. Real end-to-end Clerk verification is **not yet confirmed
  working** — see memory `project_clerk_auth_blocked` — `/users/me` is
  currently 401ing on JWT decode even with a real token. This blocks any
  real frontend auth integration until resolved.
- `POST /api/v1/users/sync` — Clerk webhook-style upsert into our own
  `users` table (needs to be wired to a real Clerk webhook in production;
  currently only testable by hand-simulating the payload).

### Users
- `GET/PATCH/DELETE /api/v1/users/me` — profile, display name, preferences,
  soft-delete + Qdrant purge (GDPR).

### Resumes
- `POST /api/v1/resumes/upload` — AES-256-GCM encrypted upload to MinIO,
  background-parsed (via FastAPI BackgroundTasks in dev, Celery in prod)
  into structured JSON: contact info, links (GitHub/LinkedIn/portfolio —
  extracted from both visible text and PDF hyperlink annotations),
  experience, education, certifications, **projects**, **coding_profiles**,
  skills.
- `GET /api/v1/resumes/` / `GET /api/v1/resumes/{id}` — list + parsed detail
  view (this is what would power a "Profile" tab's data).
- `POST /api/v1/resumes/{id}/set-primary`, `DELETE /api/v1/resumes/{id}`.
- No "resume scoring against a JD" or "tailor resume to this job" endpoint
  exists. (Gap vs. Simplify — see §3.)

### Templates (pre-written answers — our equivalent of a saved "profile" of
canned answers, reusable across applications)
- Full CRUD (`/api/v1/templates/`), one marked `is_default`.
- Each template holds structured `answers_json`: contact fields, links,
  location/relocation/visa/salary/notice-period, cover letter, why-us,
  about-yourself, strengths, and a `custom_qa` list of Q&A pairs — plus
  free-form custom questions get answered live by the AI agent, grounded
  in resume + template + past-answer context.

### Applications (one record per job a user is applying to)
- `POST /api/v1/applications/` — create a session (links user + JD +
  template + resume).
- `GET /api/v1/applications/` (paginated list — this is the data source for
  a "Job Tracker"-style table: platform, company, role, date, status),
  `GET /api/v1/applications/{id}`.
- `PATCH /api/v1/applications/{id}/status` — status enum transitions.
- `POST /api/v1/applications/{id}/answers` — bulk-save filled answers.
- `POST /api/v1/applications/{id}/answers/{answer_id}/ingest` — push a
  submitted answer into the `past_answers` Qdrant collection for future
  RAG reuse (this is what makes future autofills get smarter over time).

### AI Agents (`/api/v1/agents/*`) — the actual "Autofill" engine
- `POST /agents/form/understand` — raw HTML in → structured field map out
  (canonical field keys, field types, AI-required flags, selectors).
  Two-phase: regex heuristics (~80% of fields, 35 canonical keys) + LLM
  fallback for the rest. Detects ATS platform (18 platforms supported).
- `POST /agents/answers/generate` — single grounded answer generation.
  **Zero-hallucination by design**: context-only injection, refusal
  protocol when context is insufficient, post-generation validation
  against invented numbers/facts, confidence score from 3 signals (RAG
  coverage, source diversity, grounding ratio).
- `POST /agents/answers/generate-batch` — same, for multiple fields in one
  call (this is what "Autofill This Page" should call).
- `POST /agents/memory/search` — semantic search over past submitted
  answers (powers a "Memory"/past-answers browser).
- `POST /agents/memory/record` — explicitly store an answer in memory.

### Explicitly NOT implemented (gap list vs. Simplify's feature set)
- Job search / "Super Search" across job boards.
- Save-jobs-to-tracker as a distinct action separate from creating an
  application session (our `applications` table conflates "tracked job"
  and "application in progress" — may be fine, may need a status value
  like `saved`/`not_started`).
- Contacts / recruiter-saving.
- LinkedIn profile optimization scoring.
- Resume-to-JD match scoring ("Resume Score" tab equivalent).
- "Tailor Resume" (rewriting resume content to match a specific JD) — we
  have JD ingestion infrastructure in `rag/ingestion.py` and a
  `job_descriptions` Qdrant collection, but no endpoint that creates a JD
  record or runs a tailoring agent against it (this is META_PROMPT's
  "Priority 3 — Job Description pipeline," also not yet built).
- Gamification (streaks, "hours saved" counter, "All-Star Applicant" badge).

---

## 3. Scope decision needed before coding

Our backend's actual strength is **zero-hallucination autofill + a
template/memory system**, not job discovery or resume tailoring. Two ways
to proceed, and this should be settled before implementation starts:

- **A. Visual/structural parity, narrower feature set (recommended default)**
  — copy Simplify's layout patterns (3-tab side panel, popup launcher,
  dashboard-with-nav website) but only wire the tabs/pages that map to real
  endpoints: Autofill tab (real), Profile tab (real, from resume + template
  data), and either drop the Resume Score tab for v1 or stub it as
  "coming soon." Website gets Dashboard (applications list), Resumes,
  Templates, Memory — matching META_PROMPT's existing Priority 2 plan —
  rather than Matches/Jobs/Job Tracker/Refer.
- **B. Full parity** — also build job-description ingestion, a resume
  scoring/tailoring agent, and job tracking as new backend features before
  or alongside the frontend. Larger scope, new backend work required.

---

## 4. Proposed surfaces to build (pending scope decision in §3)

### 4a. Website — Next.js 14 App Router (per META_PROMPT Priority 2)
- Auth via `@clerk/nextjs`.
- `/dashboard` — applications list/table (platform, company, role, date,
  status) + quick stats, styled after Simplify's welcome/dashboard page.
- `/resumes` — upload UI, parse status, parsed-data viewer (contact,
  experience, projects, links, coding profiles).
- `/templates` — template editor: structured fields + custom Q&A list,
  default-template selector.
- `/memory` — past-answer browser with semantic search
  (`/agents/memory/search`).
- Styling: Tailwind CSS, React Query v5 for data fetching, TypeScript
  strict.

### 4b. Extension — Plasmo + TypeScript strict + React 18
- **Popup** (toolbar icon): launcher menu, Simplify-style, scoped to what
  we actually have — e.g. Autofill, View Templates, View Profile/Resume,
  View Applications. No job-search/contacts/LinkedIn entries unless §3
  option B is chosen.
- **Side panel** (injected on a detected job application page), 3 tabs:
  - **Autofill** — resume selector, template selector, "Autofill This
    Page" button driving `form/understand` → `answers/generate-batch` →
    DOM fill, per-field confidence/source badges, filled-count summary.
  - **Resume Score** — stub/"coming soon" under option A, or real
    JD-match scoring under option B.
  - **Profile** — read-only click-to-copy view sourced from the primary
    resume's parsed data + default template, mirroring Simplify's layout.
- Needs: Clerk Chrome Extension auth flow, token persistence in
  `chrome.storage.local`, platform/ATS detection, DOM scanner + injector,
  MV3 service worker — all per META_PROMPT's existing Priority 1 plan.

---

## 5. Decision (2026-10-17)

User chose a hybrid of both options:

- **Build and wire the frontend against option A** (what the backend
  actually supports today): Autofill, Profile, Templates, Resumes, Memory,
  Applications/dashboard. This is the live, working v1.
- **Also write the option-B backend features now** (JD ingestion endpoint,
  resume-scoring/tailoring agent) as real, production-grade code — but do
  **not** wire any frontend UI to them yet. They land unused/unwired,
  ready for a future release. The "Resume Score" tab in the extension
  ships as a visible "Coming soon" placeholder, not hidden entirely
  (matches Simplify's visual layout per §1b, keeps the 3-tab structure
  intact for when it's wired up later).
- **Clerk is deferred, not blocking.** Decision: `MOCK_AUTH_BYPASS=true` is
  now set in the backend `.env` for local dev, so API calls succeed
  regardless of JWT verification status. The **frontend still uses the
  real Clerk SDK** (`@clerk/nextjs` for the website, Clerk's Chrome
  extension SDK for the extension) for actual sign-in and real session
  token fetching — only the backend's verification step is bypassed for
  now. This avoids throwaway work: once `project_clerk_auth_blocked` is
  resolved, flipping `MOCK_AUTH_BYPASS` back to `false` should make
  everything work end-to-end without frontend changes, since real tokens
  are already being sent.

### Build order
1. Backend: JD ingestion endpoint + resume-scoring agent (option-B code,
   unwired).
2. Website (`apps/web`, Next.js 14 App Router + Tailwind + React Query v5
   + `@clerk/nextjs`): Dashboard, Resumes, Templates, Memory pages.
3. Extension (`apps/extension`, Plasmo + TypeScript strict + React 18):
   popup launcher, side panel (Autofill live, Profile live, Resume Score
   stub), DOM scanner/injector, background service worker.

## 6. Build status (2026-10-17)

Both surfaces exist, typecheck clean, and build clean:

- **`apps/web`** (Next.js 16 + Tailwind v4 + `@clerk/nextjs` + React Query v5):
  landing page, sign-in/sign-up, and the 4 authenticated pages (dashboard,
  resumes, templates, memory) all wired to the real backend endpoints.
  `npm run build` passes. Hit two real Next.js 16 / Clerk Core 3 issues
  along the way, now fixed: `middleware.ts` is deprecated in Next 16 (must
  be `proxy.ts`), and `<SignedIn>`/`<SignedOut>` were removed from
  `@clerk/nextjs` in "Core 3" in favor of `<Show when="signed-in">`. Also
  disabled the `cacheComponents` experimental flag — its strict
  static/dynamic prerendering checks aren't worth it for an app where
  every page fetches client-side.
- **`apps/extension`** (Plasmo + TypeScript strict + React 18 +
  `@clerk/chrome-extension` + React Query v5): popup launcher, side panel
  with the 3 Simplify-style tabs (Autofill — live, wired to
  `form/understand` → `answers/generate-batch` → DOM injection; Resume
  Score — visible "coming soon" stub per the §5 decision; Profile — live,
  click-to-copy from the parsed resume), a content script
  (`src/contents/scanner.ts`) doing the actual DOM read/write since
  popup/side-panel/background can't touch the page directly, and a DOM
  injector that uses the native-setter + event-dispatch trick needed for
  React/Vue/Angular-controlled inputs. `npx plasmo build` passes and
  produces a loadable `build/chrome-mv3-prod` with the expected manifest
  (side panel, content script, `externally_connectable` for Clerk's sync
  host). Fixed the scaffold's `"plasmo": "workspace:*"` dependency (a
  pnpm-only protocol that breaks `npm install`) to a real version.

**Not yet done — needs a live loaded-extension + live-browser pass, not
just a clean build:**
- Confirm Clerk's chrome-extension sync-host flow actually authenticates
  (same open question as the website's Clerk auth — see
  `project_clerk_auth_blocked` memory).
- Confirm the content script's DOM scan/fill actually works against a
  real ATS form (Greenhouse, Lever, etc.) — built against the documented
  field/selector shape from `FormUnderstandingAgent`, never run against a
  live page yet.
- `chrome.sidePanel.open()` requires a user-gesture context; verify the
  popup button click satisfies that in practice.

### Still open (not blocking, revisit opportunistically)
- Website component library: tried shadcn/ui's init first, but it hangs on
  an interactive TTY prompt in this non-interactive shell environment, so
  the website is built with plain Tailwind CSS components instead. Revisit
  shadcn by hand (copy-pasting individual component files in, which is all
  it really is) if the hand-rolled components get unwieldy.
