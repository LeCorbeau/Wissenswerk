---
layout: wiki_page
title: Signals and Tasks
category: Technical
---

# Signals and Tasks

Wissenswerk uses Signals and Tasks for agent coordination. They replace committed message-board behavior with local, JSON-first runtime state.

Signals are events that need another decision unit. Tasks are the tracked work items created from those events. They are not factual authority; facts come from sources, wiki output, provenance, and retrieval.

## When To Raise A Signal

Use `task raise` only for coordination exceptions:

- `anomaly`: suspicious input, provenance, or generated output.
- `blocker`: the current workflow cannot continue safely.
- `handoff`: another role must own the next action.
- `approval`: destructive, expensive, external, or protected work needs confirmation.
- `audit_finding`: validation found missing citations, schema errors, or provenance gaps.
- `run_event`: meaningful status for long-running work.

Routine progress and final summaries belong in reports, not Tasks.

## Roles

- `coordinator`: checks digests, routes tasks, resolves simple coordination items.
- `curator`: raises anomalies for RagPrep/import/source planning issues.
- `verifier`: owns citation, link, provenance, and audit findings.
- `maintainer`: owns provider, migration, reset/wipe, bot, and release approvals.

## Local State

Task state is local runtime data:

```text
.wissenswerk/tasks/tasks.sqlite
.wissenswerk/tasks/active/*.md
```

The SQLite database is the machine-readable task state. The Markdown files are short active-task mirrors for humans. Neither should be committed.

## Command Pattern

```bash
./wissenswerk.py task digest --json
./wissenswerk.py task raise --type audit_finding --severity high --role verifier --summary "Missing source_path" --json
./wissenswerk.py task claim TASK-2026-0001 --agent verifier --json
./wissenswerk.py task resolve TASK-2026-0001 --summary "Source metadata fixed" --json
./wissenswerk.py run status --json
```

Task IDs are stable. Terminal tasks are not reopened; follow-up work should raise a new task and link the previous one as context.
