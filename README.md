# Wissenswerk

Wissenswerk is a platform-independent knowledge compiler. It imports prepared document corpora, builds an auditable Markdown wiki, preserves provenance and reports, and exposes the same knowledge through search, bot adapters, and future API surfaces.

The project is designed for agents and humans working together through open repository contracts instead of IDE-specific assumptions.

## What It Can Do Today

Wissenswerk currently provides a dependency-light local CLI prototype. It can create a project profile, import RagPrep JSON/JSONL chunks, analyze the whole corpus into inventory/entities/claims/graph artifacts, plan article candidates, build Markdown pages with provenance JSON, audit source/provenance risks, generate demo statistics, and prepare a GitHub Pages dry-run.

Retrieval is still a lexical bootstrap. PostgreSQL + pgvector and OpenAI-compatible embedding/rerank providers are configured as the target architecture, not yet the full default runtime.

## Why Not Just RAG?

RAG is an interaction pattern: it answers a question from retrieved context. Wissenswerk is a build pipeline: it turns a prepared corpus into durable project knowledge that can be reviewed, cited, published, searched, audited, and handed to agents.

The two belong together. Retrieval should support Wissenswerk, but it should not replace the generated wiki, provenance, reports, and article plans.

## Why Not llm-wiki.net?

`llm-wiki` style tools are strong for agent-native research and fast topic compilation. Wissenswerk has a more conservative center of gravity: prepared corpora, explicit RagPrep boundaries, source metadata without source dumping, claim-ledger analysis, provenance JSON, local Signals & Tasks, and GitHub Pages demo publishing.

Use Wissenswerk when the goal is an auditable corpus-to-wiki compiler rather than a one-off research workspace.

## Quick Start

```bash
./wissenswerk.py setup --quick --profile local-history --json
./wissenswerk.py ingest --from-ragprep tests/fixtures/ragprep --apply --json
./wissenswerk.py build --apply --json
./wissenswerk.py publish pages --dry-run --json
```

For a one-command local demo:

```bash
./wissenswerk.py demo run --from-ragprep tests/fixtures/ragprep --profile local-history --apply --json
```

The intended release showcase is a local-history workflow: prepared historical documents about a place go in, and a source-backed static wiki suitable for GitHub Pages comes out.

The inspectable productive flow is:

```bash
./wissenswerk.py setup --guided --json
./wissenswerk.py ingest --from-ragprep <dir> --apply --json
./wissenswerk.py analyze --apply --json
./wissenswerk.py plan articles --apply --json
./wissenswerk.py build --apply --json
./wissenswerk.py audit --json
./wissenswerk.py stats --json
./wissenswerk.py publish pages --dry-run --json
```

## Core Principles

- **Markdown-first:** generated knowledge remains inspectable and portable.
- **RagPrep boundary:** parsing, cleanup, and pre-chunking happen before Wissenswerk.
- **Graph before prose:** the compiler analyzes corpus inventory, entities, claims, and concept links before drafting pages.
- **Claims over vibes:** generated articles are compiled from source-backed candidate claims, not free-form summaries alone.
- **No source dumping:** public demos can publish source metadata and generated pages without publishing private full-text corpora.
- **Provenance first:** auto-apply runs produce reports, auditable state, and rollback hints.
- **Provider-neutral:** chat, summary, embeddings, and rerank use OpenAI-compatible endpoints.
- **pgvector default:** PostgreSQL + pgvector is the default retrieval target.
- **Signals, not chat:** agents use local Tasks for anomalies, blockers, handoffs, approvals, audit findings, and run events.
- **Agent-readable contracts:** `AGENTS.md`, `DESIGN.md`, `wissenswerk.yaml`, `project_manifest.json`, JSON CLI output, and future MCP/tool manifests are canonical.

## Roles

Public role IDs are English and stable:

- `coordinator`: run planning, reports, delegation, human escalation.
- `curator`: corpus inventory, RagPrep import, article planning, source mapping.
- `verifier`: citations, conflicts, link checks, provenance and audit.
- `maintainer`: core code, providers, migrations, tests, releases.

Localized display names belong in tenant configuration.

## Signals & Tasks

Wissenswerk uses a local coordination layer instead of a committed message board:

```bash
./wissenswerk.py task raise --type anomaly --severity medium --summary "Unexpected source shape" --json
./wissenswerk.py task list --status submitted --json
./wissenswerk.py task claim TASK-2026-0001 --agent verifier --json
./wissenswerk.py task resolve TASK-2026-0001 --summary "Checked and documented" --json
./wissenswerk.py run status --json
```

Task state lives under `.wissenswerk/tasks/` and is ignored by git. It coordinates work; it is never factual authority.

## Verification

```bash
python3 -m py_compile wissenswerk.py
./wissenswerk.py doctor --json
./wissenswerk.py audit --json
./wissenswerk.py stats --json
./wissenswerk.py export plan --strict --json
./wissenswerk.py test --json
git diff --check
```

To create and verify a candidate public repository tree:

```bash
./wissenswerk.py export materialize --target /tmp/wissenswerk-public --apply --json
./wissenswerk.py export verify --target /tmp/wissenswerk-public --json
```

## GitHub Readiness

The repository includes:

- `README.md`, `LICENSE`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, and `SUPPORT.md`
- issue templates, pull request template, CODEOWNERS, and Python CI under `.github/`
- `.gitignore` coverage for secrets, runtime state, local databases, and generated reports
- `AGENTS.md` and `DESIGN.md` as agent-readable root contracts
- `pyproject.toml` for package metadata and the `wissenswerk` console script

## Documentation

- Agent contract: [AGENTS.md](AGENTS.md)
- Design contract: [DESIGN.md](DESIGN.md)
- CLI and operations: [docs/Wissenswerk/cli.md](docs/Wissenswerk/cli.md)
- Architecture: [docs/Wissenswerk/architecture.md](docs/Wissenswerk/architecture.md)
- Retrieval and memory: [docs/Wissenswerk/retrieval.md](docs/Wissenswerk/retrieval.md)
- Publication readiness: [docs/Wissenswerk/publication-readiness.md](docs/Wissenswerk/publication-readiness.md)

## License

The current repository license is [MIT](LICENSE). Before the first public demo release, the project should make a deliberate release-license decision; the current release plan favors AGPL-3.0-or-later for code and CC BY-SA 4.0 for documentation/demo wiki content.
