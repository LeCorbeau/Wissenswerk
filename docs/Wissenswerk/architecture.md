---
layout: wiki_page
title: Wissenswerk Architecture
category: Technical
---

# Wissenswerk Architecture

Wissenswerk is a knowledge compiler, not only a chatbot. The product boundary is:

```text
RagPrep artifacts -> setup/import profile -> source registry -> evidence segments -> corpus analysis -> article plan -> Markdown wiki + provenance + retrieval + reports
```

## Layer Model

| Layer | Purpose | Surface |
|---|---|---|
| Contracts | Human- and agent-readable rules | `AGENTS.md`, `DESIGN.md`, `project_manifest.json` |
| Tenant config | Paths, providers, localization, memory, reset policy | `wissenswerk.yaml` |
| CLI | Portable execution surface | `./wissenswerk.py` |
| Corpus import | Source document registration and evidence segment normalization | `ingest --from-ragprep` |
| Corpus analysis | Inventory, entity registry, claim ledger, concept graph, coverage, conflicts | `analyze` |
| Article planning | Prioritized article candidates and recommended sections | `plan articles` |
| Wiki output | Markdown pages, provenance JSON, reports | `build` |
| Retrieval | Search over raw/wiki/all | `search` |
| Coordination | Local Signals and Tasks | `task`, `run status` |
| Quality | Contract, provider, design, audit, stats, export, and test checks | `doctor`, `audit`, `stats`, `design lint`, `export plan`, `test` |
| Publish | GitHub Pages readiness without remote side effects | `publish pages` |
| Reset | Local state reset and protected wipe planning | `reset`, `wipe` |

## Data Flow

```mermaid
flowchart LR
  A["Document corpus"] --> B["RagPrep parsing and segmentation"]
  B --> C["Wissenswerk ingest"]
  C --> D["Source registry + evidence segments"]
  D --> E["Corpus inventory, claims, concept graph"]
  E --> F["Article plan"]
  F --> G["Markdown wiki + provenance JSON"]
  D --> H["pgvector index"]
  G --> I["Retriever search"]
  H --> I
  I --> J["CLI, bot, future API"]
  F --> K["Reports, audit, statistics"]
```

## Provider Boundary

Wissenswerk configures chat, summary, embedding, and rerank profiles separately. Providers should be OpenAI-compatible endpoints and can run remotely or self-hosted.

The default vector target is PostgreSQL + pgvector.

## Platform Independence

IDE- or host-specific adapters are generated surfaces. Agent hosts should consume the same contracts:

- `AGENTS.md`
- `DESIGN.md`
- JSON CLI output
- tool manifests
- future MCP resources
- `project_manifest.json`

No host-specific file may become the only place where runtime semantics are defined.
