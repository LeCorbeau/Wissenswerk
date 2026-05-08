---
layout: wiki_page
title: Wissenswerk Publication Readiness
category: Technical
---

# Wissenswerk Publication Readiness

This page defines the minimum bar for a clean public `wissenswerk` repository.

## Public Repository Files

The repository must contain:

- `README.md`: human overview, quick start, verification, documentation map.
- `AGENTS.md`: platform-independent agent instructions.
- `DESIGN.md`: UI and documentation design contract.
- `LICENSE`: MIT license for the public core.
- `CONTRIBUTING.md`: contribution flow and verification gates.
- `CODE_OF_CONDUCT.md`: community behavior expectations.
- `SECURITY.md`: private vulnerability reporting and secret-handling policy.
- `SUPPORT.md`: support boundaries.
- `pyproject.toml`: Python package metadata and console script.
- `.gitignore`: secrets, runtime state, caches, local DBs, and build outputs.
- `Makefile`: short local aliases for verify, test, and runtime cleanup checks.
- `.github/workflows/ci.yml`: Python CI.
- `.github/ISSUE_TEMPLATE/`: bug and feature issue forms.
- `.github/PULL_REQUEST_TEMPLATE.md`: PR checklist.
- `.github/CODEOWNERS`: review ownership.

## Required Gates

Run:

```bash
python3 -m py_compile wissenswerk.py
./wissenswerk.py doctor --json
./wissenswerk.py audit --json
./wissenswerk.py stats --json
./wissenswerk.py test --json
./wissenswerk.py design lint --json
./wissenswerk.py hygiene reports --json
git diff --check
```

## GitHub AI Readiness

The public repository should be easy for hosted and local coding agents to work on:

- predictable `AGENTS.md` with setup, tests, roles, and PR rules,
- JSON-producing CLI commands for machine parsing,
- CI that mirrors local gates,
- no hidden semantics in IDE-specific adapter files,
- no secrets or private generated state in the repository,
- issue/PR templates that ask for sanitized reproduction steps.

## Current Status

This repository is the canonical public repository. Current readiness is established by the direct repository gate above. A ready repository has:

- passing health, test, design, and diff checks,
- no tracked generated report ballast or private runtime state,
- no private source leaks in generated wiki output,
- no open blocking coordination tasks.

## Demo Release Gate

For a showcase repository such as `porreres-wiki`, run:

```bash
./wissenswerk.py demo run --from-ragprep <dir> --profile local-history --apply --json
./wissenswerk.py publish pages --dry-run --json
```

The demo repository should publish generated pages, source metadata, reports, and screenshots. It should not publish private source full text unless rights have been reviewed.
