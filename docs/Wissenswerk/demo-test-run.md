---
layout: wiki_page
title: Wissenswerk Demo Test Run
category: Technical
---

# Wissenswerk Demo Test Run

This runbook describes the pre-publication demo path for a Hermes-led Wissenswerk project. The goal is to prove that a prepared document corpus can become an auditable Markdown wiki through neutral CLI contracts, without relying on an IDE-specific agent runtime.

## Scenario

The reference demo is a local-history corpus:

- RagPrep has already parsed and cleaned source material.
- Wissenswerk receives RagPrep JSON/JSONL artifacts.
- Public output must not dump private full-text sources.
- The generated wiki must be inspectable, source-backed, and suitable for GitHub Pages.

Hermes acts as `coordinator`. Other personas are role scopes, not mandatory separate processes:

- `curator`: source inventory, evidence segment normalization, article planning.
- `verifier`: provenance, source locators, conflicts, audit findings.
- `maintainer`: CLI, tests, export, publication readiness.

## Required Inputs

Minimum files before a real demo run:

- `AGENTS.md`
- `DESIGN.md`
- `wissenswerk.yaml`
- `project_manifest.json`
- a RagPrep output directory containing `.json` or `.jsonl` artifacts

Minimum RagPrep record fields:

- `document_id`
- `segment_id` or RagPrep-compatible `chunk_id`
- `text`

Recommended source locator fields:

- `source_path`
- `archive_id`
- `source_url`

Missing source locators create audit findings. They do not stop ingestion unless the project policy later requires public source links.

## Hermes Kickoff Prompt

```text
You are Hermes acting as Wissenswerk coordinator.

Read AGENTS.md, DESIGN.md, wissenswerk.yaml, project_manifest.json, and docs/Wissenswerk/demo-test-run.md.
Run doctor and task digest with JSON output.
Use setup --quick for the demo unless a guided profile already exists.
Ingest the RagPrep corpus as source documents with evidence segments.
Analyze the full corpus before planning or drafting pages.
Plan article candidates from claims, source coverage, and the concept graph.
Build only source-backed Markdown pages.
Run audit, stats, demo report, and publish pages --dry-run.
Raise tasks only for real anomalies, blockers, approvals, audit findings, or handoffs.
Do not publish remotely, delete sources, or expose private source paths without explicit human approval.
End with the generated paths, warnings, open tasks, and exact next commands.
```

## One-Command Demo

Use this for a home-style test run:

```bash
./wissenswerk.py demo run \
  --from-ragprep <ragprep-output-dir> \
  --profile local-history \
  --project-name "Porreres Local History" \
  --language en \
  --apply \
  --json
```

Internally, `demo run` executes:

```text
setup -> ingest -> analyze -> plan articles -> build -> audit -> stats -> demo report
```

It is convenient, but it is not a black box. Each step has an equivalent standalone command.

## Inspectable Step-By-Step Flow

### 1. Health and Coordination

```bash
./wissenswerk.py doctor --json
./wissenswerk.py task digest --since 24h --json
```

Hermes checks whether contracts, design lint, provider configuration, gitignore policy, and task storage are usable. Open `critical`, `approval`, or `blocker` tasks must be handled before destructive or public actions.

### 2. Setup

```bash
./wissenswerk.py setup \
  --quick \
  --profile local-history \
  --project-name "Porreres Local History" \
  --from-ragprep <ragprep-output-dir> \
  --language en \
  --json
```

Setup writes or updates:

- `wissenswerk.yaml`
- `.wissenswerk/profile/project_profile.json`
- `docs/Project_Profile.md`
- `docs/Source_Policy.md`
- `docs/Wiki_Style_Guide.md`

Setup also scans the RagPrep directory and records a corpus preview:

- discovered source documents
- normalized evidence segment count
- source locator coverage
- metadata findings
- recommended next commands

This is the point where Hermes should notice whether the corpus shape matches the intended project. For a quick local-history demo, the setup questions stay intentionally light. More editorial policy belongs in `setup --guided`.

### 3. Ingest

```bash
./wissenswerk.py ingest --from-ragprep <ragprep-output-dir> --apply --json
```

Ingest writes:

- `.wissenswerk/corpus/source_documents.json`
- `.wissenswerk/corpus/evidence_segments.json`
- `.wissenswerk/corpus/import_manifest.json`
- `reports/wissenswerk/*_ragprep_ingest.json`

The public mental model is source-first:

```text
source documents -> evidence segments -> claims -> article candidates -> wiki pages
```

Evidence segments are internal compiler units. They are used for provenance, retrieval, and claim grounding, but users should not have to think in segments.

### 4. Analyze

```bash
./wissenswerk.py analyze --apply --json
```

Analyze reads the corpus state and writes:

- `.wissenswerk/analysis/corpus_inventory.json`
- `.wissenswerk/analysis/entities.json`
- `.wissenswerk/analysis/claims.json`
- `.wissenswerk/analysis/graph.json`
- `.wissenswerk/analysis/conflicts.json`
- `.wissenswerk/analysis/analysis.json`

The concept graph is a bootstrap graph. It connects:

- entities to source documents via `mentioned_in`
- claims to evidence segments via `supported_by`
- source topics to years or related concepts via simple typed relations

Hermes should use this graph to avoid drafting from one isolated document. The point is to first see which concepts recur, which sources support them, and where review is needed.

### 5. Plan Articles

```bash
./wissenswerk.py plan articles --apply --json
```

Article planning ranks candidates by:

- source-document coverage
- mention frequency
- local-history usefulness
- navigational value
- conflict or uncertainty relevance

The output is `.wissenswerk/article_plans/article_plan.json`.

Hermes should review whether the plan looks plausible before building. For the Porreres-style demo, expected classes of pages are:

- overview article
- history article
- timeline
- sources overview
- places and buildings
- institutions
- people and families
- glossary

### 6. Build

```bash
./wissenswerk.py build --apply --json
```

Build writes generated Markdown and provenance:

- `docs/Wiki/Articles/*.md`
- `docs/Wiki/Articles/*.provenance.json`
- `docs/Wiki/index.md`
- `reports/wissenswerk/*_wiki_build.json`

Generated pages must cite source references and must not publish private local source paths. If source coverage is weak, Hermes should leave uncertainty visible rather than smoothing it into confident prose.

### 7. Audit, Stats, Demo Report

```bash
./wissenswerk.py audit --json
./wissenswerk.py stats --json
./wissenswerk.py demo report --json
```

These commands provide the release evidence:

- audit status
- generated page count
- source/provenance findings
- entity and claim counts
- article candidate counts
- open tasks
- publish blockers

The generated demo summary is:

- `reports/wissenswerk/demo_summary.json`
- `reports/wissenswerk/demo_summary.md`

### 8. Publish Dry Run

```bash
./wissenswerk.py publish pages --dry-run --json
```

This does not create a remote repository. It checks whether the local docs tree is ready for GitHub Pages and whether there are blockers such as missing index pages, missing demo reports, public leaks, or blocking tasks.

Remote publication remains a human-approved release action.

## What Hermes Should Report At The End

Hermes should finish with:

- status of every command
- generated source-document and evidence-segment counts
- generated wiki page count
- audit status and findings
- source locator coverage
- open tasks by severity
- paths to reports and generated wiki output
- exact next commands for human review or publication

The closeout should not claim factual completeness. It should say what the compiler produced, what evidence supports it, and what still needs human judgment.

## Success Criteria

A demo run is publishable when:

- `doctor` passes or has only understood non-blocking runtime warnings.
- `ingest` writes source documents and evidence segments.
- `analyze` writes claims and graph artifacts.
- `plan articles` produces plausible prioritized pages.
- `build` writes Markdown plus provenance JSON.
- `audit` has no private source leaks and no missing-source failures.
- `stats` and `demo report` exist.
- `publish pages --dry-run` reports `ready`.
- Hermes leaves no unresolved `critical`, `approval`, or `blocker` tasks.
