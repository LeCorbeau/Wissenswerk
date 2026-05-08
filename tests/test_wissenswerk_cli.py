import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import wissenswerk


class WissenswerkCliContractTests(unittest.TestCase):
    def temp_config(self, root: Path) -> Path:
        config = wissenswerk.default_config_payload()
        config["paths"]["tasks"] = str(root / "tasks")
        config["paths"]["reports"] = str(root / "reports")
        config["paths"]["corpus"] = str(root / "corpus_state")
        config["paths"]["runtime_state"] = str(root / "state")
        config["paths"]["profile"] = str(root / "profile")
        config["paths"]["analysis"] = str(root / "analysis")
        config["paths"]["article_plans"] = str(root / "article_plans")
        config["paths"]["wiki"] = str(root / "wiki")
        config["paths"]["project_docs"] = str(root / "docs")
        config["paths"]["sources"] = [str(root / "sources")]
        config_path = root / "wissenswerk.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return config_path

    def test_default_config_is_generic(self):
        config = wissenswerk.default_config_payload()
        self.assertEqual(config["project"]["product"], "Wissenswerk")
        self.assertEqual(config["project"]["tenant_id"], "example")
        self.assertEqual(config["vector_store"]["kind"], "pgvector")
        self.assertEqual(config["agents"]["roles"], ["coordinator", "curator", "verifier", "maintainer"])

    def test_provider_status_separates_configured_from_runtime_ready(self):
        payload = wissenswerk.provider_status(wissenswerk.default_config_payload())
        self.assertEqual(payload["status"], "configured")
        self.assertIn(payload["runtime_status"], {"ready", "missing_credentials"})
        self.assertEqual(payload["vector_store"]["kind"], "pgvector")

    def test_export_plan_has_no_public_blockers(self):
        payload = wissenswerk.export_plan(wissenswerk.DEFAULT_EXPORT_MANIFEST)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["blockers"], [])
        self.assertEqual(payload["summary"]["overlap"], [])

    def test_export_materialize_applies_public_mappings(self):
        payload = wissenswerk.export_materialize_plan(
            wissenswerk.DEFAULT_EXPORT_MANIFEST,
            wissenswerk.REPO_ROOT / ".tmp" / "wissenswerk-export",
        )
        destinations = {operation["destination"] for operation in payload["operations"]}
        self.assertIn("AGENTS.md", destinations)
        self.assertIn("LICENSE", destinations)
        self.assertIn("project_manifest.json", destinations)
        self.assertIn("pyproject.toml", destinations)
        self.assertIn("Makefile", destinations)
        self.assertIn("wissenswerk.yaml", destinations)

    def test_materialized_manifest_uses_public_names(self):
        payload = wissenswerk.materialized_manifest_payload(wissenswerk.DEFAULT_EXPORT_MANIFEST)
        flattened = wissenswerk.flatten_manifest_paths(payload.get("include", {}))
        self.assertIn("AGENTS.md", flattened)
        self.assertIn("LICENSE", flattened)
        self.assertIn("project_manifest.json", flattened)
        self.assertIn("pyproject.toml", flattened)
        self.assertIn("Makefile", flattened)
        self.assertIn("wissenswerk.yaml", flattened)
        self.assertEqual(payload.get("export_mappings"), {})

    def test_manifest_spec_overlap_detects_directory_specs(self):
        self.assertTrue(wissenswerk.manifest_spec_matches_file("docs/Wissenswerk/", "docs/Wissenswerk/index.md"))
        self.assertFalse(wissenswerk.manifest_spec_matches_file("docs/Wissenswerk/", "docs/setup_rag.md"))

    def test_task_store_dedupes_open_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = wissenswerk.TaskStore(Path(tmp) / "tasks")
            first = store.raise_signal(
                task_type="anomaly",
                severity="medium",
                role="curator",
                summary="Duplicate segment id",
                evidence=["fixture.json"],
                dedupe_key="test:duplicate",
                created_by="test",
            )
            second = store.raise_signal(
                task_type="anomaly",
                severity="high",
                role="curator",
                summary="Duplicate segment id again",
                evidence=["fixture.json"],
                dedupe_key="test:duplicate",
                created_by="test",
            )
            self.assertEqual(first["status"], "created")
            self.assertEqual(second["status"], "deduped")
            self.assertEqual(first["task"]["id"], second["task"]["id"])
            self.assertEqual(second["task"]["repeat_count"], 2)
            self.assertTrue((Path(tmp) / "tasks" / "active" / f"{first['task']['id']}.md").exists())

    def test_task_store_allocates_after_existing_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = wissenswerk.TaskStore(Path(tmp) / "tasks")
            current_year = wissenswerk.datetime.now(wissenswerk.timezone.utc).year
            with contextlib.closing(store.connect()) as conn:
                for task_id in [f"TASK-{current_year}-0001", f"TASK-{current_year}-0010", f"TASK-{current_year}-notnumeric"]:
                    conn.execute(
                        """
                        INSERT INTO tasks (
                          id, type, severity, status, role, summary, evidence_json, dedupe_key,
                          created_by, claimed_by, created_at, updated_at, artifacts_json, ttl_days,
                          parent_id, repeat_count, last_evidence_json, resolution
                        )
                        VALUES (?, 'anomaly', 'low', 'completed', 'curator', 'seed', '[]', NULL,
                                'test', NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',
                                '[]', 30, NULL, 1, '[]', 'seed')
                        """,
                        (task_id,),
                    )
                conn.commit()
            created = store.raise_signal(
                task_type="anomaly",
                severity="medium",
                role="curator",
                summary="New task after existing ids",
                created_by="test",
            )["task"]
            self.assertEqual(created["id"], f"TASK-{current_year}-0011")

    def test_task_lifecycle_removes_active_markdown_on_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = wissenswerk.TaskStore(Path(tmp) / "tasks")
            created = store.raise_signal(
                task_type="approval",
                severity="critical",
                role="maintainer",
                summary="Approval required",
                dedupe_key="test:approval",
                created_by="test",
            )["task"]
            claimed = store.claim(created["id"], "maintainer")
            self.assertEqual(claimed["status"], "working")
            resolved = store.resolve(created["id"], "Approved elsewhere")
            self.assertEqual(resolved["status"], "completed")
            self.assertFalse((Path(tmp) / "tasks" / "active" / f"{created['id']}.md").exists())

    def test_task_cli_raise_and_digest_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = wissenswerk.default_config_payload()
            config["paths"]["tasks"] = str(Path(tmp) / "tasks")
            config_path = Path(tmp) / "wissenswerk.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = wissenswerk.main(
                    [
                        "--config",
                        str(config_path),
                        "task",
                        "raise",
                        "--type",
                        "blocker",
                        "--severity",
                        "high",
                        "--role",
                        "maintainer",
                        "--summary",
                        "Provider unavailable",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            raised = json.loads(out.getvalue())
            self.assertEqual(raised["status"], "created")

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = wissenswerk.main(["--config", str(config_path), "task", "digest", "--since", "24h", "--json"])
            self.assertEqual(code, 0)
            digest = json.loads(out.getvalue())
            self.assertEqual(digest["status"], "ok")
            self.assertEqual(len(digest["open"]), 1)

    def test_ingest_missing_locator_raises_deduped_audit_task_without_hard_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = wissenswerk.default_config_payload()
            config["paths"]["tasks"] = str(root / "tasks")
            config["paths"]["reports"] = str(root / "reports")
            config["paths"]["corpus"] = str(root / "corpus_state")
            config_path = root / "wissenswerk.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            ragprep = root / "ragprep"
            ragprep.mkdir()
            (ragprep / "bad.json").write_text(
                json.dumps({"document_id": "doc-1", "chunk_id": "chunk-1", "text": "hello"}),
                encoding="utf-8",
            )

            for _ in range(2):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = wissenswerk.main(
                        ["--config", str(config_path), "ingest", "--from-ragprep", str(ragprep), "--apply", "--json"]
                    )
                self.assertEqual(code, 0)
                payload = json.loads(out.getvalue())
                self.assertEqual(payload["status"], "ready")
                self.assertEqual(payload["segments_total"], 1)
                self.assertNotIn("chunks_total", payload)
                self.assertEqual(len(payload["metadata_findings"]), 1)
                self.assertTrue((root / "corpus_state" / "source_documents.json").exists())
                self.assertTrue((root / "corpus_state" / "evidence_segments.json").exists())
                self.assertTrue((root / "corpus_state" / "import_manifest.json").exists())
            tasks = wissenswerk.TaskStore(root / "tasks").list()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["type"], "audit_finding")
            self.assertEqual(tasks[0]["repeat_count"], 2)

    def test_ingest_missing_required_segment_field_hard_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = wissenswerk.default_config_payload()
            config["paths"]["tasks"] = str(root / "tasks")
            config["paths"]["reports"] = str(root / "reports")
            config["paths"]["corpus"] = str(root / "corpus_state")
            config_path = root / "wissenswerk.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            ragprep = root / "ragprep"
            ragprep.mkdir()
            (ragprep / "bad.json").write_text(
                json.dumps({"document_id": "doc-1", "chunk_id": "chunk-1", "source_url": "https://example.test/source"}),
                encoding="utf-8",
            )

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = wissenswerk.main(
                    ["--config", str(config_path), "ingest", "--from-ragprep", str(ragprep), "--apply", "--json"]
                )
            self.assertEqual(code, 1)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["status"], "fail")
            self.assertEqual(payload["validation_findings"][0]["missing"], ["text"])
            self.assertFalse((root / "corpus_state" / "import_manifest.json").exists())

    def test_ingest_accepts_source_url_without_source_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self.temp_config(root)
            ragprep = root / "ragprep"
            ragprep.mkdir()
            (ragprep / "with_url.json").write_text(
                json.dumps({"document_id": "doc-1", "chunk_id": "chunk-1", "text": "hello", "source_url": "https://example.test/source"}),
                encoding="utf-8",
            )

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = wissenswerk.main(
                    ["--config", str(config_path), "ingest", "--from-ragprep", str(ragprep), "--apply", "--json"]
                )
            self.assertEqual(code, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["metadata_findings"], [])
            self.assertEqual(payload["source_documents_written"], 1)
            self.assertEqual(payload["evidence_segments_written"], 1)

    def test_analysis_extracts_metadata_claims_and_plans_small_corpus_sources(self):
        config = wissenswerk.default_config_payload()
        config["project"]["name"] = "Small Archive"
        config["project"]["type"] = "local-history"
        segments = [
            {
                "document_id": "doc-1",
                "segment_id": "doc-1:0001",
                "title": "Council Notes",
                "section": "Market Square",
                "text": "In 1899 the council recorded repairs near the square.",
                "summary": "Council notes describe repairs near the market square.",
                "entities": ["Council"],
                "keywords": ["repairs", "market square"],
                "source_url": "https://example.test/council-notes",
                "language": "en",
            }
        ]

        analysis = wissenswerk.build_corpus_analysis(config, segments, None)
        predicates = {claim["predicate"] for claim in analysis["claims"]}
        self.assertIn("summarizes", predicates)
        self.assertIn("has_section", predicates)
        self.assertIn("has_keyword", predicates)

        plan = wissenswerk.build_article_plan(config, analysis)
        candidates = plan["article_candidates"]
        by_title = {candidate["title"]: candidate for candidate in candidates}
        self.assertEqual(by_title["Market Square"]["planning_basis"], ["section_coverage"])
        self.assertEqual(by_title["Market Square"]["priority"], "B")
        self.assertEqual(by_title["Source: Council Notes"]["priority"], "B")
        self.assertEqual(by_title["Source: Council Notes"]["status"], "planned")

    def test_demo_release_pipeline_generates_analysis_plan_wiki_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self.temp_config(root)
            fixture = wissenswerk.REPO_ROOT / "tests" / "fixtures" / "ragprep"

            commands = [
                [
                    "--config",
                    str(config_path),
                    "setup",
                    "--quick",
                    "--profile",
                    "local-history",
                    "--project-name",
                    "Porreres Test",
                    "--from-ragprep",
                    str(fixture),
                    "--language",
                    "en",
                    "--json",
                ],
                ["--config", str(config_path), "ingest", "--from-ragprep", str(fixture), "--apply", "--json"],
                ["--config", str(config_path), "analyze", "--apply", "--json"],
                ["--config", str(config_path), "plan", "articles", "--apply", "--json"],
                ["--config", str(config_path), "build", "--apply", "--json"],
                ["--config", str(config_path), "audit", "--json"],
                ["--config", str(config_path), "stats", "--json"],
                ["--config", str(config_path), "demo", "report", "--json"],
                ["--config", str(config_path), "publish", "pages", "--dry-run", "--json"],
            ]
            for command in commands:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = wissenswerk.main(command)
                self.assertEqual(code, 0, out.getvalue())

            self.assertTrue((root / "profile" / "project_profile.json").exists())
            profile = json.loads((root / "profile" / "project_profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["corpus"]["preview"]["source_documents"], 1)
            self.assertEqual(profile["corpus"]["preview"]["evidence_segments"], 1)
            self.assertTrue((root / "corpus_state" / "source_documents.json").exists())
            self.assertTrue((root / "corpus_state" / "evidence_segments.json").exists())
            self.assertTrue((root / "corpus_state" / "import_manifest.json").exists())
            self.assertTrue((root / "analysis" / "claims.json").exists())
            claims = json.loads((root / "analysis" / "claims.json").read_text(encoding="utf-8"))["claims"]
            self.assertTrue(claims)
            self.assertIn("evidence_segment_id", claims[0])
            self.assertNotIn("chunk_id", claims[0])
            self.assertIn("summarizes", {claim["predicate"] for claim in claims})
            self.assertTrue((root / "analysis" / "graph.json").exists())
            self.assertTrue((root / "article_plans" / "article_plan.json").exists())
            self.assertTrue((root / "wiki" / "Articles").exists())
            self.assertTrue((root / "wiki" / "Articles" / "Source_Sample_RagPrep_Document.md").exists())
            self.assertTrue(list((root / "wiki" / "Articles").glob("*.provenance.json")))
            self.assertTrue((root / "reports" / "demo_summary.json").exists())

    def test_demo_run_orchestrates_pipeline_with_single_json_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self.temp_config(root)
            fixture = wissenswerk.REPO_ROOT / "tests" / "fixtures" / "ragprep"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = wissenswerk.main(
                    [
                        "--config",
                        str(config_path),
                        "demo",
                        "run",
                        "--from-ragprep",
                        str(fixture),
                        "--profile",
                        "local-history",
                        "--project-name",
                        "Porreres Test",
                        "--apply",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0, out.getvalue())
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["steps"]["build"], 0)

    def test_json_output_stays_clean_when_progress_goes_to_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self.temp_config(root)
            fixture = wissenswerk.REPO_ROOT / "tests" / "fixtures" / "ragprep"
            out = io.StringIO()
            err = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = wissenswerk.main(
                    ["--config", str(config_path), "ingest", "--from-ragprep", str(fixture), "--apply", "--json"]
                )
            self.assertEqual(code, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["status"], "ready")
            self.assertIn("[wissenswerk] ingest:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
