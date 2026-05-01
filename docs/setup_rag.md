# Retrieval and Provider Setup

Wissenswerk separates corpus processing, retrieval state, and agent memory.

## Current Split

- `./wissenswerk.py search`: generic search surface; currently lexical bootstrap.
- Target backend: PostgreSQL + pgvector with OpenAI-compatible embedding and rerank endpoints.
- Optional local development backends can be added as adapters, but they must not replace the provider contract.

## Provider Configuration

Providers are configured in `wissenswerk.yaml`:

```json
{
  "providers": {
    "chat": {
      "kind": "openai-compatible",
      "base_url": "https://api.openai.com/v1",
      "api_key_env": "OPENAI_API_KEY",
      "model": "gpt-5.2"
    },
    "summary": {
      "kind": "openai-compatible",
      "base_url": "https://api.openai.com/v1",
      "api_key_env": "OPENAI_API_KEY",
      "model": "gpt-5.2"
    },
    "embedding": {
      "kind": "openai-compatible",
      "base_url": "https://api.openai.com/v1",
      "api_key_env": "OPENAI_API_KEY",
      "model": "text-embedding-3-large"
    }
  }
}
```

Check configuration:

```bash
./wissenswerk.py providers check --json
```

Missing environment variables are reported as runtime status. A provider profile can still be structurally valid.

## Vector Store

The intended default is:

```json
{
  "vector_store": {
    "kind": "pgvector",
    "dsn_env": "WISSENSWERK_DATABASE_URL",
    "schema": "wissenswerk",
    "collection": "example"
  }
}
```

The production implementation should:

1. create schema/table migrations,
2. store source document ID, evidence segment ID, source locator, title, section, language, hash, text, and embedding,
3. support filtered search by `raw`, `wiki`, and `all`,
4. expose scores and evidence metadata,
5. preserve provenance links in answer output.

## RagPrep Import

RagPrep is responsible for parsing, cleanup, and segmentation. Wissenswerk imports the artifacts as source documents and internal evidence segments:

```bash
./wissenswerk.py ingest --from-ragprep <dir> --apply --json
./wissenswerk.py curate --json
```

Required evidence segment fields:

- `document_id`
- `segment_id` or RagPrep `chunk_id`
- `text`

Recommended source locator fields:

- `source_path`
- `archive_id`
- `source_url`

Optional fields:

- `title`
- `section`
- `language`
- `hash`
- `entities`
- `summary`

## Search

```bash
./wissenswerk.py search "question" --source raw --json
./wissenswerk.py search "question" --source wiki --json
./wissenswerk.py search "question" --source all --json
```

## Memory Boundary

Optional memory systems may be used for user or agent working memory. They must not become factual authority. Facts come from source corpus, wiki pages, provenance, and retrieval indexes.
