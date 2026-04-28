---
layout: wiki_page
title: Wissenswerk Positioning
category: Technical
---

# Wissenswerk Positioning

Wissenswerk shares the Markdown-first instinct of recent LLM-wiki experiments, but it is not just another personal Claude-Code wiki.

## Borrowed Ideas

- Markdown remains the primary human-readable output.
- Raw sources and generated wiki pages are separated.
- Agents can operate through simple CLI flows.
- Project contracts should be readable by humans and machines.

Useful reference points:

- MindStudio on Karpathy-style LLM wikis: <https://www.mindstudio.ai/blog/andrej-karpathy-llm-wiki-knowledge-base-claude-code>
- Pratiyush `llm-wiki`: <https://github.com/Pratiyush/llm-wiki>
- Charles Chen's wiki example: <https://wiki.charleschen.ai/>

## Differentiators

- RagPrep is the parsing and pre-chunking boundary.
- Provenance, audit, and rollback reports are mandatory, not optional.
- PostgreSQL + pgvector is the target retrieval backbone.
- Providers are OpenAI-compatible and can be self-hosted or remote.
- Agents are IDE-independent through neutral contracts.
- Tenant configuration is explicit, so the same engine can serve many corpora.
- Bot/API usage is an adapter over the same Retriever surface, not a fork.

## Current Weaknesses

- The Python core is still a bootstrap script, not a package.
- Article generation is not yet the full compiler pipeline.
- Test coverage is strong on CLI contracts but weak on unit-level behavior.
- Discord is configured only as a status adapter, not as a live bot.

## Product Thesis

Wissenswerk should become a conservative knowledge compiler for teams that need durable Markdown, source traceability, agent-safe contracts, and operational auditability. Its value is not speed alone; its value is explainable transformation from corpus to maintained wiki.
