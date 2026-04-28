# Contributing to Wissenswerk

Wissenswerk is a generic corpus-to-wiki platform. Contributions should keep the core portable, auditable, and independent of any single IDE, model provider, tenant, or document collection.

## Contribution Rules

- Use English public IDs for code, commands, role names, schemas, and package-facing documentation.
- Keep labels and generated prose localizable through tenant configuration.
- Treat `./wissenswerk.py`, `wissenswerk.yaml`, `project_manifest.json`, `AGENTS.md`, and `DESIGN.md` as core contracts.
- Do not add unique semantics to IDE-specific adapters. Agent hosts should consume the same neutral contracts.
- Keep generated reports, private corpora, local databases, provider credentials, and memory caches out of version control.

## Development Flow

```bash
./wissenswerk.py doctor --json
./wissenswerk.py providers check --json
./wissenswerk.py design lint --json
./wissenswerk.py export plan --strict --json
./wissenswerk.py test --json
git diff --check
```

Use `--json` where available so agents and CI can parse results reliably.

## Public Export Flow

```bash
./wissenswerk.py export materialize --target /tmp/wissenswerk-public --apply --json
./wissenswerk.py export verify --target /tmp/wissenswerk-public --json
```

The materialized tree must pass its own verification before it is pushed or released.

## Data and Secrets

Never commit provider keys, Discord tokens, database URLs, local databases, pgvector dumps, private RagPrep outputs, local memory caches, or bot session files. Keep small anonymized fixtures under `tests/fixtures/`.

## Pull Requests

Pull requests should include:

- a short summary of behavior changes,
- verification output or a concise list of commands run,
- documentation updates for changed public interfaces,
- explicit notes for migration, reset, wipe, provider, or security implications.

## License

By contributing, you agree that code and documentation contributions are licensed under MIT unless a file states otherwise.
