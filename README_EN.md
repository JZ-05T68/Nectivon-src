# Nectivon v0.8.6

[简体中文](README.md) | **English** | [日本語](README_JP.md)

**Nectivon** (formerly **Engineering Knowledge Base / EKB**) is a local-first personal knowledge and experience system
for accumulating, organizing, verifying, and recalling long-term personal knowledge assets.

## Using v0.8.6

On Windows 10/11 with Python 3.11+, run `check_environment.bat` and install missing dependencies:

```powershell
python -m pip install -r requirements\requirements.txt
```

Start the local application with `启动正式版.bat` at `127.0.0.1:8501`; stop it with `stop_release.bat`.
The source also provides isolated development launchers for ports 8511 and 8512. `run_all_tests.bat`
runs Ruff and the Python regression suite. No API key is required to start or use offline features.

### Separate image-reading and explanation settings

Configure providers in System Settings → AI / Model Services. Image reading / question splitting
and explanations / knowledge Q&A save their models and credentials independently. Image reading
can be enabled by itself; existing configurations remain compatible. Saving settings does not call AI.
Keys use Windows Credential Manager or a DPAPI-encrypted fallback and are excluded from this repository.

PDFs, Word, PowerPoint, and images share direct reading of original page images; Office files are
converted locally first. Supported image presets use Qwen, DeepSeek, Kimi, or GLM. The reading flow
does not send OCR, extracted text layers, or old summaries to the image model. Connection failures
are reported explicitly. Original files, notes, search, and backups remain local.

### Source crops and manual image revisions

Question candidates, learning organization, explanations, and practice share source-image crops and
math rendering. Compare the original scan with a manually revised display image using drawing,
text, color sampling, shapes, local restoration, zoom, undo, and redo. Changes take effect only after
Save; local display copies and revision history preserve the original scan. AI continues to read the original.

The 2026-10-06 source update also aligns learning subjects with training profiles and fixes font
preferences, storage-path compatibility, and GMT+8 time display. The existing v0.8.6 tag and release
retain the earlier release snapshot; `main` contains these subsequent revisions.

## Product Positioning

Nectivon began with engineering knowledge management, but it is more than a PDF question-answering
tool, a RAG utility, or a document search engine. It is evolving from material management into a
personal knowledge and experience system that helps users accumulate and reuse their own knowledge,
experience, mistakes, methods, judgment, project history, decisions, corrections, and outcomes.

General-purpose AI is better at invoking the world's knowledge; Nectivon aims to help users invoke the
self they have accumulated over time. Users must actively import or authorize every source. Local
files and SQLite remain the sources of truth, and AI remains an optional enhancement layer.

```text
materials -> understanding -> knowledge objects -> source verification -> personal experience
          -> retrieval and recall -> reuse -> engineering capability
```

## Core Capabilities

- import PDFs, detect duplicates with SHA-256, render pages, and extract existing text layers;
- read scanned pages, images, charts, and other visual pages while returning to the real document
  page for final evidence;
- retrieve materials with SQLite FTS5, field weighting, specificity top-up, and optional hybrid search;
- organize knowledge objects, sources, evidence, revisions, relations, and personal memory;
- distinguish materials, raw saved Q&A, and formal experience, preserving provenance, authority,
  and user-confirmation state;
- let the Agent select read-only tools, read materials, obtain evidence, and produce
  citation-constrained answers;
- protect version families, old and superseded values, object attribution, and cross-device or
  cross-model engineering parameters;
- create, validate, and restore complete local backups while protecting the database through
  contiguous migrations, integrity checks, and foreign-key checks.

## Current Version

The current source version is v0.8.6; see this repository's v0.8.6 tag and release for the release identity. Port 8511 is an internal test instance. v0.8.4 was rejected after further defects were found and is **not** the current formal release. The v0.8.4 material below records only historical development and bounded test findings.
The public distribution omits the school, undergraduate-major, and catalog-source JSON files whose redistribution basis is unconfirmed. Without them, higher-education catalog lookup and new profile configuration explicitly report that the data is unavailable. Saved records are retained; basic-education configuration, import, retrieval, learning organization, teach-back, and local practice remain available. Catalog-dependent regression tests require separately authorized data.

### v0.8.4 — Historical Development and Bounded Reliability Tests (Rejected)

The v0.8.4 work moved the v0.8.x focus from merely asking whether
the Agent can call tools to whether it can safely find, bind, and explain evidence under realistic,
colloquial, incomplete, and easily confused requests. The current backend reliability baseline has
now been frozen.

Highlights include:

- stronger retrieval for colloquial wording, Chinese numerals, single-character clues, and terminology mismatch;
- routing discipline for location-style questions so words such as “last time” or “before” do not send a page lookup to the wrong knowledge source;
- continued protection for version, condition, object, and cross-device parameter binding;
- provenance and authority boundaries across visual content, text, Saved QA, and personal experience;
- explicit rejection or disclosure of stale, incorrect, or low-authority evidence;
- scoped honest-boundary responses when current retrieval evidence is insufficient, instead of turning a local miss into a claim about the whole corpus;
- a final independent simulated-user retest, followed by backend reliability freeze with no known P0, P1, or blocking P2;
- code-only promotion of the frozen backend to the 8501 Competition Stable runtime while preserving formal data and keeping 8511 staging isolated.

The v0.8.4 backend reliability baseline is frozen, but this is not a claim of perfect recall or a
guarantee that the product can never fail. A small amount of Honest Omission remains: relevant
material may exist but not be retrieved in a given query. In that case, the system should say that
the current evidence is insufficient rather than fill the gap with unrelated content. Zero-Batch
real-user validation and further product work remained necessary. Those historical results do not replace the v0.8.6 release audit or human acceptance testing.

## Historical v0.8.4 Reliability Evidence (Not a Current Release Finding)

The final v0.8.4 independent simulated-user retest was performed by a tester role separate from the
developer role and against the same frozen backend code. It recorded 233 same-pipeline Agent
executions. In that evidence set, no incorrect concrete answer (Commission), fabricated content,
version/condition/object binding error, evidence-authority error, or unsupported corpus-wide
negative assertion was observed. The nine historical residual cases ended as 7 direct passes,
2 honest boundaries, and 0 reliability failures.

At the same time, 32 of 232 unique cases were Honest Omissions: the target material existed but was
not successfully retrieved, and the system responded with a scoped “current evidence is
insufficient” boundary. This shows that retrieval recall can still improve; it must not be described
as 100% recall or as a guarantee that the system cannot fail.

Frozen status:

- `OPEN P0 = 0`
- `OPEN P1 = 0`
- `OPEN BLOCKING P2 = 0`
- `M1R-A = RESOLVED_WITH_HONEST_OMISSION_MONITOR`
- `M1R-B = RESOLVED_MONITOR`
- `M2 = RESOLVED_MONITOR`
- `M3 = ARCHITECTURE_REQUIRED_UNCHANGED`

M3 still means that full conversation history is not frozen as a current capability. When a
cross-turn bare reference lacks enough context, safe clarification remains the product boundary.

The original internal test records are retained outside the public candidate worktree; request access from the maintainer if they need to be reviewed.

## Version History

The entries below describe completed scope version by version and preserve the early release history.

### v0.0.1 — Initial Local Engineering Knowledge Base MVP

Established local PDF import, per-page rendering, text extraction, review status, page Markdown,
browsing, search, evidence packages, and SQLite persistence.

### v0.0.2 — Page-Level Knowledge Management & Background Operation

Established page organization and Windows background start, stop, status, health, and logging workflows.

### v0.0.3 — Continuous Review Workflow

Added a continuous review queue, previous/next navigation, save-and-continue, unsaved-change protection,
and skip flow.

### v0.0.4 — Traceable Page Retrieval & Citation Evidence Packages

Linked search results to documents and pages, separated source material from user notes, and warned
about unreviewed content.

### v0.0.5 — Multi-Page Evidence Collection & Citation Workflow

Introduced a persistent evidence basket, cross-page selections, source validation, and single- or
multi-document evidence packages.

### v0.0.6 — Search Filters & State Restoration

Expanded project, tag, review-state, and hit-field filters and restored search-page state.

### v0.0.7 — Search Explainability & Continuous Reading

Added result grouping, hit summaries, page previews, in-document navigation, and result-focus restoration.

### v0.0.8 — Full Backup, Diagnostics & Release Closure

Completed full local backup, read-only diagnostics, safe restore preflight, and unified release checks
before moving to v0.1.0.

### v0.1.0 — First Full Manual Acceptance & Formal Release

Completed the first full manual acceptance, backup/restore loop, diagnostic reporting, and formal release.

### v0.1.1 — Stability & Usability Patch

Fixed the local listening boundary and improved Windows diagnostics, review entry, and same-page selections.

### v0.1.2 — Batch Organization Efficiency

Added bounded batch updates, preflight, explicit confirmation, and stable selection scopes.

### v0.2.0 — Long & Non-Standard Document Foundation

Hardened long-document import, per-page failure isolation, unusual-page handling, recovery, and scale checks.

### v0.2.1 — Default Evidence Basket Concurrency Patch

Fixed concurrent first-time creation of the default evidence basket.

### v0.2.2 — Local Optical Character Recognition

Added explicit local OCR for scans and pages without a text layer while preserving human-review boundaries.

### v0.2.3 — v0.2.x Closure

Closed reliability, recovery, and test work for the long and non-standard document foundation.

### v0.2.4 — Release & Deployment Consistency Patch

Aligned application, page, configuration, release-check, and local-deployment versions.

### v0.3.0 — Structured Notes Foundation

Established document, page, text-selection, and image-region notes with Schema 5.

### v0.3.1 — Note Importance & Visual Mapping

Added three importance levels, customizable display mapping, filtering, and Schema 6.

### v0.3.2 — Cross-Document Aggregation & Document Deletion Lifecycle

Added cross-document aggregation, impact preview, quarantine, and crash recovery.

### v0.3.3 — Document Management & Data Safety

Consolidated document management and deletion behind exact-title confirmation and recovery safeguards.

### v0.4.0 — Evidence Objects & Source Model

Unified page, text-selection, and image-region evidence with source anchors and confirmation states.

### v0.4.1 — Citation-Grounded Prompt Packages

Generated traceable prompt packages from confirmed evidence and failed closed when sources became invalid.

### v0.4.2 — Prompt Freshness & Stale-Output Protection

Invalidated old output after evidence changes so stale prompt packages were less likely to be reused.

### v0.4.3 — Real-Problem Validation & AI Readiness Gate

Completed real-problem validation, source-boundary checks, and the conditional gate for later optional AI.

### v0.5.0 — AI Foundation & Optional Hybrid Retrieval

Added an optional model interface, page vectors, indexing orchestration, persistent vector recall, and hybrid
retrieval while keeping offline mode the default.

### v0.5.1 — Retrieval Stabilization

Established a frozen retrieval benchmark, explicit fallback states, index coverage, and weak-evidence notices.

### v0.5.2 — Knowledge Foundation

Established knowledge objects, memories, sources, relations, append-only revisions, source fingerprints,
and dedicated full-text indexes.

### v0.5.3 — Audited AI Integration

Added citation-constrained on-demand answers, read-only experience candidates, an AI call ledger,
structured exports, and isolated legacy-backup upgrade.

### v0.6.0 — Agent Foundation

Established the single-step read-only execution path so the Agent could select tools, read materials,
obtain evidence, and produce source-grounded answers.

### v0.6.1 — Competition Demo Refinement (milestone marker, not a separate release)

Completed the competition workspace, deterministic demo corpus, presentation states, runbook, and frozen
audit. This milestone has no separate tag or GitHub Release.

### v0.7.0 — Personal Experience Foundation

Established long-term personal-experience data, capture flow, and provenance boundaries that distinguish
materials, raw saved Q&A, and personal experience.

### v0.7.1 — Experience Recall

Let saved experience participate in appropriate answers while preserving provenance and the authority
boundary between material and experience.

### v0.7.2 — Visual Document Understanding

Extended page-level understanding to scans, images, and charts; final answers still return to real pages
for evidence.

### v0.7.3 — Experience Quality

Added provenance, authority, and confirmation controls so ordinary model output, raw Q&A, or unconfirmed
information could not become high-authority experience.

### v0.7.4 — Experience Evolution

Added confirms, refines, contradicts, and supersedes relations so later evidence can correct, overturn,
or replace earlier experience.

### v0.8.0 — Reliability Baseline & Evaluation

Established failure taxonomy, frozen and holdout evaluation sets, a risk matrix, failure ledger, and
reliability gates, creating the discover-analyze-fix-regress-revalidate loop.

### v0.8.1 — Retrieval & Tool Correctness

Focused on finding the right material through retrieval, tool routing, version-family selection, corpus
version identification, and drift protection.

### v0.8.2 — Answer & Evidence Correctness

Focused on saying the right thing through answer, citation, and evidence binding, the insufficient-information
contract, visual-evidence constraints, and unsupported-claim protection.

### v0.8.3 — Personal Memory & Reliability Closure

Closed personal-memory, long-term and cross-session experience, visual retrieval, version/value/attribution
protection, and migration-integrity work, backed by simulated human use, independent audit, formal-data
provenance, and migration/rollback rehearsals.

### v0.8.4 — Human Reliability & Zero-Batch Validation

Closed the current reliability loop around colloquial retrieval, tool routing, evidence boundaries,
version/condition/object binding, and honest omission. After the final independent simulated-user
retest, the backend reliability baseline was frozen and the frozen code was promoted code-only to
the 8501 Competition Stable runtime. This is a historical runtime record: v0.8.4 was subsequently rejected and is not the current formal release.

### v0.8.5 — Agent Decision Reliability and Zero-Batch Feedback (Historical Milestone)

The changelog records structured decision-output revisions, separate truncation/parsing/provider-failure handling, a request-level AI audit ledger, numeric retrieval downranking, and numbered-answer formatting. This describes completed development scope at that point, not the current formal release.

### v0.8.6 — Learning Workflow

The source implements local-first material import and page handling (PDF, Word, PowerPoint, and upload of existing image files), local retrieval with optional hybrid recall, source/evidence links, single-question organization in layer one, grouping in layer two, and teach-back plus targeted practice in layer three. Variant practice questions are retrieved only from real local user materials: there is no online question search or AI-generated filler, and zero results are valid when no trustworthy variant exists. Qwen, DeepSeek, Kimi, Hunyuan, and GLM are optional configurable providers; offline core functions remain available without an API key. These are implementation boundaries; positive local-practice page/source-jump acceptance and cross-machine reproduction remain pending after submission.

## Next Stage

### v0.9.0 — Limited Preview

The next focus is real-user feedback and inputs for the v0.9.x hardening
stage. v0.9.x will continue to focus on long-running operation, cost, context, memory pollution,
evaluation, and related engineering hardening. Only completed and validated capabilities should be
described as shipped.

## Local Operation & Safety

Requirements: Windows 10/11, PowerShell, Python 3.11, and a Python SQLite build with FTS5 support.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m streamlit run app.py
```

The formal service binds to `127.0.0.1:8501`. `data/`, `backups/`, `logs/`, `runtime/`, `.env`,
databases, user materials, and page images must never enter Git. The project has no registration,
login, accounts, multi-user permissions, or cloud sync. Offline core features remain available without
an API key.

## Quality Checks

```powershell
python -m pytest --ignore=tests/test_hosted_packaging.py -q
python -m ruff check .
git diff --check
```

## Repository & License

The formal source repository is
[JZ-05T68/Nectivon-src](https://github.com/JZ-05T68/Nectivon-src), and the product showcase
repository is [JZ-05T68/Nectivon](https://github.com/JZ-05T68/Nectivon).
The repository currently has no standalone LICENSE file, so no unexpressed open-source license grant
should be inferred.

## Documentation

- [Changelog](CHANGELOG.md)
- [GitHub Releases](https://github.com/JZ-05T68/Nectivon-src/releases)

`README.md`, `README_EN.md`, and `README_JP.md` are fact-equivalent official project documents. Current version,
positioning, capabilities, boundaries, evidence, and roadmap changes must stay synchronized.
