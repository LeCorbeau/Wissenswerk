#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = REPO_ROOT / "wissenswerk.yaml"
DEFAULT_DESIGN = REPO_ROOT / "DESIGN.md"
DEFAULT_MANIFEST = REPO_ROOT / "project_manifest.json"
TASK_TYPES = {"anomaly", "blocker", "handoff", "approval", "audit_finding", "run_event"}
TASK_SEVERITIES = {"low", "medium", "high", "critical"}
TASK_STATUSES = {"submitted", "working", "input-required", "auth-required", "completed", "failed", "canceled", "rejected"}
TASK_TERMINAL_STATUSES = {"completed", "failed", "canceled", "rejected"}
TASK_ROLES = {"coordinator", "curator", "verifier", "maintainer"}
GENERIC_SECTION_TITLES = {"overview", "introduction", "summary", "contents", "content", "notes", "misc", "general"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def progress(message: str, *, enabled: bool = True) -> None:
    if enabled:
        print(f"[wissenswerk] {message}", file=sys.stderr, flush=True)


def load_json_like(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path.name} must be JSON-compatible YAML for this dependency-light bootstrap slice: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain an object at the top level")
    return payload


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    return load_json_like(path)


def repo_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def task_root(config: dict[str, Any]) -> Path:
    paths = config.get("paths", {})
    return repo_path(paths.get("tasks", ".wissenswerk/tasks"))


def write_report(config: dict[str, Any], name: str, payload: dict[str, Any]) -> Path:
    reports_dir = repo_path(config.get("paths", {}).get("reports", "reports/wissenswerk"))
    ensure_dir(reports_dir)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    report_path = reports_dir / f"{stamp}_{name}.json"
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report_path


def json_dumps_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def parse_json_field(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


class TaskStore:
    """Small local task store for agent coordination; never a factual authority."""

    def __init__(self, root: Path):
        self.root = root
        self.active_dir = root / "active"
        self.db_path = root / "tasks.sqlite"

    def connect(self) -> sqlite3.Connection:
        ensure_dir(self.root)
        ensure_dir(self.active_dir)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY,
              type TEXT NOT NULL,
              severity TEXT NOT NULL,
              status TEXT NOT NULL,
              role TEXT NOT NULL,
              summary TEXT NOT NULL,
              evidence_json TEXT NOT NULL,
              dedupe_key TEXT,
              created_by TEXT NOT NULL,
              claimed_by TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              artifacts_json TEXT NOT NULL,
              ttl_days INTEGER NOT NULL,
              parent_id TEXT,
              repeat_count INTEGER NOT NULL DEFAULT 1,
              last_evidence_json TEXT NOT NULL,
              resolution TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_dedupe ON tasks(dedupe_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at)")
        conn.commit()
        return conn

    def next_id(self, conn: sqlite3.Connection) -> str:
        year = datetime.now(timezone.utc).year
        prefix = f"TASK-{year}-"
        rows = conn.execute("SELECT id FROM tasks WHERE id LIKE ?", (f"{prefix}%",)).fetchall()
        highest = 0
        for row in rows:
            match = re.fullmatch(rf"{re.escape(prefix)}(\d+)", str(row["id"]))
            if not match:
                continue
            highest = max(highest, int(match.group(1)))
        return f"{prefix}{highest + 1:04d}"

    def row_to_task(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "type": row["type"],
            "severity": row["severity"],
            "status": row["status"],
            "role": row["role"],
            "summary": row["summary"],
            "evidence": parse_json_field(row["evidence_json"], []),
            "dedupe_key": row["dedupe_key"] or "",
            "created_by": row["created_by"],
            "claimed_by": row["claimed_by"] or None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "artifacts": parse_json_field(row["artifacts_json"], []),
            "ttl_days": int(row["ttl_days"]),
            "parent_id": row["parent_id"] or None,
            "repeat_count": int(row["repeat_count"]),
            "last_evidence": parse_json_field(row["last_evidence_json"], []),
            "resolution": row["resolution"] or "",
        }

    def get(self, task_id: str) -> dict[str, Any] | None:
        with contextlib.closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return self.row_to_task(row) if row else None

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        with contextlib.closing(self.connect()) as conn:
            if status:
                rows = conn.execute("SELECT * FROM tasks WHERE status = ? ORDER BY updated_at DESC, id DESC", (status,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC, id DESC").fetchall()
            return [self.row_to_task(row) for row in rows]

    def raise_signal(
        self,
        *,
        task_type: str,
        severity: str,
        summary: str,
        role: str = "coordinator",
        evidence: list[str] | None = None,
        dedupe_key: str = "",
        created_by: str = "agent",
        artifacts: list[str] | None = None,
        ttl_days: int = 30,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        if task_type not in TASK_TYPES:
            raise ValueError(f"Unsupported task type: {task_type}")
        if severity not in TASK_SEVERITIES:
            raise ValueError(f"Unsupported task severity: {severity}")
        if role not in TASK_ROLES:
            raise ValueError(f"Unsupported task role: {role}")
        evidence = evidence or []
        artifacts = artifacts or []
        timestamp = now_iso()
        with contextlib.closing(self.connect()) as conn:
            if dedupe_key:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE dedupe_key = ? AND status NOT IN ('completed','failed','canceled','rejected') ORDER BY updated_at DESC LIMIT 1",
                    (dedupe_key,),
                ).fetchone()
                if row:
                    repeat_count = int(row["repeat_count"]) + 1
                    conn.execute(
                        """
                        UPDATE tasks
                        SET summary = ?, severity = ?, role = ?, evidence_json = ?, last_evidence_json = ?,
                            updated_at = ?, repeat_count = ?
                        WHERE id = ?
                        """,
                        (
                            summary,
                            severity,
                            role,
                            json_dumps_compact(evidence),
                            json_dumps_compact(evidence),
                            timestamp,
                            repeat_count,
                            row["id"],
                        ),
                    )
                    conn.commit()
                    task = self.get(str(row["id"]))
                    if task:
                        self.write_active_markdown(task)
                        return {"status": "deduped", "task": task}
            task_id = ""
            for _ in range(25):
                task_id = self.next_id(conn)
                try:
                    conn.execute(
                        """
                        INSERT INTO tasks (
                          id, type, severity, status, role, summary, evidence_json, dedupe_key,
                          created_by, claimed_by, created_at, updated_at, artifacts_json, ttl_days,
                          parent_id, repeat_count, last_evidence_json, resolution
                        )
                        VALUES (?, ?, ?, 'submitted', ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, 1, ?, NULL)
                        """,
                        (
                            task_id,
                            task_type,
                            severity,
                            role,
                            summary,
                            json_dumps_compact(evidence),
                            dedupe_key or None,
                            created_by,
                            timestamp,
                            timestamp,
                            json_dumps_compact(artifacts),
                            ttl_days,
                            parent_id,
                            json_dumps_compact(evidence),
                        ),
                    )
                    conn.commit()
                    break
                except sqlite3.IntegrityError as exc:
                    conn.rollback()
                    if "tasks.id" not in str(exc):
                        raise
            else:
                raise RuntimeError("Could not allocate a unique task id after 25 attempts")
        task = self.get(task_id)
        if not task:
            raise RuntimeError(f"Task was not created: {task_id}")
        self.write_active_markdown(task)
        return {"status": "created", "task": task}

    def transition(self, task_id: str, *, status: str, summary: str = "", claimed_by: str | None = None) -> dict[str, Any]:
        if status not in TASK_STATUSES:
            raise ValueError(f"Unsupported task status: {status}")
        with contextlib.closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            current = str(row["status"])
            if current in TASK_TERMINAL_STATUSES:
                raise ValueError(f"Task {task_id} is terminal: {current}")
            timestamp = now_iso()
            resolution = summary if status in TASK_TERMINAL_STATUSES else row["resolution"]
            claimer = claimed_by if claimed_by is not None else row["claimed_by"]
            conn.execute(
                "UPDATE tasks SET status = ?, claimed_by = ?, updated_at = ?, resolution = ? WHERE id = ?",
                (status, claimer, timestamp, resolution, task_id),
            )
            conn.commit()
        task = self.get(task_id)
        if not task:
            raise KeyError(task_id)
        if status in TASK_TERMINAL_STATUSES:
            self.remove_active_markdown(task_id)
        else:
            self.write_active_markdown(task)
        return task

    def claim(self, task_id: str, agent: str) -> dict[str, Any]:
        if agent not in TASK_ROLES:
            raise ValueError(f"Unsupported agent role: {agent}")
        return self.transition(task_id, status="working", claimed_by=agent)

    def resolve(self, task_id: str, summary: str) -> dict[str, Any]:
        return self.transition(task_id, status="completed", summary=summary)

    def reject(self, task_id: str, reason: str) -> dict[str, Any]:
        return self.transition(task_id, status="rejected", summary=reason)

    def status_counts(self) -> dict[str, int]:
        with contextlib.closing(self.connect()) as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status").fetchall()
            return {str(row["status"]): int(row["count"]) for row in rows}

    def blocking_tasks(self) -> list[dict[str, Any]]:
        tasks = self.list()
        return [
            task
            for task in tasks
            if task["status"] not in TASK_TERMINAL_STATUSES
            and (task["severity"] == "critical" or task["type"] == "approval" or task["status"] in {"input-required", "auth-required"})
        ]

    def digest(self, since: datetime) -> dict[str, Any]:
        tasks = self.list()
        open_tasks = [task for task in tasks if task["status"] not in TASK_TERMINAL_STATUSES]
        new_tasks = [task for task in tasks if parse_task_time(task["created_at"]) >= since]
        return {
            "status": "ok",
            "generated_at": now_iso(),
            "since": since.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "counts": self.status_counts(),
            "open": open_tasks,
            "blocking": self.blocking_tasks(),
            "new": new_tasks,
        }

    def write_active_markdown(self, task: dict[str, Any]) -> None:
        if task["status"] in TASK_TERMINAL_STATUSES:
            self.remove_active_markdown(task["id"])
            return
        ensure_dir(self.active_dir)
        path = self.active_dir / f"{task['id']}.md"
        lines = [
            "---",
            f"id: {task['id']}",
            f"type: {task['type']}",
            f"severity: {task['severity']}",
            f"status: {task['status']}",
            f"role: {task['role']}",
            f"created_at: {task['created_at']}",
            f"updated_at: {task['updated_at']}",
            "---",
            "",
            f"# {task['id']}: {task['summary']}",
            "",
            f"- Created by: `{task['created_by']}`",
            f"- Claimed by: `{task['claimed_by'] or '[unclaimed]'}`",
            f"- Dedupe key: `{task['dedupe_key'] or '[none]'}`",
            f"- Repeat count: {task['repeat_count']}",
            "",
            "## Evidence",
            "",
        ]
        evidence = task.get("evidence", [])
        lines.extend(f"- `{item}`" for item in evidence or ["[none]"])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def remove_active_markdown(self, task_id: str) -> None:
        path = self.active_dir / f"{task_id}.md"
        if path.exists():
            path.unlink()


def parse_task_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_since(value: str) -> datetime:
    match = re.fullmatch(r"(\d+)([hdw])", value.strip())
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        delta = {"h": timedelta(hours=amount), "d": timedelta(days=amount), "w": timedelta(weeks=amount)}[unit]
        return datetime.now(timezone.utc) - delta
    return parse_task_time(value)


def default_task_store(config: dict[str, Any]) -> TaskStore:
    return TaskStore(task_root(config))


def default_config_payload() -> dict[str, Any]:
    return {
        "schema_version": "wissenswerk.config.v1",
        "project": {
            "name": "Example Corpus",
            "product": "Wissenswerk",
            "language": "de",
            "tenant_id": "example",
            "type": "general",
        },
        "paths": {
            "sources": ["corpus"],
            "wiki": "docs/Wiki",
            "reports": "reports/wissenswerk",
            "corpus": ".wissenswerk/corpus",
            "runtime_state": ".wissenswerk/state",
            "tasks": ".wissenswerk/tasks",
            "profile": ".wissenswerk/profile",
            "analysis": ".wissenswerk/analysis",
            "article_plans": ".wissenswerk/article_plans",
            "project_docs": "docs",
        },
        "source_precedence": ["Primary Sources", "Derived Notes", "Wiki Pages"],
        "localization": {
            "default_locale": "en",
            "available_locales": ["en", "de"],
        },
        "automation": {
            "auto_apply": True,
            "write_reports": True,
            "require_provenance": True,
            "rollback_hint": True,
        },
        "providers": {
            "chat": {
                "kind": "openai-compatible",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "model": "gpt-5.2",
            },
            "summary": {
                "kind": "openai-compatible",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "model": "gpt-5.2",
            },
            "embedding": {
                "kind": "openai-compatible",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "model": "text-embedding-3-large",
            },
        },
        "vector_store": {
            "kind": "pgvector",
            "dsn_env": "WISSENSWERK_DATABASE_URL",
            "schema": "wissenswerk",
            "collection": "example",
        },
        "memory": {
            "default": "markdown-db-hybrid",
            "facts_authority": ["sources", "wiki", "provenance", "retrieval"],
            "optional_providers": {
                "honcho": {"enabled": False, "api_key_env": "HONCHO_API_KEY"},
            },
        },
        "agents": {
            "roles": ["coordinator", "curator", "verifier", "maintainer"],
            "low_cost_profile": "summary",
        },
        "ragprep": {
            "accepted_extensions": [".json", ".jsonl"],
            "required_fields": ["document_id", "segment_id", "text"],
            "source_locator_fields": ["source_path", "archive_id", "source_url"],
            "optional_fields": ["title", "section", "language", "hash", "entities", "summary", "author", "year", "page", "keywords", "archive_id", "source_url", "rights"],
        },
        "publication": {
            "mode": "private-sources-public-wiki",
            "target": "github-pages",
            "include_source_fulltext": False,
            "include_source_metadata": True,
        },
    }


def command_init(args: argparse.Namespace) -> int:
    target = repo_path(args.config)
    if target.exists() and not args.force:
        payload = {"status": "exists", "config": rel(target), "changed": False}
        json_print(payload) if args.json else print(f"{rel(target)} already exists")
        return 0
    target.write_text(json.dumps(default_config_payload(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    payload = {"status": "created", "config": rel(target), "changed": True}
    json_print(payload) if args.json else print(f"Created {rel(target)}")
    return 0


def write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def safe_slug(value: str, fallback: str = "project") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def corpus_dir(config: dict[str, Any]) -> Path:
    return repo_path(config.get("paths", {}).get("corpus", ".wissenswerk/corpus"))


def has_source_locator(segment: dict[str, Any]) -> bool:
    return bool(segment.get("source_path") or segment.get("archive_id") or segment.get("source_url"))


def source_document_id(segment: dict[str, Any]) -> str:
    return str(segment.get("source_document_id") or segment.get("document_id") or "")


def evidence_segment_id(segment: dict[str, Any]) -> str:
    return str(segment.get("evidence_segment_id") or segment.get("segment_id") or segment.get("chunk_id") or "")


def profile_payload(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    project_name = args.project_name or config.get("project", {}).get("name") or "Example Corpus"
    project_type = args.profile or config.get("project", {}).get("type") or "local-history"
    language = args.language or config.get("project", {}).get("language") or "en"
    publication_mode = args.publication_mode or config.get("publication", {}).get("mode") or "private-sources-public-wiki"
    source_languages = [item.strip() for item in (args.source_languages or language).split(",") if item.strip()]
    audience = [item.strip() for item in (args.audience or "interested public,agents").split(",") if item.strip()]
    return {
        "schema_version": "wissenswerk.project-profile.v1",
        "generated_at": now_iso(),
        "mode": "guided" if args.guided else "quick",
        "project": {
            "name": project_name,
            "type": project_type,
            "tenant_id": safe_slug(project_name),
            "output_language": language,
            "source_languages": source_languages,
        },
        "corpus": {
            "ragprep_input": args.from_ragprep or "",
            "expected_shape": "source documents with RagPrep evidence segments in JSON/JSONL",
            "import_contract": {
        "source_documents": "Human-facing source works such as books, PDFs, OCR Markdown, articles, or archival records.",
                "evidence_segments": "Technical RagPrep sections used for evidence, provenance, and retrieval.",
                "source_archive": "Private or public source archive referenced by source_path, archive_id, or source_url.",
                "publication_policy": publication_mode,
            },
            "preview": {},
        },
        "publication": {
            "mode": publication_mode,
            "target": "github-pages",
            "include_source_fulltext": False,
            "include_source_metadata": True,
        },
        "wiki_policy": {
            "audience": audience,
            "citation_policy": args.citation_policy or "section",
            "article_granularity": args.article_granularity or "medium",
            "uncertainty_policy": args.uncertainty_policy or "explicit",
            "preserve_original_terms": True,
        },
        "agent_workflow": [
            "doctor",
            "task digest",
            "ingest",
            "analyze",
            "plan articles",
            "build",
            "audit",
            "stats",
            "demo report",
            "publish pages --dry-run",
        ],
    }


def profile_paths(config: dict[str, Any]) -> dict[str, Path]:
    profile_root = repo_path(config.get("paths", {}).get("profile", ".wissenswerk/profile"))
    docs_root = repo_path(config.get("paths", {}).get("project_docs", "docs"))
    return {
        "profile_json": profile_root / "project_profile.json",
        "project_md": docs_root / "Project_Profile.md",
        "source_policy_md": docs_root / "Source_Policy.md",
        "style_guide_md": docs_root / "Wiki_Style_Guide.md",
    }


def write_project_profile_docs(config: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    paths = profile_paths(config)
    project = profile["project"]
    publication = profile["publication"]
    policy = profile["wiki_policy"]
    write_json(paths["profile_json"], profile)
    ensure_dir(paths["project_md"].parent)
    paths["project_md"].write_text(
        "\n".join(
            [
                "# Project Profile",
                "",
                f"- Project: {project['name']}",
                f"- Type: {project['type']}",
                f"- Output language: {project['output_language']}",
                f"- Source languages: {', '.join(project['source_languages']) or '[unknown]'}",
                f"- Publication mode: {publication['mode']}",
                f"- Corpus import path: {profile.get('corpus', {}).get('ragprep_input') or '[not configured]'}",
                "",
                "## Corpus Preview",
                "",
                f"- Source documents: {profile.get('corpus', {}).get('preview', {}).get('source_documents', 0)}",
                f"- Evidence segments: {profile.get('corpus', {}).get('preview', {}).get('evidence_segments', 0)}",
                f"- Source locator coverage: {profile.get('corpus', {}).get('preview', {}).get('source_locator_coverage', '[not scanned]')}",
                "",
                "## Agent Workflow",
                "",
                "Agents must analyze the whole corpus before drafting wiki prose.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    paths["source_policy_md"].write_text(
        "\n".join(
            [
                "# Source Policy",
                "",
                "- Source full text is private by default.",
                "- Public wiki output may include source titles, archive IDs, external links, short evidence references, and generated summaries.",
                "- Local private paths must not be published.",
                "- Unknown rights or provenance risks must raise a Wissenswerk Task.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    paths["style_guide_md"].write_text(
        "\n".join(
            [
                "# Wiki Style Guide",
                "",
                f"- Audience: {', '.join(policy['audience'])}",
                f"- Citation policy: {policy['citation_policy']}",
                f"- Article granularity: {policy['article_granularity']}",
                f"- Uncertainty policy: {policy['uncertainty_policy']}",
                "- Use restrained, source-backed prose.",
                "- Preserve original local or historical terms on first mention when useful.",
                "- Mark unresolved facts explicitly instead of smoothing over uncertainty.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return [rel(path) for path in paths.values()]


def command_setup(args: argparse.Namespace) -> int:
    config_path = repo_path(args.config)
    config = load_config(config_path) if config_path.exists() else default_config_payload()
    profile = profile_payload(args, config)
    if profile["corpus"]["ragprep_input"]:
        preview = discover_corpus_preview(repo_path(profile["corpus"]["ragprep_input"]), profile["project"]["output_language"])
        profile["corpus"]["preview"] = preview
    config["project"]["name"] = profile["project"]["name"]
    config["project"]["type"] = profile["project"]["type"]
    config["project"]["tenant_id"] = profile["project"]["tenant_id"]
    config["project"]["language"] = profile["project"]["output_language"]
    if profile["corpus"]["ragprep_input"]:
        config["paths"]["sources"] = [profile["corpus"]["ragprep_input"]]
    config["publication"] = profile["publication"]
    written = write_project_profile_docs(config, profile)
    ensure_dir(config_path.parent)
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    written.insert(0, rel(config_path))
    payload = {
        "status": "ready",
        "mode": profile["mode"],
        "profile": profile,
        "written": written,
        "next_commands": [
            "./wissenswerk.py ingest --from-ragprep <dir> --apply --json",
            "./wissenswerk.py build --apply --json",
            "./wissenswerk.py publish pages --dry-run --json",
        ],
    }
    json_print(payload) if args.json else print(f"Wissenswerk setup: {payload['status']} ({profile['mode']})")
    return 0


def provider_status(config: dict[str, Any]) -> dict[str, Any]:
    providers = config.get("providers", {})
    checks = []
    for name, provider in sorted(providers.items()):
        key_env = provider.get("api_key_env", "")
        structural_status = "configured" if provider.get("base_url") and provider.get("model") else "incomplete"
        checks.append(
            {
                "name": name,
                "kind": provider.get("kind", ""),
                "base_url": provider.get("base_url", ""),
                "model": provider.get("model", ""),
                "api_key_env": key_env,
                "env_present": bool(key_env and os.environ.get(key_env)),
                "status": structural_status,
                "runtime_status": "ready" if key_env and os.environ.get(key_env) else "missing_credentials",
            }
        )
    vector = config.get("vector_store", {})
    dsn_env = vector.get("dsn_env", "")
    incomplete = [provider for provider in checks if provider["status"] != "configured"]
    missing_credentials = [provider for provider in checks if provider["runtime_status"] != "ready"]
    return {
        "status": "incomplete" if incomplete else "configured",
        "runtime_status": "missing_credentials" if missing_credentials or not os.environ.get(dsn_env, "") else "ready",
        "providers": checks,
        "vector_store": {
            "kind": vector.get("kind", ""),
            "dsn_env": dsn_env,
            "env_present": bool(dsn_env and os.environ.get(dsn_env)),
            "runtime_status": "ready" if dsn_env and os.environ.get(dsn_env) else "missing_credentials",
            "schema": vector.get("schema", ""),
            "collection": vector.get("collection", ""),
        },
    }


def command_providers_check(args: argparse.Namespace) -> int:
    payload = provider_status(load_config(repo_path(args.config)))
    json_print(payload) if args.json else print_provider_status(payload)
    return 0


def print_provider_status(payload: dict[str, Any]) -> None:
    print(f"Wissenswerk provider status: {payload['status']} ({payload['runtime_status']})")
    for provider in payload["providers"]:
        env = "present" if provider["env_present"] else "missing"
        print(f"- {provider['name']}: {provider['kind']} {provider['model']} ({env})")
    vector = payload["vector_store"]
    env = "present" if vector["env_present"] else "missing"
    print(f"- vector_store: {vector['kind']} schema={vector['schema']} ({env})")


def task_payload(status: str, task: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status}
    if task is not None:
        payload["task"] = task
    payload.update(extra)
    return payload


def command_task_raise(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    try:
        result = default_task_store(config).raise_signal(
            task_type=args.type,
            severity=args.severity,
            summary=args.summary,
            role=args.role,
            evidence=args.evidence,
            dedupe_key=args.dedupe_key,
            created_by=args.created_by,
            artifacts=args.artifact,
            ttl_days=args.ttl_days,
            parent_id=args.parent_id or None,
        )
    except ValueError as exc:
        payload = {"status": "fail", "error": str(exc)}
        json_print(payload) if args.json else print(payload["error"])
        return 2
    json_print(result) if args.json else print(f"{result['status']}: {result['task']['id']}")
    return 0


def command_task_list(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    tasks = default_task_store(config).list(args.status)
    payload = {"status": "ok", "tasks": tasks, "count": len(tasks)}
    json_print(payload) if args.json else print_task_list(payload)
    return 0


def command_task_show(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    task = default_task_store(config).get(args.id)
    if not task:
        payload = {"status": "not_found", "id": args.id}
        json_print(payload) if args.json else print(f"Task not found: {args.id}")
        return 1
    payload = {"status": "ok", "task": task}
    json_print(payload) if args.json else print_task(task)
    return 0


def command_task_claim(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    try:
        task = default_task_store(config).claim(args.id, args.agent)
    except (KeyError, ValueError) as exc:
        payload = {"status": "fail", "error": str(exc), "id": args.id}
        json_print(payload) if args.json else print(payload["error"])
        return 2
    payload = task_payload("claimed", task)
    json_print(payload) if args.json else print(f"claimed: {task['id']}")
    return 0


def command_task_resolve(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    try:
        task = default_task_store(config).resolve(args.id, args.summary)
    except (KeyError, ValueError) as exc:
        payload = {"status": "fail", "error": str(exc), "id": args.id}
        json_print(payload) if args.json else print(payload["error"])
        return 2
    payload = task_payload("resolved", task)
    json_print(payload) if args.json else print(f"resolved: {task['id']}")
    return 0


def command_task_reject(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    try:
        task = default_task_store(config).reject(args.id, args.reason)
    except (KeyError, ValueError) as exc:
        payload = {"status": "fail", "error": str(exc), "id": args.id}
        json_print(payload) if args.json else print(payload["error"])
        return 2
    payload = task_payload("rejected", task)
    json_print(payload) if args.json else print(f"rejected: {task['id']}")
    return 0


def command_task_digest(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    try:
        since = parse_since(args.since)
    except ValueError as exc:
        payload = {"status": "fail", "error": f"Invalid --since value: {exc}"}
        json_print(payload) if args.json else print(payload["error"])
        return 2
    payload = default_task_store(config).digest(since)
    json_print(payload) if args.json else print_task_digest(payload)
    return 0


def command_run_status(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    store = default_task_store(config)
    payload = {
        "status": "ok",
        "generated_at": now_iso(),
        "task_store": rel(store.db_path),
        "counts": store.status_counts(),
        "blocking": store.blocking_tasks(),
        "facts_authority": ["sources", "wiki", "provenance", "retrieval"],
    }
    json_print(payload) if args.json else print_run_status(payload)
    return 0


def print_task(task: dict[str, Any]) -> None:
    print(f"{task['id']} [{task['status']}] {task['severity']} {task['type']}: {task['summary']}")


def print_task_list(payload: dict[str, Any]) -> None:
    print(f"Wissenswerk tasks: {payload['count']}")
    for task in payload["tasks"]:
        print_task(task)


def print_task_digest(payload: dict[str, Any]) -> None:
    print(f"Wissenswerk task digest: {len(payload['open'])} open, {len(payload['blocking'])} blocking")
    for task in payload["blocking"]:
        print_task(task)


def print_run_status(payload: dict[str, Any]) -> None:
    print("Wissenswerk run status: ok")
    print(f"- task store: {payload['task_store']}")
    print(f"- blocking tasks: {len(payload['blocking'])}")


def parse_design_file(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise ValueError("DESIGN.md must start with YAML front matter")
    end = raw.find("\n---", 3)
    if end == -1:
        raise ValueError("DESIGN.md front matter is not closed")
    frontmatter = raw[3:end].strip()
    body = raw[end + 4 :].lstrip()
    try:
        tokens = json.loads(frontmatter)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "This bootstrap linter expects JSON-compatible YAML front matter in DESIGN.md"
        ) from exc
    if not isinstance(tokens, dict):
        raise ValueError("DESIGN.md front matter must contain an object")
    return tokens, body


def resolve_token(tokens: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, str):
        return value
    match = re.fullmatch(r"\{([^}]+)\}", value.strip())
    if not match:
        return value
    current: Any = tokens
    for part in match.group(1).split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(match.group(1))
        current = current[part]
    return current


def _hex_to_rgb(value: str) -> tuple[float, float, float] | None:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        return None
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (1, 3, 5))


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def contrast_ratio(fg: str, bg: str) -> float | None:
    fg_rgb = _hex_to_rgb(fg)
    bg_rgb = _hex_to_rgb(bg)
    if fg_rgb is None or bg_rgb is None:
        return None
    fg_lum = 0.2126 * _linear(fg_rgb[0]) + 0.7152 * _linear(fg_rgb[1]) + 0.0722 * _linear(fg_rgb[2])
    bg_lum = 0.2126 * _linear(bg_rgb[0]) + 0.7152 * _linear(bg_rgb[1]) + 0.0722 * _linear(bg_rgb[2])
    lighter = max(fg_lum, bg_lum)
    darker = min(fg_lum, bg_lum)
    return (lighter + 0.05) / (darker + 0.05)


def lint_design(path: Path) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    try:
        tokens, body = parse_design_file(path)
    except Exception as exc:
        return {
            "status": "fail",
            "path": rel(path),
            "findings": [{"severity": "error", "message": str(exc)}],
            "summary": {"errors": 1, "warnings": 0, "info": 0},
        }

    required = ["name", "colors", "typography", "spacing", "components"]
    for key in required:
        if key not in tokens:
            findings.append({"severity": "error", "path": key, "message": f"Missing token section: {key}"})

    if "primary" not in tokens.get("colors", {}):
        findings.append({"severity": "warning", "path": "colors.primary", "message": "Missing primary color token"})

    section_order = [
        "Overview",
        "Colors",
        "Typography",
        "Layout",
        "Elevation & Depth",
        "Shapes",
        "Components",
        "Do's and Don'ts",
    ]
    positions = []
    for section in section_order:
        match = re.search(rf"^##\s+{re.escape(section)}\s*$", body, re.MULTILINE)
        if match:
            positions.append((section, match.start()))
    if positions != sorted(positions, key=lambda item: item[1]):
        findings.append({"severity": "error", "path": "sections", "message": "DESIGN.md sections are out of order"})

    seen_sections: set[str] = set()
    for match in re.finditer(r"^##\s+(.+?)\s*$", body, re.MULTILINE):
        section = match.group(1).strip()
        if section in seen_sections:
            findings.append({"severity": "error", "path": f"section.{section}", "message": "Duplicate section heading"})
        seen_sections.add(section)

    for component_name, component in tokens.get("components", {}).items():
        if not isinstance(component, dict):
            continue
        try:
            bg = resolve_token(tokens, component.get("backgroundColor"))
            fg = resolve_token(tokens, component.get("textColor"))
        except KeyError as exc:
            findings.append(
                {
                    "severity": "error",
                    "path": f"components.{component_name}",
                    "message": f"Broken token reference: {exc}",
                }
            )
            continue
        ratio = contrast_ratio(str(fg), str(bg))
        if ratio is not None and ratio < 4.5:
            findings.append(
                {
                    "severity": "warning",
                    "path": f"components.{component_name}",
                    "message": f"Contrast ratio {ratio:.2f}:1 is below WCAG AA.",
                }
            )

    findings.append(
        {
            "severity": "info",
            "path": "tokens",
            "message": (
                f"colors={len(tokens.get('colors', {}))}, "
                f"typography={len(tokens.get('typography', {}))}, "
                f"components={len(tokens.get('components', {}))}"
            ),
        }
    )
    errors = sum(1 for finding in findings if finding["severity"] == "error")
    warnings = sum(1 for finding in findings if finding["severity"] == "warning")
    info = sum(1 for finding in findings if finding["severity"] == "info")
    return {
        "status": "fail" if errors else "pass",
        "path": rel(path),
        "findings": findings,
        "summary": {"errors": errors, "warnings": warnings, "info": info},
    }


def command_design_lint(args: argparse.Namespace) -> int:
    payload = lint_design(repo_path(args.file))
    json_print(payload) if args.json else print_design_lint(payload)
    return 1 if payload["summary"]["errors"] else 0


def print_design_lint(payload: dict[str, Any]) -> None:
    print(f"DESIGN.md lint: {payload['status']}")
    for finding in payload["findings"]:
        print(f"- {finding['severity']}: {finding.get('path', '-')}: {finding['message']}")


def iter_ragprep_records(import_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    files = sorted([p for p in import_dir.rglob("*") if p.suffix.lower() in {".json", ".jsonl"}])
    for file_path in files:
        if file_path.suffix.lower() == ".jsonl":
            for line_no, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    item["_ragprep_file"] = rel(file_path)
                    item["_ragprep_line"] = line_no
                    records.append(item)
            continue
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        items: list[Any]
        if isinstance(payload, dict) and isinstance(payload.get("evidence_segments"), list):
            items = payload["evidence_segments"]
        elif isinstance(payload, dict) and isinstance(payload.get("segments"), list):
            items = payload["segments"]
        elif isinstance(payload, dict) and isinstance(payload.get("chunks"), list):
            items = payload["chunks"]
        elif isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = [payload]
        else:
            items = []
        for item in items:
            if isinstance(item, dict):
                item["_ragprep_file"] = rel(file_path)
                records.append(item)
    return records


def normalize_evidence_segment(record: dict[str, Any], default_language: str) -> dict[str, Any]:
    text = str(record.get("text") or record.get("content") or record.get("chunk_text") or "")
    source_path = str(record.get("source_path") or record.get("path") or record.get("source") or "")
    title = str(record.get("title") or Path(source_path).stem or record.get("document_title") or "")
    document_id = str(record.get("document_id") or record.get("source_document_id") or record.get("doc_id") or source_path or title or record.get("_ragprep_file", ""))
    segment_seed = str(record.get("segment_id") or record.get("evidence_segment_id") or record.get("chunk_id") or record.get("id") or hashlib.sha1(text.encode("utf-8")).hexdigest()[:16])
    segment_id = str(segment_seed)
    return {
        "source_document_id": document_id,
        "document_id": document_id,
        "evidence_segment_id": segment_id,
        "segment_id": segment_id,
        "text": text,
        "source_path": source_path,
        "title": title or document_id,
        "section": str(record.get("section") or record.get("heading") or ""),
        "language": str(record.get("language") or default_language),
        "hash": str(record.get("hash") or hashlib.sha256(text.encode("utf-8")).hexdigest()),
        "entities": record.get("entities", []),
        "summary": str(record.get("summary") or ""),
        "author": str(record.get("author") or ""),
        "year": str(record.get("year") or record.get("publication_year") or ""),
        "page": str(record.get("page") or record.get("page_number") or ""),
        "keywords": record.get("keywords", []),
        "archive_id": str(record.get("archive_id") or record.get("archive") or ""),
        "source_url": str(record.get("source_url") or record.get("url") or ""),
        "rights": str(record.get("rights") or record.get("license") or ""),
        "ragprep_file": record.get("_ragprep_file", ""),
    }


def validate_evidence_segments(segments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    validation_findings = []
    metadata_findings = []
    for index, segment in enumerate(segments):
        missing = []
        if not source_document_id(segment):
            missing.append("source_document_id")
        if not evidence_segment_id(segment):
            missing.append("segment_id")
        if not segment.get("text"):
            missing.append("text")
        if missing:
            validation_findings.append(
                {
                    "index": index,
                    "source_document_id": source_document_id(segment),
                    "document_id": str(segment.get("document_id") or ""),
                    "segment_id": evidence_segment_id(segment),
                    "missing": missing,
                }
            )
        if not has_source_locator(segment):
            metadata_findings.append(
                {
                    "index": index,
                    "source_document_id": source_document_id(segment),
                    "document_id": str(segment.get("document_id") or ""),
                    "segment_id": evidence_segment_id(segment),
                    "missing": ["source_path|archive_id|source_url"],
                    "severity": "medium",
                    "message": "Evidence segment has no source locator. Ingest can continue, but publication/source review is required.",
                }
            )
    return validation_findings, metadata_findings


def source_documents_from_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        doc_id = source_document_id(segment) or "unknown"
        doc = documents.setdefault(
            doc_id,
            {
                "source_document_id": doc_id,
                "document_id": doc_id,
                "title": str(segment.get("title") or doc_id),
                "author": str(segment.get("author") or ""),
                "year": str(segment.get("year") or ""),
                "language": str(segment.get("language") or ""),
                "source_path": str(segment.get("source_path") or ""),
                "archive_id": str(segment.get("archive_id") or ""),
                "source_url": str(segment.get("source_url") or ""),
                "rights": str(segment.get("rights") or ""),
                "segments": 0,
                "sections": set(),
                "metadata_quality": "complete",
            },
        )
        doc["segments"] += 1
        for field in ("title", "author", "year", "language", "source_path", "archive_id", "source_url", "rights"):
            if not doc.get(field) and segment.get(field):
                doc[field] = str(segment.get(field) or "")
        if segment.get("section"):
            doc["sections"].add(str(segment["section"]))
    normalized: list[dict[str, Any]] = []
    for doc in documents.values():
        doc["sections"] = sorted(doc["sections"])
        if not (doc.get("source_path") or doc.get("archive_id") or doc.get("source_url")):
            doc["metadata_quality"] = "missing_source_locator"
        elif not doc.get("author") or not doc.get("year") or not doc.get("rights"):
            doc["metadata_quality"] = "partial"
        normalized.append(doc)
    return sorted(normalized, key=lambda item: str(item.get("title", "")).lower())


def corpus_preview_from_segments(segments: list[dict[str, Any]]) -> dict[str, Any]:
    source_documents = source_documents_from_segments(segments)
    located_documents = sum(1 for doc in source_documents if doc.get("source_path") or doc.get("archive_id") or doc.get("source_url"))
    validation_findings, metadata_findings = validate_evidence_segments(segments)
    return {
        "source_documents": len(source_documents),
        "evidence_segments": len(segments),
        "source_locator_coverage": f"{located_documents}/{len(source_documents)}" if source_documents else "0/0",
        "metadata_findings": len(metadata_findings),
        "validation_findings": len(validation_findings),
        "languages": sorted({str(segment.get("language") or "") for segment in segments if isinstance(segment, dict) and segment.get("language")}),
        "recommended_next_commands": [
            "./wissenswerk.py ingest --from-ragprep <dir> --apply --json",
            "./wissenswerk.py analyze --apply --json",
            "./wissenswerk.py plan articles --apply --json",
        ],
    }


def discover_corpus_preview(import_dir: Path, default_language: str) -> dict[str, Any]:
    if not import_dir.exists():
        return {"status": "missing", "path": rel(import_dir), "source_documents": 0, "evidence_segments": 0}
    records = iter_ragprep_records(import_dir)
    segments = [normalize_evidence_segment(record, default_language) for record in records]
    return {"status": "scanned", "path": rel(import_dir), **corpus_preview_from_segments(segments)}


def command_ingest(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    import_dir = repo_path(args.from_ragprep)
    progress(f"ingest: scanning RagPrep artifacts in {rel(import_dir)}")
    if not import_dir.exists():
        payload = {"status": "fail", "error": f"RagPrep import directory missing: {rel(import_dir)}"}
        json_print(payload) if args.json else print(payload["error"])
        return 2
    raw_records = iter_ragprep_records(import_dir)
    progress(f"ingest: loaded {len(raw_records)} raw record(s)")
    segments = [normalize_evidence_segment(record, config.get("project", {}).get("language", "de")) for record in raw_records]
    source_documents = source_documents_from_segments(segments)
    validation_findings, metadata_findings = validate_evidence_segments(segments)
    progress(f"ingest: discovered {len(source_documents)} source document(s)")
    progress(f"ingest: normalized {len(segments)} evidence segment(s); validation findings={len(validation_findings)}; metadata findings={len(metadata_findings)}")
    task_events = []
    if validation_findings:
        store = default_task_store(config)
        for finding in validation_findings:
            missing = ",".join(finding.get("missing", []))
            segment_id = str(finding.get("segment_id") or finding.get("chunk_id") or f"index-{finding.get('index', 0)}")
            result = store.raise_signal(
                task_type="audit_finding",
                severity="high",
                role="curator",
                summary=f"Evidence segment {segment_id} is missing required field(s): {missing}",
                evidence=[rel(import_dir)],
                dedupe_key=f"ragprep:missing-required:{segment_id}:{missing}",
                created_by="ingest",
            )
            task_events.append({"trigger": "ragprep_validation", **result})
    if metadata_findings:
        store = default_task_store(config)
        for finding in metadata_findings:
            segment_id = str(finding.get("segment_id") or finding.get("chunk_id") or f"index-{finding.get('index', 0)}")
            result = store.raise_signal(
                task_type="audit_finding",
                severity="medium",
                role="verifier",
                summary=f"Evidence segment {segment_id} has no source locator.",
                evidence=[rel(import_dir)],
                dedupe_key=f"ragprep:missing-source-locator:{segment_id}",
                created_by="ingest",
            )
            task_events.append({"trigger": "source_locator_metadata", **result})
    status = "fail" if validation_findings else "ready"
    payload = {
        "status": status,
        "mode": "auto-apply" if args.apply else "dry-run",
        "source": rel(import_dir),
        "documents_total": len(source_documents),
        "segments_total": len(segments),
        "source_documents_written": 0,
        "evidence_segments_written": 0,
        "metadata_findings": metadata_findings,
        "validation_findings": validation_findings,
        "vector_store": config.get("vector_store", {}),
        "tasks": task_events,
        "report_path": "",
        "written": [],
    }
    if args.apply and not validation_findings:
        corpus_state_dir = corpus_dir(config)
        ensure_dir(corpus_state_dir)
        source_documents_path = corpus_state_dir / "source_documents.json"
        evidence_segments_path = corpus_state_dir / "evidence_segments.json"
        import_manifest_path = corpus_state_dir / "import_manifest.json"
        generated_at = now_iso()
        progress(f"ingest: writing source document registry to {rel(source_documents_path)}")
        write_json(source_documents_path, {"schema_version": "wissenswerk.source-documents.v1", "generated_at": generated_at, "source_documents": source_documents})
        progress(f"ingest: writing evidence segments to {rel(evidence_segments_path)}")
        write_json(evidence_segments_path, {"schema_version": "wissenswerk.evidence-segments.v1", "generated_at": generated_at, "evidence_segments": segments})
        write_json(
            import_manifest_path,
            {
                "schema_version": "wissenswerk.import-manifest.v1",
                "generated_at": generated_at,
                "source": rel(import_dir),
                "source_documents_path": rel(source_documents_path),
                "evidence_segments_path": rel(evidence_segments_path),
                "documents_total": len(source_documents),
                "segments_total": len(segments),
                "metadata_findings": metadata_findings,
            },
        )
        payload["source_documents_written"] = len(source_documents)
        payload["evidence_segments_written"] = len(segments)
        payload["written"].extend([rel(source_documents_path), rel(evidence_segments_path), rel(import_manifest_path)])
    report_path = write_report(config, "ragprep_ingest", payload)
    progress(f"ingest: wrote report {rel(report_path)}")
    payload["report_path"] = rel(report_path)
    json_print(payload) if args.json else print(f"{status}: {len(source_documents)} source documents, {len(segments)} evidence segments; report {rel(report_path)}")
    return 1 if validation_findings else 0


def latest_import_state(config: dict[str, Any]) -> tuple[Path | None, list[dict[str, Any]]]:
    import_manifest = corpus_dir(config) / "import_manifest.json"
    evidence_segments = corpus_dir(config) / "evidence_segments.json"
    if import_manifest.exists() and evidence_segments.exists():
        payload = json.loads(evidence_segments.read_text(encoding="utf-8"))
        segments = payload.get("evidence_segments") or payload.get("segments") or payload.get("chunks") or []
        return import_manifest, segments if isinstance(segments, list) else []
    state_dir = repo_path(config.get("paths", {}).get("ragprep_imports", ".wissenswerk/ragprep_imports"))
    if not state_dir.exists():
        return None, []
    imports = sorted(state_dir.glob("ragprep_import_*.json"))
    if not imports:
        return None, []
    latest = imports[-1]
    payload = json.loads(latest.read_text(encoding="utf-8"))
    segments = payload.get("evidence_segments") or payload.get("segments") or payload.get("chunks") or []
    return latest, segments if isinstance(segments, list) else []


def analysis_dir(config: dict[str, Any]) -> Path:
    return repo_path(config.get("paths", {}).get("analysis", ".wissenswerk/analysis"))


def article_plan_dir(config: dict[str, Any]) -> Path:
    return repo_path(config.get("paths", {}).get("article_plans", ".wissenswerk/article_plans"))


def latest_analysis(config: dict[str, Any]) -> dict[str, Any]:
    path = analysis_dir(config) / "analysis.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def latest_article_plan(config: dict[str, Any]) -> dict[str, Any]:
    path = article_plan_dir(config) / "article_plan.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def listify(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def extract_terms(text: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\b[\wÀ-ÖØ-öø-ÿ][\wÀ-ÖØ-öø-ÿ'’-]{2,}\b", text, re.UNICODE):
        value = match.group(0).strip("'’-")
        if not value or value.lower() in {"the", "and", "for", "with", "from", "und", "der", "die", "das", "eine", "para", "con", "del", "les", "els"}:
            continue
        if value[0].isupper() or len(value) > 8:
            key = value.casefold()
            if key not in seen:
                terms.append(value)
                seen.add(key)
        if len(terms) >= 12:
            break
    return terms


def segment_entities(segment: dict[str, Any]) -> list[str]:
    entities = listify(segment.get("entities"))
    if entities:
        return entities
    keywords = listify(segment.get("keywords"))
    if keywords:
        return keywords[:12]
    return extract_terms(str(segment.get("title", "")) + " " + str(segment.get("text", "")))


def compact_claim_text(value: str, *, max_chars: int = 180) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rsplit(" ", 1)[0].rstrip(".,;:") + "..."


def public_source_ref(segment: dict[str, Any]) -> dict[str, str]:
    source_path = str(segment.get("source_path") or "")
    return {
        "source_document_id": source_document_id(segment),
        "document_id": str(segment.get("document_id") or source_document_id(segment)),
        "evidence_segment_id": evidence_segment_id(segment),
        "segment_id": evidence_segment_id(segment),
        "title": str(segment.get("title") or Path(source_path).stem or segment.get("document_id") or ""),
        "author": str(segment.get("author") or ""),
        "year": str(segment.get("year") or ""),
        "page": str(segment.get("page") or ""),
        "archive_id": str(segment.get("archive_id") or ""),
        "source_url": str(segment.get("source_url") or ""),
        "source_label": Path(source_path).name if source_path else str(segment.get("document_id") or ""),
        "language": str(segment.get("language") or ""),
        "rights": str(segment.get("rights") or ""),
    }


def append_claim(
    claims: list[dict[str, Any]],
    *,
    subject: str,
    predicate: str,
    object_value: str,
    document_id: str,
    segment_id: str,
    language: str,
    confidence: float,
    status: str = "candidate",
) -> dict[str, Any]:
    claim = {
        "id": f"CLAIM-{len(claims) + 1:06d}",
        "subject": subject,
        "predicate": predicate,
        "object": object_value,
        "source_document_id": document_id,
        "document_id": document_id,
        "evidence_segment_id": segment_id,
        "segment_id": segment_id,
        "language": language,
        "confidence": confidence,
        "quote_policy": "short-or-none",
        "status": status,
    }
    claims.append(claim)
    return claim


def build_corpus_analysis(config: dict[str, Any], segments: list[dict[str, Any]], state_path: Path | None) -> dict[str, Any]:
    documents: dict[str, dict[str, Any]] = {}
    entities: dict[str, dict[str, Any]] = {}
    claims: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    language_counts: dict[str, int] = {}
    optional_missing: dict[str, int] = {}
    required_findings, metadata_findings = validate_evidence_segments(segments)
    optional_fields = config.get("ragprep", {}).get("optional_fields", [])
    for segment_index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            continue
        document_id = source_document_id(segment) or "unknown"
        segment_id = evidence_segment_id(segment)
        title = str(segment.get("title") or document_id)
        language = str(segment.get("language") or config.get("project", {}).get("language", ""))
        language_counts[language] = language_counts.get(language, 0) + 1
        doc = documents.setdefault(
            document_id,
            {
                "document_id": document_id,
                "source_document_id": document_id,
                "title": title,
                "language": language,
                "segments": 0,
                "source": public_source_ref(segment),
                "sections": set(),
            },
        )
        doc["segments"] += 1
        if segment.get("section"):
            doc["sections"].add(str(segment["section"]))
        for field in optional_fields:
            if not segment.get(field):
                optional_missing[field] = optional_missing.get(field, 0) + 1
        names = segment_entities(segment)
        for name in names:
            key = name.casefold()
            entity = entities.setdefault(
                key,
                {
                    "id": f"ENT-{len(entities) + 1:05d}",
                    "name": name,
                    "mentions": 0,
                    "documents": set(),
                    "segments": set(),
                    "languages": set(),
                },
            )
            entity["mentions"] += 1
            entity["documents"].add(document_id)
            entity["segments"].add(segment_id)
            if language:
                entity["languages"].add(language)
            append_claim(
                claims,
                subject=name,
                predicate="mentioned_in",
                object_value=title,
                document_id=document_id,
                segment_id=segment_id,
                language=language,
                confidence=0.72 if segment.get("entities") else 0.55,
            )
            edges.append({"source": name, "target": title, "type": "mentioned_in"})
        summary = compact_claim_text(str(segment.get("summary") or ""))
        if summary:
            append_claim(
                claims,
                subject=title,
                predicate="summarizes",
                object_value=summary,
                document_id=document_id,
                segment_id=segment_id,
                language=language,
                confidence=0.78,
            )
            edges.append({"source": title, "target": segment_id, "type": "summarizes"})
        section = str(segment.get("section") or "").strip()
        if section:
            append_claim(
                claims,
                subject=title,
                predicate="has_section",
                object_value=section,
                document_id=document_id,
                segment_id=segment_id,
                language=language,
                confidence=0.8,
            )
            edges.append({"source": title, "target": section, "type": "has_section"})
        for keyword in listify(segment.get("keywords"))[:6]:
            append_claim(
                claims,
                subject=title,
                predicate="has_keyword",
                object_value=keyword,
                document_id=document_id,
                segment_id=segment_id,
                language=language,
                confidence=0.7,
            )
            edges.append({"source": title, "target": keyword, "type": "has_keyword"})
        years = sorted(set(re.findall(r"\b(1[0-9]{3}|20[0-9]{2})\b", str(segment.get("text", "")))))
        for year in years[:8]:
            append_claim(
                claims,
                subject=title,
                predicate="mentions_year",
                object_value=year,
                document_id=document_id,
                segment_id=segment_id,
                language=language,
                confidence=0.65,
            )
            edges.append({"source": title, "target": year, "type": "related_to"})
    normalized_docs = []
    for doc in documents.values():
        doc["sections"] = sorted(doc["sections"])
        normalized_docs.append(doc)
    normalized_entities = []
    for entity in entities.values():
        entity["documents"] = sorted(entity["documents"])
        entity["segments"] = sorted(item for item in entity["segments"] if item)
        entity["languages"] = sorted(entity["languages"])
        normalized_entities.append(entity)
    conflicts = []
    year_claims: dict[str, set[str]] = {}
    for claim in claims:
        if claim["predicate"] == "mentions_year":
            year_claims.setdefault(claim["subject"], set()).add(claim["object"])
    for subject, years in sorted(year_claims.items()):
        if len(years) > 1:
            conflicts.append(
                {
                    "type": "date_context_review",
                    "subject": subject,
                    "values": sorted(years),
                    "severity": "low",
                    "note": "Multiple years mentioned for the same source/topic; verifier should decide whether this is chronology or conflict.",
                }
            )
    return {
        "schema_version": "wissenswerk.analysis.v1",
        "generated_at": now_iso(),
        "source_import": rel(state_path) if state_path else "",
        "project": config.get("project", {}),
        "corpus_inventory": {
            "segments_total": len(segments),
            "documents_total": len(normalized_docs),
            "languages": language_counts,
            "documents": sorted(normalized_docs, key=lambda item: item["title"].lower()),
        },
        "entities": sorted(normalized_entities, key=lambda item: (-item["mentions"], item["name"].lower())),
        "claims": claims,
        "concept_graph": {
            "nodes": [{"id": entity["name"], "type": "entity"} for entity in normalized_entities]
            + [{"id": doc["title"], "type": "source_document"} for doc in normalized_docs]
            + [{"id": claim["id"], "type": "claim"} for claim in claims],
            "edges": edges
            + [{"source": claim["id"], "target": claim.get("evidence_segment_id", ""), "type": "supported_by"} for claim in claims if claim.get("evidence_segment_id")],
        },
        "source_coverage": {
            "documents_with_source_ref": sum(1 for doc in normalized_docs if doc["source"].get("source_label") or doc["source"].get("source_url") or doc["source"].get("archive_id")),
            "segments_with_hash": sum(1 for segment in segments if isinstance(segment, dict) and segment.get("hash")),
            "metadata_findings": metadata_findings,
            "optional_missing": optional_missing,
        },
        "conflict_candidates": conflicts,
        "validation_findings": required_findings,
    }


def command_analyze(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    state_path, segments = latest_import_state(config)
    progress(f"analyze: reading import state {rel(state_path) if state_path else '[missing]'}")
    analysis = build_corpus_analysis(config, segments, state_path)
    progress(
        "analyze: built inventory "
        f"documents={analysis['corpus_inventory']['documents_total']} "
        f"segments={analysis['corpus_inventory'].get('segments_total', 0)} "
        f"entities={len(analysis['entities'])} claims={len(analysis['claims'])}"
    )
    task_events = []
    for finding in analysis["validation_findings"]:
        missing = ",".join(finding.get("missing", []))
        segment_id = str(finding.get("segment_id") or finding.get("chunk_id") or f"index-{finding.get('index', 0)}")
        result = default_task_store(config).raise_signal(
            task_type="audit_finding",
            severity="high",
            role="curator",
            summary=f"Analysis found evidence segment {segment_id} missing required field(s): {missing}",
            evidence=[analysis.get("source_import", "")],
            dedupe_key=f"analyze:missing-required:{segment_id}:{missing}",
            created_by="analyze",
        )
        task_events.append({"trigger": "analysis_validation", **result})
    for conflict in analysis["conflict_candidates"]:
        result = default_task_store(config).raise_signal(
            task_type="audit_finding",
            severity="low",
            role="verifier",
            summary=f"Potential {conflict['type']} for {conflict['subject']}",
            evidence=[analysis.get("source_import", "")],
            dedupe_key=f"analyze:conflict:{conflict['type']}:{conflict['subject']}",
            created_by="analyze",
        )
        task_events.append({"trigger": "conflict_candidate", **result})
    payload = {
        "status": "ready" if segments else "empty",
        "mode": "auto-apply" if args.apply else "dry-run",
        "analysis": analysis,
        "tasks": task_events,
        "written": [],
        "report_path": "",
        "next_commands": ["./wissenswerk.py plan articles --apply --json", "./wissenswerk.py build --apply --json"],
    }
    if args.apply:
        out_dir = analysis_dir(config)
        progress(f"analyze: writing analysis artifacts to {rel(out_dir)}")
        write_json(out_dir / "analysis.json", analysis)
        write_json(out_dir / "corpus_inventory.json", analysis["corpus_inventory"])
        write_json(out_dir / "entities.json", {"entities": analysis["entities"]})
        write_json(out_dir / "claims.json", {"claims": analysis["claims"]})
        write_json(out_dir / "graph.json", analysis["concept_graph"])
        write_json(out_dir / "conflicts.json", {"conflict_candidates": analysis["conflict_candidates"]})
        payload["written"].extend(
            rel(out_dir / name)
            for name in ["analysis.json", "corpus_inventory.json", "entities.json", "claims.json", "graph.json", "conflicts.json"]
        )
    report_path = write_report(config, "analysis", payload)
    progress(f"analyze: wrote report {rel(report_path)}")
    payload["report_path"] = rel(report_path)
    json_print(payload) if args.json else print(f"Wissenswerk analyze: {payload['status']}")
    return 1 if analysis["validation_findings"] else 0


def candidate_priority(entity: dict[str, Any], project_type: str) -> str:
    mentions = int(entity.get("mentions", 0))
    docs = len(entity.get("documents", []))
    name = str(entity.get("name", ""))
    if docs >= 2 or mentions >= 4:
        return "A"
    if project_type == "local-history" and (name.istitle() or len(name) > 8):
        return "B"
    if mentions >= 2:
        return "B"
    return "C"


def article_subtype_for_title(title: str, candidate_type: str) -> str:
    normalized = title.casefold()
    if candidate_type == "source":
        return "source"
    if "timeline" in normalized:
        return "timeline"
    if normalized == "sources overview":
        return "sources_overview"
    if "glossary" in normalized:
        return "glossary"
    if "places" in normalized or "buildings" in normalized:
        return "places_buildings"
    if "institutions" in normalized:
        return "institutions"
    if "people" in normalized or "families" in normalized:
        return "people_families"
    if candidate_type == "topic":
        return "topic"
    if candidate_type == "concept":
        return "concept"
    if candidate_type == "navigation":
        return "navigation"
    return "overview"


def build_article_plan(config: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    project = config.get("project", {})
    project_name = str(project.get("name") or "Project")
    project_type = str(project.get("type") or "general")
    entities = analysis.get("entities", [])
    docs = analysis.get("corpus_inventory", {}).get("documents", [])
    claims = analysis.get("claims", [])
    candidates: list[dict[str, Any]] = []
    mandatory = [project_name, f"History of {project_name}", f"Timeline of {project_name}", "Sources Overview"]
    if project_type == "local-history":
        mandatory.extend(["Places and Buildings", "Institutions", "People and Families", "Glossary of Historical Terms"])
    seen_titles: set[str] = set()
    for index, title in enumerate(mandatory, start=1):
        if title.casefold() in seen_titles:
            continue
        seen_titles.add(title.casefold())
        candidate_type = "overview" if index <= 4 else "navigation"
        candidates.append(
            {
                "id": f"ARTICLE-{len(candidates) + 1:04d}",
                "title": title,
                "priority": "A" if index <= 4 else "B",
                "type": candidate_type,
                "article_subtype": article_subtype_for_title(title, candidate_type),
                "source_entities": [],
                "source_documents": [doc.get("document_id", "") for doc in docs[:5]],
                "recommended_sections": ["Overview", "Evidence-backed claims", "Historical context", "Open questions", "Sources", "Related pages"],
                "planning_basis": ["mandatory_profile_page"],
                "render_profile": {"mode": "deterministic", "synthesis_slot": "provider_optional"},
                "status": "planned",
            }
        )
    section_documents: dict[str, set[str]] = {}
    for claim in claims:
        if claim.get("predicate") != "has_section":
            continue
        section = str(claim.get("object") or "").strip()
        if not section or section.casefold() in GENERIC_SECTION_TITLES:
            continue
        section_documents.setdefault(section, set()).add(str(claim.get("source_document_id") or ""))
    for section, document_ids in sorted(section_documents.items(), key=lambda item: (-len(item[1]), item[0].lower()))[:12]:
        title = section if section.lower().startswith(("source:", "section:")) else f"{section}"
        if title.casefold() in seen_titles:
            continue
        seen_titles.add(title.casefold())
        priority = "B" if len(document_ids) > 1 or len(docs) <= 3 else "C"
        candidates.append(
            {
                "id": f"ARTICLE-{len(candidates) + 1:04d}",
                "title": title,
                "priority": priority,
                "type": "topic",
                "article_subtype": "topic",
                "source_entities": [],
                "source_documents": sorted(item for item in document_ids if item),
                "recommended_sections": ["Overview", "Evidence-backed claims", "Sources", "Related pages"],
                "planning_basis": ["section_coverage"],
                "render_profile": {"mode": "deterministic", "synthesis_slot": "provider_optional"},
                "status": "planned" if priority in {"A", "B"} else "stub",
            }
        )
    for entity in entities[:50]:
        title = str(entity.get("name", "")).strip()
        if not title or title.casefold() in seen_titles:
            continue
        seen_titles.add(title.casefold())
        priority = candidate_priority(entity, project_type)
        candidates.append(
            {
                "id": f"ARTICLE-{len(candidates) + 1:04d}",
                "title": title,
                "priority": priority,
                "type": "concept" if priority in {"A", "B"} else "stub",
                "article_subtype": "concept" if priority in {"A", "B"} else "stub",
                "source_entities": [entity.get("id", "")],
                "source_documents": entity.get("documents", []),
                "recommended_sections": ["Overview", "Evidence-backed claims", "Sources", "Related pages"],
                "planning_basis": ["entity_mentions"],
                "render_profile": {"mode": "deterministic", "synthesis_slot": "provider_optional"},
                "status": "planned" if priority in {"A", "B"} else "stub",
            }
        )
    source_candidates = []
    for doc in docs:
        source_priority = "B" if len(docs) <= 3 else "D"
        source_candidates.append(
            {
                "id": f"ARTICLE-{len(candidates) + len(source_candidates) + 1:04d}",
                "title": f"Source: {doc.get('title', doc.get('document_id', 'Document'))}",
                "priority": source_priority,
                "type": "source",
                "article_subtype": "source",
                "source_entities": [],
                "source_documents": [doc.get("document_id", "")],
                "recommended_sections": ["Source metadata", "Coverage", "Use in wiki"],
                "planning_basis": ["source_document"],
                "render_profile": {"mode": "deterministic", "synthesis_slot": "provider_optional"},
                "status": "planned" if source_priority == "B" else "source-note",
            }
        )
    candidates.extend(source_candidates)
    return {
        "schema_version": "wissenswerk.article-plan.v1",
        "generated_at": now_iso(),
        "project": project,
        "article_candidates": candidates,
        "summary": {
            "total": len(candidates),
            "by_priority": {priority: sum(1 for item in candidates if item["priority"] == priority) for priority in ["A", "B", "C", "D", "E"]},
        },
    }


def command_plan_articles(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    analysis = latest_analysis(config)
    if not analysis:
        progress("plan articles: no saved analysis found; building analysis in memory")
        state_path, segments = latest_import_state(config)
        analysis = build_corpus_analysis(config, segments, state_path)
    else:
        progress("plan articles: using saved analysis artifacts")
    plan = build_article_plan(config, analysis)
    progress(f"plan articles: planned {len(plan['article_candidates'])} article candidate(s)")
    payload = {
        "status": "ready" if plan["article_candidates"] else "empty",
        "mode": "auto-apply" if args.apply else "dry-run",
        "article_plan": plan,
        "written": [],
        "report_path": "",
        "next_commands": ["./wissenswerk.py build --apply --json", "./wissenswerk.py audit --json"],
    }
    if args.apply:
        out_dir = article_plan_dir(config)
        progress(f"plan articles: writing article plan to {rel(out_dir / 'article_plan.json')}")
        write_json(out_dir / "article_plan.json", plan)
        payload["written"].append(rel(out_dir / "article_plan.json"))
    report_path = write_report(config, "article_plan", payload)
    progress(f"plan articles: wrote report {rel(report_path)}")
    payload["report_path"] = rel(report_path)
    json_print(payload) if args.json else print(f"Wissenswerk plan articles: {payload['status']}")
    return 0


def command_curate(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    state_path, segments = latest_import_state(config)
    documents: dict[str, dict[str, Any]] = {}
    duplicate_segment_ids: set[str] = set()
    seen_segment_ids: set[str] = set()
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        segment_id = evidence_segment_id(segment)
        if segment_id in seen_segment_ids:
            duplicate_segment_ids.add(segment_id)
        seen_segment_ids.add(segment_id)
        document_id = source_document_id(segment) or str(segment.get("source_path") or "unknown")
        doc = documents.setdefault(
            document_id,
            {
                "document_id": document_id,
                "source_document_id": document_id,
                "title": str(segment.get("title") or Path(str(segment.get("source_path", ""))).stem or document_id),
                "source_path": str(segment.get("source_path", "")),
                "language": str(segment.get("language") or config.get("project", {}).get("language", "")),
                "segments": 0,
                "sections": set(),
                "summaries": 0,
            },
        )
        doc["segments"] += 1
        if segment.get("section"):
            doc["sections"].add(str(segment["section"]))
        if segment.get("summary"):
            doc["summaries"] += 1

    article_candidates = []
    for doc in sorted(documents.values(), key=lambda item: item["title"].lower()):
        sections = sorted(doc.pop("sections"))
        article_candidates.append(
            {
                **doc,
                "sections": sections,
                "readiness": "ready" if doc["segments"] and doc["source_path"] else "needs_source",
                "recommended_action": "build" if doc["segments"] else "inspect source",
            }
        )
    task_events = []
    if duplicate_segment_ids:
        store = default_task_store(config)
        for segment_id in sorted(duplicate_segment_ids):
            result = store.raise_signal(
                task_type="anomaly",
                severity="medium",
                role="curator",
                summary=f"Duplicate evidence segment id detected during curation: {segment_id}",
                evidence=[rel(state_path) if state_path else ""],
                dedupe_key=f"curate:duplicate-segment:{segment_id}",
                created_by="curate",
            )
            task_events.append({"trigger": "duplicate_segment_id", **result})
    payload = {
        "status": "ready" if segments else "empty",
        "source_import": rel(state_path) if state_path else "",
        "workflow": "curate",
        "segments_total": len(segments),
        "documents_total": len(article_candidates),
        "article_candidates": article_candidates,
        "conflicts": {
            "duplicate_segment_ids": sorted(duplicate_segment_ids),
            "missing_summaries": sum(1 for segment in segments if isinstance(segment, dict) and not segment.get("summary")),
        },
        "tasks": task_events,
        "next_commands": [
            "./wissenswerk.py analyze --apply --json",
            "./wissenswerk.py plan articles --apply --json",
            "./wissenswerk.py build --apply --json",
            "./wissenswerk.py search \"<query>\" --source all --json",
        ],
        "report_path": "",
    }
    report_path = write_report(config, "curation", payload)
    payload["report_path"] = rel(report_path)
    json_print(payload) if args.json else print_curate(payload)
    return 0


def print_curate(payload: dict[str, Any]) -> None:
    print(f"Wissenswerk curate: {payload['status']} ({payload['documents_total']} article candidates)")
    for candidate in payload["article_candidates"][:10]:
        print(f"- {candidate['title']} segments={candidate.get('segments', 0)} readiness={candidate['readiness']}")


def slugify_title(value: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip()).strip("_")
    return slug or fallback


def claim_segment_id(claim: dict[str, Any]) -> str:
    return str(claim.get("evidence_segment_id") or claim.get("segment_id") or claim.get("chunk_id") or "")


def claims_for_candidate(candidate: dict[str, Any], analysis: dict[str, Any]) -> list[dict[str, Any]]:
    title_key = str(candidate.get("title", "")).casefold()
    subtype = str(candidate.get("article_subtype") or article_subtype_for_title(str(candidate.get("title", "")), str(candidate.get("type", ""))))
    documents = {str(item) for item in candidate.get("source_documents", []) if str(item)}
    claims = analysis.get("claims", [])
    if subtype == "timeline":
        matched = [claim for claim in claims if claim.get("predicate") == "mentions_year" and (not documents or str(claim.get("source_document_id", "")) in documents)]
        return matched[:24] if matched else [claim for claim in claims if claim.get("predicate") == "mentions_year"][:24]
    if subtype == "source":
        return [claim for claim in claims if str(claim.get("source_document_id", "")) in documents][:24]
    if subtype == "concept":
        exact = [claim for claim in claims if str(claim.get("subject", "")).casefold() == title_key]
        if exact:
            return exact[:16]
    if subtype == "topic":
        section = [
            claim
            for claim in claims
            if claim.get("predicate") == "has_section"
            and str(claim.get("object", "")).casefold() == title_key
        ]
        if section:
            return section[:16]
    matched = [
        claim
        for claim in claims
        if str(claim.get("subject", "")).casefold() == title_key
        or str(claim.get("source_document_id", "")) in documents
    ]
    if matched:
        return matched[:12]
    if candidate.get("priority") == "A":
        return claims[:12]
    return claims[:5]


def source_refs_for_claims(claims: list[dict[str, Any]], segments: list[dict[str, Any]]) -> list[dict[str, str]]:
    by_segment = {evidence_segment_id(segment): segment for segment in segments if isinstance(segment, dict)}
    legacy_by_chunk = {str(segment.get("chunk_id", "")): segment for segment in segments if isinstance(segment, dict)}
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for claim in claims:
        segment_key = str(claim.get("evidence_segment_id") or claim.get("segment_id") or "")
        segment = by_segment.get(segment_key) or legacy_by_chunk.get(str(claim.get("chunk_id", "")))
        if not segment:
            continue
        ref = public_source_ref(segment)
        key = ref.get("segment_id") or ref.get("chunk_id") or ref.get("document_id")
        if key and key not in seen:
            refs.append(ref)
            seen.add(key)
    return refs


def source_refs_for_documents(document_ids: set[str], segments: list[dict[str, Any]]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for segment in segments:
        if not isinstance(segment, dict) or source_document_id(segment) not in document_ids:
            continue
        ref = public_source_ref(segment)
        key = ref.get("segment_id") or ref.get("document_id")
        if key and key not in seen:
            refs.append(ref)
            seen.add(key)
    return refs


def format_source_ref(ref: dict[str, str]) -> str:
    label = ref.get("title") or ref.get("source_label") or ref.get("document_id") or "Source"
    extras = []
    if ref.get("author"):
        extras.append(ref["author"])
    if ref.get("year"):
        extras.append(ref["year"])
    if ref.get("page"):
        extras.append(f"p. {ref['page']}")
    if ref.get("archive_id"):
        extras.append(f"archive `{ref['archive_id']}`")
    if ref.get("source_url"):
        extras.append(ref["source_url"])
    if ref.get("segment_id"):
        extras.append(f"segment `{ref['segment_id']}`")
    suffix = f" ({'; '.join(extras)})" if extras else ""
    return f"- {label}{suffix}"


def format_claim_bullet(claim: dict[str, Any]) -> str:
    predicate = str(claim.get("predicate", "related_to")).replace("_", " ")
    subject = claim.get("subject", "[unresolved]")
    obj = claim.get("object", "[unresolved]")
    segment = claim_segment_id(claim)
    citation = f" [`{segment}`]" if segment else ""
    return f"- {subject} {predicate} {obj}.{citation}"


def source_documents_by_id(analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    docs = analysis.get("corpus_inventory", {}).get("documents", [])
    return {str(doc.get("document_id") or doc.get("source_document_id") or ""): doc for doc in docs if isinstance(doc, dict)}


def segments_for_documents(segments: list[dict[str, Any]], document_ids: set[str]) -> list[dict[str, Any]]:
    return [segment for segment in segments if isinstance(segment, dict) and source_document_id(segment) in document_ids]


def source_ref_for_document(doc: dict[str, Any], segments: list[dict[str, Any]]) -> dict[str, str]:
    doc_id = str(doc.get("document_id") or doc.get("source_document_id") or "")
    for segment in segments:
        if isinstance(segment, dict) and source_document_id(segment) == doc_id:
            return public_source_ref(segment)
    source = doc.get("source", {})
    return source if isinstance(source, dict) else {}


def related_pages_block(ctx: dict[str, Any]) -> list[str]:
    title = ctx["title"]
    related = [item for item in ctx.get("related_titles", []) if item and item != title]
    if not related:
        return []
    return ["## Related pages", "", *[f"- [[{item}]]" for item in related[:8]], ""]


def sources_block(refs: list[dict[str, str]]) -> list[str]:
    if not refs:
        return []
    return ["## Sources", "", *[format_source_ref(ref) for ref in refs], ""]


def claim_block(claims: list[dict[str, Any]], *, heading: str = "Evidence-backed claims", limit: int = 8) -> list[str]:
    if not claims:
        return []
    return [f"## {heading}", "", *[format_claim_bullet(claim) for claim in claims[:limit]], ""]


def render_frontmatter(ctx: dict[str, Any]) -> list[str]:
    candidate = ctx["candidate"]
    return [
        "---",
        f"uuid: {uuid.uuid4()}",
        f"title: {ctx['title']}",
        f"priority: {candidate.get('priority', '')}",
        f"article_type: {candidate.get('type', '')}",
        f"article_subtype: {ctx['subtype']}",
        "epistemic: \"#derived\"",
        f"updated_at: {now_iso()}",
        "---",
        "",
        f"# {ctx['title']}",
        "",
    ]


def render_overview_article(ctx: dict[str, Any]) -> list[str]:
    analysis = ctx["analysis"]
    inventory = analysis.get("corpus_inventory", {})
    coverage = analysis.get("source_coverage", {})
    entities = analysis.get("entities", [])[:8]
    lines = render_frontmatter(ctx)
    lines.extend(
        [
            "## Overview",
            "",
            f"This generated overview is compiled from {inventory.get('documents_total', 0)} source document(s) and {inventory.get('segments_total', 0)} evidence segment(s). It is a deterministic draft for review before publication-sensitive use.",
            "",
            "## Corpus coverage",
            "",
            f"- Source documents: {inventory.get('documents_total', 0)}",
            f"- Evidence segments: {inventory.get('segments_total', 0)}",
            f"- Documents with source references: {coverage.get('documents_with_source_ref', 0)}",
            f"- Segments with hashes: {coverage.get('segments_with_hash', 0)}",
            "",
        ]
    )
    if entities:
        lines.extend(["## Top concepts", "", *[f"- {entity.get('name', '')} ({entity.get('mentions', 0)} mention(s))" for entity in entities], ""])
    lines.extend(claim_block(ctx["claims"], limit=8))
    lines.extend(sources_block(ctx["refs"]))
    lines.extend(related_pages_block(ctx))
    return lines


def render_timeline_article(ctx: dict[str, Any]) -> list[str]:
    lines = render_frontmatter(ctx)
    timeline_claims = [claim for claim in ctx["claims"] if claim.get("predicate") == "mentions_year"]
    by_year: dict[str, list[dict[str, Any]]] = {}
    for claim in timeline_claims:
        by_year.setdefault(str(claim.get("object") or "[undated]"), []).append(claim)
    lines.extend(["## Timeline", ""])
    if by_year:
        for year in sorted(by_year):
            lines.extend([f"### {year}", ""])
            lines.extend(format_claim_bullet(claim) for claim in by_year[year])
            lines.append("")
    else:
        lines.extend(["- No explicit year claims were extracted for this page.", ""])
    lines.extend(["## Chronology notes", "", "- Review whether repeated or conflicting years represent a sequence, a date range, or a source conflict.", ""])
    lines.extend(sources_block(ctx["refs"]))
    lines.extend(related_pages_block(ctx))
    return lines


def render_source_article(ctx: dict[str, Any]) -> list[str]:
    candidate = ctx["candidate"]
    document_ids = {str(item) for item in candidate.get("source_documents", []) if str(item)}
    docs = source_documents_by_id(ctx["analysis"])
    segments = segments_for_documents(ctx["segments"], document_ids)
    doc = docs.get(next(iter(document_ids), ""), {})
    ref = source_ref_for_document(doc, ctx["segments"]) if doc else (ctx["refs"][0] if ctx["refs"] else {})
    lines = render_frontmatter(ctx)
    lines.extend(["## Source metadata", ""])
    metadata = [
        ("Document ID", ref.get("document_id") or next(iter(document_ids), "")),
        ("Title", ref.get("title") or doc.get("title", "")),
        ("Author", ref.get("author", "")),
        ("Year", ref.get("year", "")),
        ("Archive ID", ref.get("archive_id", "")),
        ("Source URL", ref.get("source_url", "")),
        ("Rights", ref.get("rights", "")),
    ]
    lines.extend(f"- {label}: {value or '[unknown]'}" for label, value in metadata)
    lines.extend(["", "## Coverage", "", f"- Evidence segments: {len(segments)}"])
    sections = sorted({str(segment.get("section")) for segment in segments if segment.get("section")})
    if sections:
        lines.extend(f"- Section: {section}" for section in sections)
    lines.append("")
    lines.extend(claim_block(ctx["claims"], heading="Claims supported by this source", limit=12))
    lines.extend(["## Provenance notes", "", "- This page publishes source metadata and evidence references, not private full-text source material.", ""])
    lines.extend(sources_block(ctx["refs"]))
    lines.extend(related_pages_block(ctx))
    return lines


def render_topic_article(ctx: dict[str, Any]) -> list[str]:
    document_ids = {str(item) for item in ctx["candidate"].get("source_documents", []) if str(item)}
    lines = render_frontmatter(ctx)
    lines.extend(["## Topic coverage", "", f"- Supporting source documents: {len(document_ids)}", f"- Supporting evidence references: {len(ctx['refs'])}", ""])
    lines.extend(claim_block(ctx["claims"], limit=10))
    lines.extend(["## Open questions", "", "- Review whether this topic should remain a standalone page or merge into a broader article.", ""])
    lines.extend(sources_block(ctx["refs"]))
    lines.extend(related_pages_block(ctx))
    return lines


def render_concept_article(ctx: dict[str, Any]) -> list[str]:
    lines = render_frontmatter(ctx)
    mentioned = [claim for claim in ctx["claims"] if claim.get("predicate") == "mentioned_in"]
    document_count = len({claim.get("source_document_id") for claim in ctx["claims"] if claim.get("source_document_id")})
    lines.extend(["## Concept summary", "", f"This concept appears in {document_count} source document(s).", ""])
    lines.extend(claim_block(mentioned or ctx["claims"], limit=10))
    lines.extend(["## Related source documents", ""])
    source_titles = sorted({str(claim.get("object")) for claim in mentioned if claim.get("object")})
    if source_titles:
        lines.extend(f"- {title}" for title in source_titles[:12])
    else:
        lines.append("- [unresolved]")
    lines.append("")
    lines.extend(sources_block(ctx["refs"]))
    lines.extend(related_pages_block(ctx))
    return lines


def render_navigation_article(ctx: dict[str, Any]) -> list[str]:
    subtype = ctx["subtype"]
    heading = {
        "places_buildings": "Places and buildings",
        "institutions": "Institutions",
        "people_families": "People and families",
        "glossary": "Glossary",
    }.get(subtype, "Navigation")
    lines = render_frontmatter(ctx)
    lines.extend([f"## {heading}", "", "This generated navigation page groups source-backed entries for review.", ""])
    entities = ctx["analysis"].get("entities", [])[:20]
    if subtype == "glossary":
        lines.extend(["## Terms", ""])
        lines.extend(f"- {entity.get('name', '')}: mentioned in {len(entity.get('documents', []))} source document(s)." for entity in entities)
        lines.append("")
    else:
        lines.extend(["## Candidate entries", ""])
        entries = [title for title in ctx.get("related_titles", [])[:12] if title != ctx["title"]]
        lines.extend(f"- [[{title}]]" for title in entries) if entries else lines.append("- [unresolved]")
        lines.append("")
    lines.extend(claim_block(ctx["claims"], limit=6))
    lines.extend(sources_block(ctx["refs"]))
    return lines


def render_sources_overview_article(ctx: dict[str, Any]) -> list[str]:
    docs = ctx["analysis"].get("corpus_inventory", {}).get("documents", [])
    coverage = ctx["analysis"].get("source_coverage", {})
    lines = render_frontmatter(ctx)
    lines.extend(
        [
            "## Source coverage",
            "",
            f"- Source documents: {len(docs)}",
            f"- Documents with source references: {coverage.get('documents_with_source_ref', 0)}",
            f"- Segments with hashes: {coverage.get('segments_with_hash', 0)}",
            "",
            "## Source documents",
            "",
        ]
    )
    for doc in docs:
        source = doc.get("source", {}) if isinstance(doc, dict) else {}
        source_label = source.get("source_label") or source.get("source_url") or source.get("archive_id") or "[locator unresolved]"
        lines.append(f"- {doc.get('title', doc.get('document_id', 'Document'))}: {doc.get('segments', 0)} segment(s), {source_label}")
    lines.extend(["", "## Rights and provenance gaps", ""])
    optional_missing = coverage.get("optional_missing", {})
    if optional_missing:
        lines.extend(f"- Missing `{field}` on {count} segment(s)." for field, count in sorted(optional_missing.items()))
    else:
        lines.append("- No optional metadata gaps were recorded.")
    lines.append("")
    lines.extend(sources_block(ctx["refs"]))
    lines.extend(related_pages_block(ctx))
    return lines


def build_render_context(
    config: dict[str, Any],
    candidate: dict[str, Any],
    claims: list[dict[str, Any]],
    refs: list[dict[str, str]],
    related: list[str],
    analysis: dict[str, Any],
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    title = str(candidate.get("title") or "Untitled")
    subtype = str(candidate.get("article_subtype") or article_subtype_for_title(title, str(candidate.get("type", ""))))
    return {
        "config": config,
        "candidate": candidate,
        "title": title,
        "subtype": subtype,
        "claims": claims,
        "refs": refs,
        "related_titles": related,
        "analysis": analysis,
        "segments": segments,
        "render_blocks": {
            "mode": "deterministic",
            "synthesis_slot": "provider_optional",
            "provider_invoked": False,
        },
    }


def render_article(ctx: dict[str, Any]) -> str:
    subtype = ctx["subtype"]
    candidate_type = str(ctx["candidate"].get("type", ""))
    if subtype == "timeline":
        lines = render_timeline_article(ctx)
    elif subtype == "source":
        lines = render_source_article(ctx)
    elif subtype == "sources_overview":
        lines = render_sources_overview_article(ctx)
    elif subtype in {"places_buildings", "institutions", "people_families", "glossary"}:
        lines = render_navigation_article(ctx)
    elif candidate_type == "topic" or subtype == "topic":
        lines = render_topic_article(ctx)
    elif candidate_type == "concept" or subtype == "concept":
        lines = render_concept_article(ctx)
    else:
        lines = render_overview_article(ctx)
    return "\n".join(lines)


def command_build(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    wiki_root = repo_path(config.get("paths", {}).get("wiki", "docs/Wiki"))
    state_path, segments = latest_import_state(config)
    progress(f"build: loading import state {rel(state_path) if state_path else '[missing]'}")
    analysis = latest_analysis(config)
    if not analysis:
        progress("build: no saved analysis found; building analysis in memory")
        analysis = build_corpus_analysis(config, segments, state_path)
    plan = latest_article_plan(config)
    if not plan:
        progress("build: no saved article plan found; planning articles in memory")
        plan = build_article_plan(config, analysis)
    candidates = [
        candidate
        for candidate in plan.get("article_candidates", [])
        if candidate.get("priority") in {"A", "B"}
    ]
    if not candidates:
        candidates = plan.get("article_candidates", [])[:5]
    progress(f"build: selected {len(candidates)} article candidate(s)")
    task_events = []
    if not segments:
        result = default_task_store(config).raise_signal(
            task_type="anomaly",
            severity="low",
            role="curator",
            summary="Build ran without RagPrep import state.",
            evidence=[rel(wiki_root)],
            dedupe_key="build:no-import-state",
            created_by="build",
        )
        task_events.append({"trigger": "no_import_state", **result})
    payload = {
        "status": "ready" if candidates else "empty",
        "mode": "auto-apply" if args.apply else "dry-run",
        "wiki_root": rel(wiki_root),
        "source_import": rel(state_path) if state_path else "",
        "articles_planned": len(candidates),
        "written": [],
        "tasks": task_events,
        "report_path": "",
        "rollback_hint": "Remove files listed in `written` and rerun build from article_plan.json.",
    }
    if args.apply:
        ensure_dir(wiki_root / "Articles")
        titles = [str(candidate.get("title", "")) for candidate in candidates]
        for index, candidate in enumerate(candidates, start=1):
            title = str(candidate.get("title") or f"Article {index}")
            progress(f"build: writing article {index}/{len(candidates)}: {title}")
            slug = slugify_title(title, f"article_{index}")
            claims = claims_for_candidate(candidate, analysis)
            refs = source_refs_for_claims(claims, segments)
            if not refs:
                refs = source_refs_for_documents({str(item) for item in candidate.get("source_documents", []) if str(item)}, segments)
            render_context = build_render_context(config, candidate, claims, refs, titles, analysis, segments)
            article_path = wiki_root / "Articles" / f"{slug}.md"
            provenance_path = wiki_root / "Articles" / f"{slug}.provenance.json"
            article_path.write_text(render_article(render_context), encoding="utf-8")
            write_json(
                provenance_path,
                {
                    "schema_version": "wissenswerk.article-provenance.v1",
                    "generated_at": now_iso(),
                    "article": title,
                    "candidate": candidate,
                    "render_profile": render_context["render_blocks"],
                    "claims": claims,
                    "sources": refs,
                    "source_import": rel(state_path) if state_path else "",
                },
            )
            payload["written"].extend([rel(article_path), rel(provenance_path)])
        index_path = wiki_root / "index.md"
        index_path.write_text(
            "\n".join(["# Wiki Index", "", *[f"- [[{title}]]" for title in titles], ""]),
            encoding="utf-8",
        )
        payload["written"].append(rel(index_path))
    report_path = write_report(config, "wiki_build", payload)
    progress(f"build: wrote report {rel(report_path)}")
    payload["report_path"] = rel(report_path)
    json_print(payload) if args.json else print(f"Wissenswerk build: {payload['status']}")
    return 0


def command_wiki_build(args: argparse.Namespace) -> int:
    return command_build(args)


def command_legacy_wiki_build(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    wiki_root = repo_path(config.get("paths", {}).get("wiki", "docs/Wiki"))
    sources = [repo_path(path) for path in config.get("paths", {}).get("sources", [])]
    existing_sources = [path for path in sources if path.exists()]
    articles = sorted(wiki_root.rglob("*.md")) if wiki_root.exists() else []
    state_path, segments = latest_import_state(config)
    payload = {
        "status": "ready",
        "mode": "auto-apply" if args.apply else "dry-run",
        "wiki_root": rel(wiki_root),
        "source_roots": [rel(path) for path in existing_sources],
        "articles_seen": len(articles),
        "source_import": rel(state_path) if state_path else "",
        "documents_seen": len({source_document_id(segment) for segment in segments if isinstance(segment, dict)}),
        "report_path": "",
        "tasks": [],
        "written": [],
        "rollback_hint": "Revert the files listed in `written` and remove the report for this run.",
    }
    if args.apply:
        if segments:
            missing_source_segments = [
                evidence_segment_id(segment) or source_document_id(segment) or "unknown"
                for segment in segments
                if isinstance(segment, dict) and not has_source_locator(segment)
            ]
            if missing_source_segments:
                store = default_task_store(config)
                result = store.raise_signal(
                    task_type="audit_finding",
                    severity="medium",
                    role="verifier",
                    summary=f"Wiki build encountered evidence segments without source locator: {', '.join(missing_source_segments[:5])}",
                    evidence=[rel(state_path) if state_path else rel(wiki_root)],
                    dedupe_key="wiki-build:missing-source-locator:" + hashlib.sha256(",".join(sorted(missing_source_segments)).encode("utf-8")).hexdigest()[:16],
                    created_by="wiki-build",
                )
                payload["tasks"].append({"trigger": "missing_source_locator", **result})
            grouped: dict[str, list[dict[str, Any]]] = {}
            for segment in segments:
                if isinstance(segment, dict):
                    grouped.setdefault(source_document_id(segment) or str(segment.get("source_path") or "unknown"), []).append(segment)
            for index, (document_id, doc_segments) in enumerate(sorted(grouped.items()), start=1):
                title = str(doc_segments[0].get("title") or document_id)
                article_path = wiki_root / "Articles" / f"{slugify_title(title, f'article_{index}')}.md"
                ensure_dir(article_path.parent)
                citations = [
                    f"- `{evidence_segment_id(segment)}` from `{segment.get('archive_id') or segment.get('source_url') or segment.get('source_path', '')}`"
                    for segment in doc_segments
                ]
                body_sections = []
                for segment in doc_segments[:5]:
                    section = str(segment.get("section") or "Source excerpt")
                    summary = str(segment.get("summary") or segment.get("text", "")[:500]).strip()
                    body_sections.extend([f"## {section}", "", summary or "[UNRESOLVED]", ""])
                article_path.write_text(
                    "\n".join(
                        [
                            "---",
                            f"uuid: {uuid.uuid4()}",
                            f"title: {title}",
                            "category: Generated",
                            "epistemic: \"#derived\"",
                            f"updated_at: {now_iso()}",
                            f"source_document_id: {document_id}",
                            "---",
                            "",
                            f"# {title}",
                            "",
                            *body_sections,
                            "## Sources",
                            "",
                            *citations,
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )
                payload["written"].append(rel(article_path))
        else:
            result = default_task_store(config).raise_signal(
                task_type="anomaly",
                severity="low",
                role="curator",
                summary="Wiki build ran without RagPrep import state; generated only a platform status page.",
                evidence=[rel(wiki_root)],
                dedupe_key="wiki-build:no-import-state",
                created_by="wiki-build",
            )
            payload["tasks"].append({"trigger": "no_import_state", **result})
            report_md = wiki_root / "Wissenswerk_Platform_Status.md"
            ensure_dir(report_md.parent)
            report_md.write_text(
                "\n".join(
                    [
                        "---",
                        f"uuid: {uuid.uuid4()}",
                        "title: Wissenswerk Platform Status",
                        "category: System",
                        "epistemic: \"#meta\"",
                        f"updated_at: {now_iso()}",
                        "---",
                        "",
                        "# Wissenswerk Platform Status",
                        "",
                        "No RagPrep import state was found. Run ingest and analyze before building generated articles.",
                        "",
                        f"- Existing articles in wiki tree: {len(articles)}",
                        f"- Source roots: {', '.join(rel(path) for path in existing_sources) or '[UNRESOLVED]'}",
                        "- Retrieval default: pgvector",
                        "- Design contract: DESIGN.md",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            payload["written"].append(rel(report_md))
    report_path = write_report(config, "wiki_build", payload)
    payload["report_path"] = rel(report_path)
    json_print(payload) if args.json else print(f"legacy wiki build {payload['mode']}: report {rel(report_path)}")
    return 0


def gitignore_patterns() -> list[str]:
    gitignore = REPO_ROOT / ".gitignore"
    if not gitignore.exists():
        return []
    return [
        line.strip()
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def command_doctor(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    checks: list[dict[str, Any]] = []
    for path in [Path("AGENTS.md"), Path("DESIGN.md"), Path("project_manifest.json"), Path("wissenswerk.yaml")]:
        checks.append({"name": f"contract:{path}", "status": "pass" if repo_path(path).exists() else "fail"})
    public_files = [
        Path("SECURITY.md"),
        Path("SUPPORT.md"),
        Path("CODE_OF_CONDUCT.md"),
        Path("CONTRIBUTING.md"),
        Path("pyproject.toml"),
        Path(".github/workflows/ci.yml"),
        Path(".github/PULL_REQUEST_TEMPLATE.md"),
        Path(".github/ISSUE_TEMPLATE/bug_report.yml"),
        Path(".github/ISSUE_TEMPLATE/feature_request.yml"),
        Path(".github/CODEOWNERS"),
    ]
    missing_public_files = [rel(repo_path(path)) for path in public_files if not repo_path(path).exists()]
    mapped_contract_groups = {
        "public_agent_contract": [Path("AGENTS.md")],
        "public_license": [Path("LICENSE")],
        "public_manifest": [Path("project_manifest.json")],
        "public_config": [Path("wissenswerk.yaml")],
        "public_pyproject": [Path("pyproject.toml")],
    }
    missing_groups = [
        name
        for name, candidates in mapped_contract_groups.items()
        if not any(repo_path(candidate).exists() for candidate in candidates)
    ]
    checks.append(
        {
            "name": "github:community-and-ci-surfaces",
            "status": "pass" if not missing_public_files and not missing_groups else "fail",
            "missing": missing_public_files,
            "missing_contract_groups": missing_groups,
        }
    )
    roles = config.get("agents", {}).get("roles", [])
    checks.append(
        {
            "name": "agents:english-core-roles",
            "status": "pass" if roles == ["coordinator", "curator", "verifier", "maintainer"] else "fail",
            "value": roles,
        }
    )
    design_payload = lint_design(DEFAULT_DESIGN)
    checks.append({"name": "design:lint", "status": design_payload["status"], "summary": design_payload["summary"]})
    provider_payload = provider_status(config)
    checks.append(
        {
            "name": "providers:configured",
            "status": "pass" if provider_payload["status"] == "configured" else "fail",
            "runtime_status": provider_payload["runtime_status"],
        }
    )
    patterns = gitignore_patterns()
    required_patterns = [".env", ".env.*", "*.sqlite", "*.db", "*.dump", ".wissenswerk/"]
    missing_patterns = [pattern for pattern in required_patterns if pattern not in patterns]
    checks.append(
        {
            "name": "gitignore:wissenswerk-runtime",
            "status": "pass" if not missing_patterns else "warn",
            "missing": missing_patterns,
        }
    )
    try:
        store = default_task_store(config)
        with contextlib.closing(store.connect()):
            pass
        blocking_tasks = store.blocking_tasks()
        checks.append(
            {
                "name": "tasks:coordination-state",
                "status": "warn" if blocking_tasks else "pass",
                "store": rel(store.db_path),
                "blocking": len(blocking_tasks),
                "blocking_task_ids": [task["id"] for task in blocking_tasks],
            }
        )
    except (OSError, sqlite3.Error) as exc:
        checks.append({"name": "tasks:coordination-state", "status": "fail", "error": str(exc)})
    failures = [check for check in checks if check["status"] == "fail"]
    warnings = [check for check in checks if check["status"] == "warn"]
    payload = {
        "status": "fail" if failures else "warn" if warnings else "ok",
        "checks": checks,
        "next_commands": [
            "./wissenswerk.py setup --quick --profile local-history --json",
            "./wissenswerk.py ingest --from-ragprep tests/fixtures/ragprep --apply --json",
            "./wissenswerk.py build --apply --json",
            "./wissenswerk.py publish pages --dry-run --json",
        ],
    }
    json_print(payload) if args.json else print_doctor(payload)
    return 1 if failures else 0


def print_doctor(payload: dict[str, Any]) -> None:
    print(f"Wissenswerk doctor: {payload['status']}")
    for check in payload["checks"]:
        print(f"- {check['name']}: {check['status']}")


def existing_path_specs(paths: list[Path]) -> list[str]:
    return [rel(path) for path in paths if path.exists()]


def run_git_ls_files(paths: list[str] | None = None) -> list[str]:
    cmd = ["git", "ls-files"]
    if paths:
        cmd.extend(paths)
    result = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return 1
    return sum(1 for child in path.rglob("*") if child.is_file())


def report_hygiene_inventory() -> dict[str, Any]:
    roots = [
        {
            "path": "Logs",
            "classification": "legacy_report_archive",
            "publish_policy": "exclude_from_public_wissenswerk",
        },
        {
            "path": "System/Archivregister",
            "classification": "legacy_archive_index",
            "publish_policy": "exclude_from_public_wissenswerk",
        },
        {
            "path": ".wissenswerk",
            "classification": "generated_runtime_state",
            "publish_policy": "exclude_from_public_wissenswerk",
        },
        {
            "path": "docs/archive",
            "classification": "generated_or_historical_archive",
            "publish_policy": "exclude_from_public_wissenswerk",
        },
        {
            "path": "docs/Wissenswerk",
            "classification": "generic_documentation",
            "publish_policy": "include_documentation_subset",
        },
    ]
    entries = []
    total_files = 0
    tracked_files = 0
    total_bytes = 0
    for root in roots:
        root_path = repo_path(root["path"])
        tracked = run_git_ls_files([root["path"]])
        files = count_files(root_path)
        size = directory_size(root_path)
        total_files += files
        tracked_files += len(tracked)
        total_bytes += size
        entries.append(
            {
                **root,
                "exists": root_path.exists(),
                "files_total": files,
                "tracked_files": len(tracked),
                "bytes": size,
                "status": "review" if root["publish_policy"].startswith("exclude") and tracked else "ok",
            }
        )
    return {
        "status": "review" if tracked_files else "ok",
        "scope": "branch_cleanup",
        "roots": entries,
        "summary": {
            "files_total": total_files,
            "tracked_files": tracked_files,
            "bytes_total": total_bytes,
        },
        "recommendations": [
            "Do not include generated logs, legacy coordination state, archive indexes, runtime caches, or local archives in the public repository.",
            "Keep reports runtime-generated and ignored; commit only stable contracts, fixtures, and documentation dossiers.",
            "Use `./wissenswerk.py task digest --json` for active coordination instead of publishing local task state.",
            "Use doctor, test, hygiene reports, and git diff checks as the direct repository release gate.",
        ],
    }


def command_hygiene_reports(args: argparse.Namespace) -> int:
    payload = report_hygiene_inventory()
    json_print(payload) if args.json else print_hygiene_reports(payload)
    return 0


def print_hygiene_reports(payload: dict[str, Any]) -> None:
    print(f"Wissenswerk report hygiene: {payload['status']}")
    for root in payload["roots"]:
        print(
            f"- {root['path']}: files={root['files_total']} tracked={root['tracked_files']} "
            f"policy={root['publish_policy']}"
        )


def command_test(args: argparse.Namespace) -> int:
    test_root = repo_path(args.path)
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", rel(test_root)]
    result = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    payload = {
        "status": "pass" if result.returncode == 0 else "fail",
        "command": cmd,
        "path": rel(test_root),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    json_print(payload) if args.json else print_test(payload)
    return result.returncode


def print_test(payload: dict[str, Any]) -> None:
    print(f"Wissenswerk tests: {payload['status']}")
    if payload["stdout"]:
        print(payload["stdout"].rstrip())
    if payload["stderr"]:
        print(payload["stderr"].rstrip())


def reset_plan(config: dict[str, Any], target: str) -> dict[str, Any]:
    paths_cfg = config.get("paths", {})
    runtime_state = repo_path(paths_cfg.get("runtime_state", ".wissenswerk/state"))
    corpus_state = corpus_dir(config)
    reports = repo_path(paths_cfg.get("reports", "reports/wissenswerk"))
    wiki_root = repo_path(paths_cfg.get("wiki", "docs/Wiki"))
    specs: dict[str, dict[str, Any]] = {
        "memory": {
            "affected_paths": existing_path_specs([runtime_state / "memory"]),
            "protected_paths": ["Logs/Archive/SESSION_MEMORY_*.md"],
            "stale_indexes": [],
            "next_commands": ["./wissenswerk.py doctor --json"],
        },
        "index": {
            "affected_paths": existing_path_specs([runtime_state / "index", runtime_state / "legacy_vector_cache"]),
            "virtual_targets": [config.get("vector_store", {})],
            "protected_paths": [rel(corpus_state), rel(wiki_root)],
            "stale_indexes": ["pgvector", "lexical-bootstrap"],
            "next_commands": ["./wissenswerk.py ingest --from-ragprep <dir> --apply --json"],
        },
        "generated": {
            "affected_paths": existing_path_specs([reports, runtime_state / "curation", runtime_state / "generated"]),
            "protected_paths": [rel(corpus_state), rel(wiki_root)],
            "stale_indexes": [],
            "next_commands": ["./wissenswerk.py analyze --apply --json", "./wissenswerk.py plan articles --apply --json"],
        },
        "wiki": {
            "affected_paths": existing_path_specs([wiki_root / "Wissenswerk_Platform_Status.md", wiki_root / "Articles"]),
            "protected_paths": [rel(wiki_root), *[rel(repo_path(path)) for path in paths_cfg.get("sources", [])]],
            "stale_indexes": ["wiki"],
            "next_commands": ["./wissenswerk.py build --apply --json"],
        },
    }
    return specs[target]


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        for child in sorted(path.iterdir(), reverse=True):
            remove_path(child)
        path.rmdir()
    else:
        path.unlink()


def command_reset(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    plan = reset_plan(config, args.target)
    dry_run = bool(args.dry_run or not args.apply)
    confirmed = args.confirm == "APPLY-WISSENSWERK"
    status = "dry-run" if dry_run else "applied"
    task_events = []
    if args.apply and not dry_run and not confirmed:
        result = default_task_store(config).raise_signal(
            task_type="approval",
            severity="high",
            role="maintainer",
            summary=f"Reset apply requested for `{args.target}` without confirmation token.",
            evidence=plan.get("affected_paths", []),
            dedupe_key=f"approval:reset:{args.target}",
            created_by="reset",
        )
        task_events.append({"trigger": "reset_apply_without_confirm", **result})
        status = "blocked_approval_required"
    payload = {
        "status": status,
        "target": args.target,
        "dry_run": dry_run,
        "confirm_required": bool(args.apply and not dry_run),
        "confirm_token": "APPLY-WISSENSWERK" if args.apply and not dry_run else "",
        "affected_paths": plan.get("affected_paths", []),
        "virtual_targets": plan.get("virtual_targets", []),
        "protected_paths": plan.get("protected_paths", []),
        "stale_indexes": plan.get("stale_indexes", []),
        "next_commands": plan.get("next_commands", []),
        "tasks": task_events,
        "written": [],
        "removed": [],
    }
    if status == "applied":
        for value in payload["affected_paths"]:
            path = repo_path(value)
            remove_path(path)
            payload["removed"].append(value)
        report_path = write_report(config, f"reset_{args.target}", payload)
        payload["written"].append(rel(report_path))
    json_print(payload) if args.json else print_reset(payload)
    return 2 if status == "blocked_approval_required" else 0


def print_reset(payload: dict[str, Any]) -> None:
    print(f"Wissenswerk reset {payload['target']}: {payload['status']}")
    for path in payload["affected_paths"]:
        print(f"- affected: {path}")


def command_wipe(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    paths_cfg = config.get("paths", {})
    runtime_state = repo_path(paths_cfg.get("runtime_state", ".wissenswerk/state"))
    corpus_state = corpus_dir(config)
    reports = repo_path(paths_cfg.get("reports", "reports/wissenswerk"))
    wiki_root = repo_path(paths_cfg.get("wiki", "docs/Wiki"))
    tenant_paths = existing_path_specs([runtime_state, corpus_state, reports, wiki_root / "Wissenswerk_Platform_Status.md", wiki_root / "Articles"])
    protected = [rel(repo_path(path)) for path in paths_cfg.get("sources", [])]
    protected.extend([rel(wiki_root), "wissenswerk.yaml", "project_manifest.json", "AGENTS.md", "DESIGN.md"])
    needs_confirm = bool(args.apply and not (args.dry_run or not args.apply))
    confirmed = args.confirm == "WIPE-WISSENSWERK"
    dry_run = bool(args.dry_run or not args.apply)
    status = "dry-run"
    if args.apply and needs_confirm and not confirmed:
        status = "blocked_confirmation_required"
    elif args.apply and not dry_run:
        status = "applied"
    payload = {
        "status": status,
        "target": args.target,
        "dry_run": dry_run,
        "confirm_required": needs_confirm,
        "confirm_token": "WIPE-WISSENSWERK" if needs_confirm else "",
        "affected_paths": tenant_paths,
        "protected_paths": protected,
        "stale_indexes": ["pgvector", "wiki", "lexical-bootstrap"],
        "next_commands": ["./wissenswerk.py init --json", "./wissenswerk.py ingest --from-ragprep <dir> --apply --json"],
        "tasks": [],
        "removed": [],
        "written": [],
    }
    if status == "blocked_confirmation_required":
        result = default_task_store(config).raise_signal(
            task_type="approval",
            severity="critical" if args.target == "all" else "high",
            role="maintainer",
            summary=f"Wipe apply requested for `{args.target}` without confirmation token.",
            evidence=tenant_paths,
            dedupe_key=f"approval:wipe:{args.target}",
            created_by="wipe",
        )
        payload["tasks"].append({"trigger": "wipe_apply_without_confirm", **result})
    if status == "applied":
        for value in tenant_paths:
            remove_path(repo_path(value))
            payload["removed"].append(value)
        report_path = write_report(config, f"wipe_{args.target}", payload)
        payload["written"].append(rel(report_path))
    json_print(payload) if args.json else print_wipe(payload)
    return 2 if status == "blocked_confirmation_required" else 0


def print_wipe(payload: dict[str, Any]) -> None:
    print(f"Wissenswerk wipe {payload['target']}: {payload['status']}")
    for path in payload["affected_paths"]:
        print(f"- affected: {path}")


def lexical_search(config: dict[str, Any], query: str, source: str, limit: int) -> list[dict[str, Any]]:
    roots = []
    if source in {"raw", "all"}:
        roots.extend(repo_path(path) for path in config.get("paths", {}).get("sources", []))
    if source in {"wiki", "all"}:
        roots.append(repo_path(config.get("paths", {}).get("wiki", "docs/Wiki")))
    terms = [term.lower() for term in re.findall(r"\w+", query) if len(term) > 2]
    hits = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for file_path in sorted(root.rglob("*.md")):
            if file_path in seen:
                continue
            seen.add(file_path)
            raw = file_path.read_text(encoding="utf-8", errors="ignore")
            haystack = raw.lower()
            score = sum(haystack.count(term) for term in terms)
            if score <= 0:
                continue
            snippet_start = min([haystack.find(term) for term in terms if haystack.find(term) >= 0] or [0])
            snippet = re.sub(r"\s+", " ", raw[snippet_start : snippet_start + 500]).strip()
            hits.append({"path": rel(file_path), "score": score, "snippet": snippet})
    hits.sort(key=lambda item: item["score"], reverse=True)
    return hits[:limit]


def command_search(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    hits = lexical_search(config, args.query, args.source, args.top)
    payload = {
        "status": "ok",
        "query": args.query,
        "source": args.source,
        "retrieval": "lexical-bootstrap",
        "vector_store": config.get("vector_store", {}).get("kind", ""),
        "hits": hits,
        "count": len(hits),
    }
    json_print(payload) if args.json else print_search(payload)
    return 0


def print_search(payload: dict[str, Any]) -> None:
    print(f"Wissenswerk search: {payload['query']} ({payload['source']})")
    for hit in payload["hits"]:
        print(f"- {hit['path']} score={hit['score']}: {hit['snippet'][:180]}")


def wiki_article_files(config: dict[str, Any]) -> list[Path]:
    wiki_root = repo_path(config.get("paths", {}).get("wiki", "docs/Wiki"))
    if not wiki_root.exists():
        return []
    return sorted(path for path in wiki_root.rglob("*.md") if path.is_file())


def public_leak_findings(config: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    forbidden = [str(REPO_ROOT), str(Path.home()), "OPENAI_API_KEY", "DISCORD_BOT_TOKEN", "WISSENSWERK_DATABASE_URL"]
    for path in wiki_article_files(config):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in forbidden:
            if pattern and pattern in raw:
                findings.append({"path": rel(path), "kind": "private_pattern", "pattern": pattern})
        if re.search(r"\bsource_path\s*:", raw):
            findings.append({"path": rel(path), "kind": "raw_source_path_key", "pattern": "source_path:"})
    return findings


def audit_payload(config: dict[str, Any]) -> dict[str, Any]:
    wiki_root = repo_path(config.get("paths", {}).get("wiki", "docs/Wiki"))
    articles = [path for path in wiki_article_files(config) if not path.name.endswith(".provenance.md")]
    missing_sources = []
    missing_provenance = []
    for path in articles:
        if path.name == "index.md":
            continue
        raw = path.read_text(encoding="utf-8", errors="ignore")
        if "## Sources" not in raw:
            missing_sources.append(rel(path))
        provenance = path.with_suffix(".provenance.json")
        if not provenance.exists():
            missing_provenance.append(rel(path))
    leaks = public_leak_findings(config)
    analysis = latest_analysis(config)
    blocking_tasks = default_task_store(config).blocking_tasks()
    findings = []
    findings.extend({"severity": "high", "kind": "missing_sources", "path": path} for path in missing_sources)
    findings.extend({"severity": "medium", "kind": "missing_provenance", "path": path} for path in missing_provenance)
    findings.extend({"severity": "critical", **finding} for finding in leaks)
    for conflict in analysis.get("conflict_candidates", []):
        findings.append({"severity": conflict.get("severity", "low"), "kind": "conflict_candidate", "subject": conflict.get("subject", "")})
    return {
        "status": "fail" if leaks or missing_sources else "warn" if missing_provenance or blocking_tasks else "pass",
        "generated_at": now_iso(),
        "wiki_root": rel(wiki_root),
        "articles_checked": len(articles),
        "findings": findings,
        "blocking_tasks": blocking_tasks,
        "summary": {
            "missing_sources": len(missing_sources),
            "missing_provenance": len(missing_provenance),
            "private_leaks": len(leaks),
            "blocking_tasks": len(blocking_tasks),
            "conflict_candidates": len(analysis.get("conflict_candidates", [])),
        },
    }


def command_audit(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    payload = audit_payload(config)
    report_path = write_report(config, "audit", payload)
    payload["report_path"] = rel(report_path)
    json_print(payload) if args.json else print(f"Wissenswerk audit: {payload['status']}")
    return 1 if payload["status"] == "fail" else 0


def stats_payload(config: dict[str, Any]) -> dict[str, Any]:
    analysis = latest_analysis(config)
    plan = latest_article_plan(config)
    articles = wiki_article_files(config)
    store = default_task_store(config)
    candidates = plan.get("article_candidates", [])
    return {
        "status": "ok",
        "generated_at": now_iso(),
        "documents": analysis.get("corpus_inventory", {}).get("documents_total", 0),
        "segments": analysis.get("corpus_inventory", {}).get("segments_total", 0),
        "languages": analysis.get("corpus_inventory", {}).get("languages", {}),
        "entities": len(analysis.get("entities", [])),
        "claims": len(analysis.get("claims", [])),
        "article_candidates": len(candidates),
        "article_candidates_by_priority": {
            priority: sum(1 for item in candidates if item.get("priority") == priority)
            for priority in ["A", "B", "C", "D", "E"]
        },
        "generated_pages": len([path for path in articles if path.suffix == ".md"]),
        "source_coverage": analysis.get("source_coverage", {}),
        "conflicts": len(analysis.get("conflict_candidates", [])),
        "tasks": store.status_counts(),
        "top_concepts": [
            {"name": entity.get("name", ""), "mentions": entity.get("mentions", 0)}
            for entity in analysis.get("entities", [])[:10]
        ],
    }


def command_stats(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    payload = stats_payload(config)
    report_path = write_report(config, "stats", payload)
    payload["report_path"] = rel(report_path)
    json_print(payload) if args.json else print(f"Wissenswerk stats: {payload['documents']} documents, {payload['segments']} evidence segments, {payload['generated_pages']} pages")
    return 0


def demo_report_payload(config: dict[str, Any]) -> dict[str, Any]:
    stats = stats_payload(config)
    audit = audit_payload(config)
    project = config.get("project", {})
    return {
        "status": "ready" if audit["status"] in {"pass", "warn"} else "needs_review",
        "generated_at": now_iso(),
        "project": project,
        "summary": {
            "documents": stats["documents"],
            "segments": stats["segments"],
            "entities": stats["entities"],
            "claims": stats["claims"],
            "article_candidates": stats["article_candidates"],
            "generated_pages": stats["generated_pages"],
            "conflicts": stats["conflicts"],
            "audit_status": audit["status"],
        },
        "top_concepts": stats["top_concepts"],
        "next_steps": [
            "./wissenswerk.py publish pages --dry-run --json",
            "Review open conflicts and source-rights notes before public release.",
        ],
    }


def write_demo_report(config: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    reports_dir = repo_path(config.get("paths", {}).get("reports", "reports/wissenswerk"))
    ensure_dir(reports_dir)
    json_path = reports_dir / "demo_summary.json"
    md_path = reports_dir / "demo_summary.md"
    write_json(json_path, payload)
    summary = payload["summary"]
    md_path.write_text(
        "\n".join(
            [
                "# Wissenswerk Demo Summary",
                "",
                f"- Status: {payload['status']}",
                f"- Project: {payload.get('project', {}).get('name', 'Wissenswerk Project')}",
                f"- Documents: {summary['documents']}",
                f"- Evidence segments: {summary['segments']}",
                f"- Entities: {summary['entities']}",
                f"- Claims: {summary['claims']}",
                f"- Article candidates: {summary['article_candidates']}",
                f"- Generated pages: {summary['generated_pages']}",
                f"- Conflicts: {summary['conflicts']}",
                f"- Audit status: {summary['audit_status']}",
                "",
                "## Top Concepts",
                "",
                *[f"- {item['name']} ({item['mentions']})" for item in payload.get("top_concepts", [])],
                "",
            ]
        ),
        encoding="utf-8",
    )
    return [rel(json_path), rel(md_path)]


def command_demo_report(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    payload = demo_report_payload(config)
    payload["written"] = write_demo_report(config, payload)
    json_print(payload) if args.json else print(f"Wissenswerk demo report: {payload['status']}")
    return 0 if payload["status"] == "ready" else 1


def command_demo_run(args: argparse.Namespace) -> int:
    config_path = repo_path(args.config)
    config = load_config(config_path) if config_path.exists() else default_config_payload()
    setup_args = argparse.Namespace(
        config=str(config_path),
        quick=True,
        guided=False,
        profile=args.profile,
        project_name=args.project_name or config.get("project", {}).get("name", "Example Corpus"),
        from_ragprep=args.from_ragprep,
        language=args.language or config.get("project", {}).get("language", "en"),
        publication_mode="private-sources-public-wiki",
        source_languages=args.language or config.get("project", {}).get("language", "en"),
        audience="interested public,agents",
        citation_policy="section",
        article_granularity="medium",
        uncertainty_policy="explicit",
        json=True,
    )
    step_outputs: dict[str, str] = {}

    def run_step(name: str, func: Any, namespace: argparse.Namespace) -> int:
        progress(f"demo run: starting {name}")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = func(namespace)
        step_outputs[name] = out.getvalue()
        progress(f"demo run: finished {name} exit={code}")
        return code

    setup_code = run_step("setup", command_setup, setup_args)
    ingest_code = run_step("ingest", command_ingest, argparse.Namespace(config=str(config_path), from_ragprep=args.from_ragprep, apply=args.apply, json=True))
    analyze_code = run_step("analyze", command_analyze, argparse.Namespace(config=str(config_path), apply=args.apply, json=True))
    plan_code = run_step("plan_articles", command_plan_articles, argparse.Namespace(config=str(config_path), apply=args.apply, json=True))
    build_code = run_step("build", command_build, argparse.Namespace(config=str(config_path), apply=args.apply, json=True))
    audit_code = run_step("audit", command_audit, argparse.Namespace(config=str(config_path), json=True))
    stats_code = run_step("stats", command_stats, argparse.Namespace(config=str(config_path), json=True))
    report_code = run_step("demo_report", command_demo_report, argparse.Namespace(config=str(config_path), json=True))
    payload = {
        "status": "ready" if max(setup_code, ingest_code, analyze_code, plan_code, build_code, audit_code, stats_code, report_code) == 0 else "needs_review",
        "steps": {
            "setup": setup_code,
            "ingest": ingest_code,
            "analyze": analyze_code,
            "plan_articles": plan_code,
            "build": build_code,
            "audit": audit_code,
            "stats": stats_code,
            "demo_report": report_code,
        },
        "step_output_json": step_outputs,
    }
    json_print(payload) if args.json else print(f"Wissenswerk demo run: {payload['status']}")
    return 0 if payload["status"] == "ready" else 1


def command_publish_pages(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    wiki_root = repo_path(config.get("paths", {}).get("wiki", "docs/Wiki"))
    audit = audit_payload(config)
    demo_json = repo_path(config.get("paths", {}).get("reports", "reports/wissenswerk")) / "demo_summary.json"
    has_index = (wiki_root / "index.md").exists()
    has_articles = bool((wiki_root / "Articles").exists() and list((wiki_root / "Articles").glob("*.md")))
    blockers = []
    if not has_index:
        blockers.append({"kind": "missing_index", "path": rel(wiki_root / "index.md")})
    if not has_articles:
        blockers.append({"kind": "missing_articles", "path": rel(wiki_root / "Articles")})
    if not demo_json.exists():
        blockers.append({"kind": "missing_demo_summary", "path": rel(demo_json)})
    if audit["summary"]["private_leaks"]:
        blockers.append({"kind": "private_source_leak", "count": audit["summary"]["private_leaks"]})
    if audit["summary"]["blocking_tasks"]:
        blockers.append({"kind": "blocking_tasks", "count": audit["summary"]["blocking_tasks"]})
    workflow_path = repo_path(".github/workflows/pages.yml")
    payload = {
        "status": "blocked" if blockers else "ready",
        "mode": "auto-apply" if args.apply else "dry-run",
        "target": "github-pages",
        "wiki_root": rel(wiki_root),
        "checks": {
            "has_index": has_index,
            "has_articles": has_articles,
            "has_demo_summary": demo_json.exists(),
            "has_pages_workflow": workflow_path.exists(),
            "audit_status": audit["status"],
        },
        "blockers": blockers,
        "written": [],
        "next_manual_steps": [
            "Create or select the GitHub repository.",
            "Push the checked tree.",
            "Enable GitHub Pages from Actions.",
        ],
    }
    if args.apply and not blockers and not workflow_path.exists():
        ensure_dir(workflow_path.parent)
        workflow_path.write_text(
            "\n".join(
                [
                    "name: Publish Wissenswerk Pages",
                    "",
                    "on:",
                    "  push:",
                    "    branches: [main]",
                    "",
                    "permissions:",
                    "  contents: read",
                    "  pages: write",
                    "  id-token: write",
                    "",
                    "jobs:",
                    "  pages:",
                    "    runs-on: ubuntu-latest",
                    "    steps:",
                    "      - uses: actions/checkout@v4",
                    "      - uses: actions/configure-pages@v5",
                    "      - uses: actions/upload-pages-artifact@v3",
                    "        with:",
                    "          path: docs",
                    "      - uses: actions/deploy-pages@v4",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        payload["written"].append(rel(workflow_path))
    json_print(payload) if args.json else print(f"Wissenswerk publish pages: {payload['status']}")
    return 1 if blockers else 0


def command_bot_discord(args: argparse.Namespace) -> int:
    config = load_config(repo_path(args.config))
    discord_cfg = config.get("bot", {}).get("discord", {})
    token_env = discord_cfg.get("token_env", "DISCORD_BOT_TOKEN")
    approval_required = bool(args.run and os.environ.get(token_env) and args.confirm != "RUN-WISSENSWERK-BOT")
    payload = {
        "status": "approval_required" if approval_required else "ready" if os.environ.get(token_env) else "missing_token",
        "adapter": "discord",
        "enabled": bool(discord_cfg.get("enabled", False)),
        "token_env": token_env,
        "token_present": bool(os.environ.get(token_env)),
        "command_prefix": discord_cfg.get("command_prefix", "!ww"),
        "run": bool(args.run),
        "confirm_required": approval_required,
        "confirm_token": "RUN-WISSENSWERK-BOT" if approval_required else "",
        "tasks": [],
        "note": "Bootstrap adapter. Use --run only after installing a Discord runtime package.",
    }
    if args.run and not payload["token_present"]:
        json_print(payload) if args.json else print("Discord token missing")
        return 2
    if approval_required:
        result = default_task_store(config).raise_signal(
            task_type="approval",
            severity="high",
            role="maintainer",
            summary="Discord bot live run requested without confirmation token.",
            evidence=["wissenswerk.yaml"],
            dedupe_key="approval:bot:discord:run",
            created_by="bot-discord",
        )
        payload["tasks"].append({"trigger": "discord_run_without_confirm", **result})
        json_print(payload) if args.json else print("Discord bot run requires approval")
        return 2
    json_print(payload) if args.json else print(f"Discord adapter: {payload['status']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Wissenswerk corpus-to-wiki platform CLI")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to wissenswerk.yaml")
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init", help="Create a starter Wissenswerk tenant config")
    init.add_argument("--config", default="wissenswerk.yaml")
    init.add_argument("--force", action="store_true")
    init.add_argument("--json", action="store_true")

    setup = sub.add_parser("setup", help="Create a project profile and demo-ready Wissenswerk configuration")
    setup_mode = setup.add_mutually_exclusive_group()
    setup_mode.add_argument("--quick", action="store_true", help="Use the minimal home/demo setup")
    setup_mode.add_argument("--guided", action="store_true", help="Use the expanded productive setup shape")
    setup.add_argument("--profile", default="local-history")
    setup.add_argument("--project-name", default="")
    setup.add_argument("--from-ragprep", default="")
    setup.add_argument("--language", default="")
    setup.add_argument("--source-languages", default="")
    setup.add_argument("--publication-mode", default="private-sources-public-wiki")
    setup.add_argument("--audience", default="")
    setup.add_argument("--citation-policy", default="")
    setup.add_argument("--article-granularity", default="")
    setup.add_argument("--uncertainty-policy", default="")
    setup.add_argument("--json", action="store_true")

    ingest = sub.add_parser("ingest", help="Import source documents and RagPrep evidence segments")
    ingest.add_argument("--from-ragprep", required=True)
    ingest.add_argument("--apply", action="store_true")
    ingest.add_argument("--json", action="store_true")

    analyze = sub.add_parser("analyze", help="Build corpus inventory, claims, graph, coverage, and conflicts")
    analyze.add_argument("--apply", action="store_true")
    analyze.add_argument("--json", action="store_true")

    plan_cmd = sub.add_parser("plan", help="Plan generated knowledge artifacts")
    plan_sub = plan_cmd.add_subparsers(dest="plan_command")
    plan_articles = plan_sub.add_parser("articles", help="Plan prioritized wiki article candidates")
    plan_articles.add_argument("--apply", action="store_true")
    plan_articles.add_argument("--json", action="store_true")

    curate = sub.add_parser("curate", help="Compatibility article-candidate command; prefer `plan articles`")
    curate.add_argument("--json", action="store_true")

    build = sub.add_parser("build", help="Build generated wiki artifacts from article plans")
    build.add_argument("--apply", action="store_true")
    build.add_argument("--json", action="store_true")

    wiki = sub.add_parser("wiki", help="Build or inspect generated wiki artifacts")
    wiki_sub = wiki.add_subparsers(dest="wiki_command")
    wiki_build = wiki_sub.add_parser("build", help="Build wiki artifacts")
    wiki_build.add_argument("--apply", action="store_true")
    wiki_build.add_argument("--json", action="store_true")

    search = sub.add_parser("search", help="Search the configured corpus/wiki")
    search.add_argument("query")
    search.add_argument("--source", choices=["raw", "wiki", "all"], default="wiki")
    search.add_argument("--top", type=int, default=5)
    search.add_argument("--json", action="store_true")

    providers = sub.add_parser("providers", help="Inspect configured model/vector providers")
    providers_sub = providers.add_subparsers(dest="providers_command")
    providers_check = providers_sub.add_parser("check", help="Check provider configuration")
    providers_check.add_argument("--json", action="store_true")

    doctor = sub.add_parser("doctor", help="Run dependency-light Wissenswerk health checks")
    doctor.add_argument("--json", action="store_true")

    audit = sub.add_parser("audit", help="Audit generated wiki pages, provenance, conflicts, and publish safety")
    audit.add_argument("--json", action="store_true")

    stats = sub.add_parser("stats", help="Summarize corpus, graph, article, wiki, conflict, and task counts")
    stats.add_argument("--json", action="store_true")

    publish = sub.add_parser("publish", help="Prepare publish targets without remote side effects")
    publish_sub = publish.add_subparsers(dest="publish_command")
    publish_pages = publish_sub.add_parser("pages", help="Prepare or check GitHub Pages publishing")
    publish_pages.add_argument("--dry-run", action="store_true")
    publish_pages.add_argument("--apply", action="store_true")
    publish_pages.add_argument("--json", action="store_true")

    reset = sub.add_parser("reset", help="Reset selected generated Wissenswerk state")
    reset.add_argument("target", choices=["memory", "index", "generated", "wiki"])
    reset.add_argument("--dry-run", action="store_true")
    reset.add_argument("--apply", action="store_true")
    reset.add_argument("--confirm", default="")
    reset.add_argument("--json", action="store_true")

    wipe = sub.add_parser("wipe", help="Wipe tenant or local Wissenswerk state with safeguards")
    wipe.add_argument("target", choices=["tenant", "all"])
    wipe.add_argument("--dry-run", action="store_true")
    wipe.add_argument("--apply", action="store_true")
    wipe.add_argument("--confirm", default="")
    wipe.add_argument("--json", action="store_true")

    design = sub.add_parser("design", help="Inspect the DESIGN.md contract")
    design_sub = design.add_subparsers(dest="design_command")
    design_lint = design_sub.add_parser("lint", help="Lint DESIGN.md")
    design_lint.add_argument("file", nargs="?", default=str(DEFAULT_DESIGN))
    design_lint.add_argument("--json", action="store_true")

    bot = sub.add_parser("bot", help="Run or inspect bot adapters")
    bot_sub = bot.add_subparsers(dest="bot_command")
    discord = bot_sub.add_parser("discord", help="Discord bot adapter")
    discord.add_argument("--run", action="store_true")
    discord.add_argument("--confirm", default="")
    discord.add_argument("--json", action="store_true")

    demo = sub.add_parser("demo", help="Run or report a one-command inspectable demo pipeline")
    demo_sub = demo.add_subparsers(dest="demo_command")
    demo_run = demo_sub.add_parser("run", help="Run setup, ingest, analyze, plan, build, audit, stats, and report")
    demo_run.add_argument("--from-ragprep", required=True)
    demo_run.add_argument("--profile", default="local-history")
    demo_run.add_argument("--project-name", default="")
    demo_run.add_argument("--language", default="")
    demo_run.add_argument("--apply", action="store_true")
    demo_run.add_argument("--json", action="store_true")
    demo_report = demo_sub.add_parser("report", help="Write demo summary artifacts")
    demo_report.add_argument("--json", action="store_true")

    task = sub.add_parser("task", help="Manage Signals & Tasks coordination state")
    task_sub = task.add_subparsers(dest="task_command")
    task_raise = task_sub.add_parser("raise", help="Raise a coordination signal")
    task_raise.add_argument("--type", required=True, choices=sorted(TASK_TYPES))
    task_raise.add_argument("--severity", required=True, choices=sorted(TASK_SEVERITIES))
    task_raise.add_argument("--role", default="coordinator", choices=sorted(TASK_ROLES))
    task_raise.add_argument("--summary", required=True)
    task_raise.add_argument("--evidence", action="append", default=[])
    task_raise.add_argument("--dedupe-key", default="")
    task_raise.add_argument("--created-by", default="agent")
    task_raise.add_argument("--artifact", action="append", default=[])
    task_raise.add_argument("--ttl-days", type=int, default=30)
    task_raise.add_argument("--parent-id", default="")
    task_raise.add_argument("--json", action="store_true")
    task_list = task_sub.add_parser("list", help="List coordination tasks")
    task_list.add_argument("--status", choices=sorted(TASK_STATUSES))
    task_list.add_argument("--json", action="store_true")
    task_show = task_sub.add_parser("show", help="Show one coordination task")
    task_show.add_argument("id")
    task_show.add_argument("--json", action="store_true")
    task_claim = task_sub.add_parser("claim", help="Claim one coordination task")
    task_claim.add_argument("id")
    task_claim.add_argument("--agent", required=True, choices=sorted(TASK_ROLES))
    task_claim.add_argument("--json", action="store_true")
    task_resolve = task_sub.add_parser("resolve", help="Resolve one coordination task")
    task_resolve.add_argument("id")
    task_resolve.add_argument("--summary", required=True)
    task_resolve.add_argument("--json", action="store_true")
    task_reject = task_sub.add_parser("reject", help="Reject one coordination task")
    task_reject.add_argument("id")
    task_reject.add_argument("--reason", required=True)
    task_reject.add_argument("--json", action="store_true")
    task_digest = task_sub.add_parser("digest", help="Summarize active coordination tasks")
    task_digest.add_argument("--since", default="24h")
    task_digest.add_argument("--json", action="store_true")

    run = sub.add_parser("run", help="Inspect current local run and coordination state")
    run_sub = run.add_subparsers(dest="run_command")
    run_status = run_sub.add_parser("status", help="Show local run status")
    run_status.add_argument("--json", action="store_true")

    hygiene = sub.add_parser("hygiene", help="Inspect publishability and runtime/report ballast")
    hygiene_sub = hygiene.add_subparsers(dest="hygiene_command")
    hygiene_reports = hygiene_sub.add_parser("reports", help="Inventory report, archive, and runtime state")
    hygiene_reports.add_argument("--json", action="store_true")

    test_cmd = sub.add_parser("test", help="Run standalone Wissenswerk unit tests")
    test_cmd.add_argument("--path", default="tests")
    test_cmd.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return command_init(args)
    if args.command == "setup":
        return command_setup(args)
    if args.command == "ingest":
        return command_ingest(args)
    if args.command == "analyze":
        return command_analyze(args)
    if args.command == "plan" and args.plan_command == "articles":
        return command_plan_articles(args)
    if args.command == "curate":
        return command_curate(args)
    if args.command == "build":
        return command_build(args)
    if args.command == "wiki" and args.wiki_command == "build":
        return command_wiki_build(args)
    if args.command == "search":
        return command_search(args)
    if args.command == "providers" and args.providers_command == "check":
        return command_providers_check(args)
    if args.command == "doctor":
        return command_doctor(args)
    if args.command == "audit":
        return command_audit(args)
    if args.command == "stats":
        return command_stats(args)
    if args.command == "publish" and args.publish_command == "pages":
        return command_publish_pages(args)
    if args.command == "reset":
        return command_reset(args)
    if args.command == "wipe":
        return command_wipe(args)
    if args.command == "design" and args.design_command == "lint":
        return command_design_lint(args)
    if args.command == "bot" and args.bot_command == "discord":
        return command_bot_discord(args)
    if args.command == "demo" and args.demo_command == "run":
        return command_demo_run(args)
    if args.command == "demo" and args.demo_command == "report":
        return command_demo_report(args)
    if args.command == "task" and args.task_command == "raise":
        return command_task_raise(args)
    if args.command == "task" and args.task_command == "list":
        return command_task_list(args)
    if args.command == "task" and args.task_command == "show":
        return command_task_show(args)
    if args.command == "task" and args.task_command == "claim":
        return command_task_claim(args)
    if args.command == "task" and args.task_command == "resolve":
        return command_task_resolve(args)
    if args.command == "task" and args.task_command == "reject":
        return command_task_reject(args)
    if args.command == "task" and args.task_command == "digest":
        return command_task_digest(args)
    if args.command == "run" and args.run_command == "status":
        return command_run_status(args)
    if args.command == "hygiene" and args.hygiene_command == "reports":
        return command_hygiene_reports(args)
    if args.command == "test":
        return command_test(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
