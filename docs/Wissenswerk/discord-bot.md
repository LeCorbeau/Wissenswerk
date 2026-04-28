---
layout: wiki_page
title: Wissenswerk Discord Bot
category: Technical
---

# Wissenswerk Discord Bot

The Discord bot is an adapter, not a second knowledge system. It must call the same Retriever/Oracle API that powers `./wissenswerk.py search`.

## Current State

```bash
./wissenswerk.py bot discord --json
```

The current implementation reports dry status and token readiness. It does not connect to Discord yet.

## Target Commands

- `/ww ask <question>`: answer with citations and source scores
- `/ww sources <question>`: show source chunks without synthesis
- `/ww status`: show index/provider/config status
- `/ww rebuild`: admin-only rebuild request or queue trigger
- `!ww <question>`: prefix fallback for simple servers

## Runtime Rules

- Tokens are read only from environment variables such as `DISCORD_BOT_TOKEN`.
- Server/channel allowlists belong in `wissenswerk.yaml`.
- Bot output must include source paths, chunk IDs, scores, and wiki links when available.
- Admin commands must be rate-limited and auditable.
- Tests must use a mock Discord runtime and a mock Retriever; no real Discord connection is required in CI.

## Implementation Path

1. Extract a small Retriever service object from the current lexical search path.
2. Make CLI search and Discord call the same request/response contract.
3. Add optional answer synthesis after retrieval, controlled by provider profile.
4. Add mock bot tests for missing token, status, ask, sources, and admin denial.
