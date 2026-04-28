---
layout: wiki_page
title: Wissenswerk Cleanup
category: Technical
---

# Wissenswerk Cleanup

Cleanliness for Wissenswerk means that the public repository contains only generic product code, contracts, fixtures, tests, and concise documentation. Runtime state, generated reports, private corpora, and tenant-specific material stay outside the public surface.

## Publishable Scope

The public export is controlled by `wissenswerk_export_manifest.json` and inspected with:

```bash
./wissenswerk.py export plan --strict --json
```

The export includes:

- root contracts: `AGENTS.md`, `DESIGN.md`, `README.md`, `CONTRIBUTING.md`, `LICENSE`
- GitHub community and CI files
- generic runtime surface: `wissenswerk.py`, `wissenswerk.yaml`, `project_manifest.json`
- focused tests and fixtures
- Wissenswerk documentation

The export excludes:

- private corpora
- generated reports
- local runtime state
- local databases and dumps
- build artifacts
- secrets and bot sessions

## Report Hygiene

Report and runtime state is visible through:

```bash
./wissenswerk.py hygiene reports --json
```

This command is an inventory surface, not a deletion tool. Destructive cleanup must use dry-run-first reset/wipe commands.

## Definition of Clean

A tree is clean enough for publication when:

- `python3 -m py_compile wissenswerk.py` succeeds
- `./wissenswerk.py doctor --json` is `ok`
- `./wissenswerk.py export plan --strict --json` is `ready`
- `./wissenswerk.py test --json` passes
- `git diff --check` is clean
- no public file contains private corpus references, local secrets, generated report state, or tenant-specific assumptions

## Candidate Verification

```bash
./wissenswerk.py export materialize --target /tmp/wissenswerk-public --apply --json
./wissenswerk.py export verify --target /tmp/wissenswerk-public --json
```

The second command runs the public gates inside the materialized tree.
