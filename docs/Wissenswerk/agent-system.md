---
layout: wiki_page
title: Wissenswerk Agent System Integration
category: Technical
---

# Wissenswerk Agent System Integration

Wissenswerk is designed so any coding agent or host (for example Codex, Jules, Gemini CLI, Cursor, or Aider) can work from the same neutral contracts.

## Coordinator Role

The lead agent should act as the `coordinator` by default:

1. Read `AGENTS.md`, `DESIGN.md`, `project_manifest.json`, and `wissenswerk.yaml`.
2. Run `./wissenswerk.py doctor --json`.
3. Read `.wissenswerk/profile/project_profile.json` when present, or run `setup --quick` / `setup --guided`.
4. Run `task digest` and inspect `hygiene reports` before public-repo work.
5. Delegate bounded work to `curator`, `verifier`, or `maintainer` roles.
6. End with concise status and machine-readable reports.

## Role Mapping

- `coordinator`: plans runs, checks state, decides escalation
- `curator`: imports RagPrep artifacts and drafts article plans
- `verifier`: checks provenance, conflicts, links, and audit reports
- `maintainer`: changes core code, provider layer, migrations, tests, and release tooling

Localized display names are aliases only. Public role IDs stay English.

## Skill Surfaces

The coordinator should prefer these neutral surfaces:

- CLI JSON from `./wissenswerk.py`
- `AGENTS.md` and nested tenant instructions
- `DESIGN.md`
- `project_manifest.json`
- MCP and tool manifests

Host-specific skill folders are generated adapter outputs. They must not contain unique semantics absent from the neutral contracts.

## Standard Automated Flow

```text
doctor -> task digest -> setup/profile -> ingest -> analyze -> plan articles -> build -> audit -> stats -> demo report -> publish pages --dry-run
```

For the pre-publication demo run, the coordinator should follow [Demo Test Run](demo-test-run.md). That runbook defines the kickoff prompt, setup artifacts, source-document import contract, analysis outputs, success criteria, and required closeout.

For maintainer work:

```text
doctor -> hygiene reports -> plan -> apply -> tests -> report
```

The coordinator should never treat memory or chat history as factual authority. Facts come from sources, wiki pages, provenance, retrieval, and audits.
