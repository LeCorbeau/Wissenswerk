---
layout: wiki_page
title: Wissenswerk Retrieval and Memory
category: Technical
---

# Wissenswerk Retrieval and Memory

Wissenswerk separates factual knowledge, retrieval state, and agent memory.

## Factual Authority

Facts come from:

1. source corpus
2. generated or curated wiki pages
3. provenance and import state
4. retrieval index derived from the above

Chat history and optional memory providers are never factual authority.

## Retrieval Target

The target retrieval stack is:

- PostgreSQL + pgvector as default vector store.
- OpenAI-compatible embedding endpoint.
- Optional OpenAI-compatible rerank endpoint.
- Search scopes: `raw`, `wiki`, `all`.
- Optional answer synthesis through the configured chat provider.

Current implementation status:

- `search` exists.
- lexical bootstrap search works over configured raw/wiki roots.
- pgvector import and semantic ranking are planned next.

## RagPrep Boundary

RagPrep owns:

- document parsing
- cleanup
- pre-chunking
- optional entity extraction
- optional chunk summaries

Wissenswerk owns:

- validating RagPrep artifacts
- preserving document/chunk identity
- recording provenance
- generating curation plans
- building wiki output
- indexing chunks for retrieval

## Memory Model

Default memory architecture is a Markdown+DB hybrid:

- Markdown for human-readable decisions, session notes, dossiers, and handovers.
- SQLite or PostgreSQL for structured runs, imports, chunk hashes, job state, and audit results.
- pgvector for retrieval vectors.

Optional Honcho integration is allowed for user and agent working memory:

- maintainer preferences
- repeated decisions
- long-lived agent context
- session continuity

Honcho must not replace sources, provenance, or retrieval-derived evidence.

## Reset Semantics

Reset commands are explicit because different state types have different consequences:

- `reset memory`: clears learned working memory; keeps sources, wiki, provenance.
- `reset index`: clears retrieval indexes; marks rebuild required.
- `reset generated`: clears generated reports and temporary classifications.
- `reset wiki`: clears only clearly generated wiki artifacts.
- `wipe tenant`: clears local tenant state while protecting sources and contracts.
- `wipe all`: requires explicit confirmation.

Every reset/wipe plan must be machine-readable and dry-run-first.
