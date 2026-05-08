---
layout: wiki_page
title: Wissenswerk Cleanup
category: Technical
---

# Wissenswerk Cleanup

Cleanliness for Wissenswerk means that the public repository contains only generic product code, contracts, fixtures, tests, and concise documentation. Runtime state, generated reports, private corpora, and tenant-specific material stay outside the public surface.

## Publishable Scope

The GitHub repository is the public Wissenswerk surface. It should contain:

- root contracts: `AGENTS.md`, `DESIGN.md`, `README.md`, `CONTRIBUTING.md`, `LICENSE`
- GitHub community and CI files
- generic runtime surface: `wissenswerk.py`, `wissenswerk.yaml`, `project_manifest.json`
- focused tests and fixtures
- Wissenswerk documentation

The repository must exclude:

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
- `./wissenswerk.py test --json` passes
- `./wissenswerk.py design lint --json` passes
- `./wissenswerk.py hygiene reports --json` is reviewed
- `git diff --check` is clean
- no public file contains private corpus references, local secrets, generated report state, or tenant-specific assumptions

## Repository Verification

```bash
python3 -m py_compile wissenswerk.py
./wissenswerk.py doctor --json
./wissenswerk.py test --json
./wissenswerk.py design lint --json
./wissenswerk.py hygiene reports --json
git diff --check
```

These checks run directly in the canonical repository.
