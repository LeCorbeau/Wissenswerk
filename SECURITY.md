# Security Policy

## Supported Scope

Security reports are accepted for the generic Wissenswerk code, CLI, provider configuration handling, export tooling, and GitHub automation in this repository.

Legacy tenant content, generated reports, local model caches, private corpora, and deployment-specific credentials are outside the public security support scope.

## Reporting a Vulnerability

Please report vulnerabilities privately through GitHub Security Advisories when available. If advisories are not enabled yet, open a minimal issue that says a private security report is needed without posting exploit details.

Include:

- affected version or commit,
- impacted command or component,
- reproduction steps,
- expected impact,
- whether secrets, private corpora, or generated reports are involved.

## Handling Secrets

Never commit API keys, Discord tokens, database URLs, pgvector dumps, private RagPrep outputs, generated memory caches, or provider credentials. The default `.gitignore` excludes common local state and secret patterns.

## Agent Safety

Agents must not run destructive reset or wipe operations without explicit confirmation. `wipe all` requires the token `WIPE-WISSENSWERK`.
