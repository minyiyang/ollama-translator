import json
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from book_agent.config import AppConfig
from book_agent.review_ui import ReviewSession
from book_agent.state import StageStatus, connect_state, set_stage_status
from book_agent.schemas import GlossaryCategory, GlossaryResult
from book_agent.stages.glossary import (
    run_glossary_approval_stage,
    run_glossary_extraction_stage,
    run_glossary_resolution_stage,
)
from book_agent.web import drafts
from book_agent.web import setup as setup_api
from book_agent.web.glossary_view import glossary_payload, write_reviewed_glossary
from book_agent.web.jobs import LogTracker, ProgressReader, job_path, list_jobs
from book_agent.web.server import UiApp, make_handler
from tests.test_glossary_stages import (
    FakeGlossaryClient,
    GlossaryStageTests,
    entry,
    resolution_result,
)

LOG = (
    "[2026-09-17T23:11:04-04:00] [stage=translate] [session=s] [running]: result=pending\n"
    "[2026-09-17T23:11:04-04:00] [stage=translate] [session=s] [role=translate.draft] "
    "[stage-plan=translate] [schedule] result=pending; prescreened=14; llm_tasks=14; skipped=0\n"
    "[2026-09-17T23:11:05-04:00] [stage=translate] [session=s] [role=r] "
    "[chunk=2/14 id=t attempt=1/4 mode=full] [llm] qwen3.8:latest: generation started\n"
    "[2026-09-17T23:11:17-04:00] [stage=translate] [session=s] [role=r] "
    "[chunk=2/14 id=t attempt=1/4 mode=full] [llm] qwen3.8:latest: completed, 1,946 characters, "
    "prompt 3,288 tokens, output 1,524 tokens, 130.4 tok/s, elapsed 13.4s\n"
    "[2026-09-17T23:12:00-04:00] [stage=audit_translation] [session=s] [role=a] "
    "[segment=187/875 id=D1 stage=audit_translation] [segment] result=passed\n"
)


def paused_glossary_workspace(base: Path):
    workspace = GlossaryStageTests().make_workspace(base)
    config = AppConfig.model_validate({"glossary": {"extraction_chunk_tokens": 100}})
    candidates = GlossaryResult(
        entries=[
            entry("Qelwright", "奎尔赖特", ["D0000-S000001"]),
            entry("Vraxwright", "弗拉克斯赖特", ["D0000-S000001"]),
        ]
    )
    run_glossary_extraction_stage(workspace, config, FakeGlossaryClient([candidates]))
    run_glossary_resolution_stage(
        workspace,
        config,
        FakeGlossaryClient(
            [
                resolution_result(
                    ("T00001", "奎尔赖特", GlossaryCategory.PERSON),
                    ("T00002", "弗拉克斯赖特", GlossaryCategory.PERSON),
                )
            ]
        ),
    )
    return workspace, config


class LogTrackerTests:
    def test_parses_plan_counters_and_llm_metrics_incrementally(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.log"
            lines = LOG.splitlines(keepends=True)
            path.write_text("".join(lines[:3]) + lines[3][:40], encoding="utf-8")
            tracker = LogTracker(path)
            tracker.advance()
            assert tracker.line_count == 3  # the partial line waits for its newline
            with path.open("a", encoding="utf-8") as handle:
                handle.write(lines[3][40:] + lines[4])
            tracker.advance()

            translate = tracker.stages["translate"]
            assert tracker.line_count == 5
            assert translate.tasks == 14
            assert translate.counter == (2, 14)
            assert translate.llm_calls == 1 and translate.output_tokens == 1524
            assert tracker.last_rate == 130.4
            assert tracker.stages["audit_translation"].segments == (187, 875)

    def test_snapshot_restarts_lines_when_a_new_session_log_appears(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = paused_glossary_workspace(Path(directory))
            logs = workspace.root / "logs"
            logs.mkdir(exist_ok=True)
            (logs / "20260101T000000-a-run.log").write_text(LOG, encoding="utf-8")
            reader = ProgressReader()
            first = reader.snapshot(workspace)
            assert first["progress"]["line_count"] == 5
            assert reader.snapshot(workspace, 5, first["progress"]["log"])["progress"]["lines"] == []

            (logs / "20260101T010000-b-resume.log").write_text(LOG[:200] + "\n", encoding="utf-8")
            switched = reader.snapshot(workspace, 5, first["progress"]["log"])["progress"]
            assert switched["log"].endswith("resume.log")
            assert len(switched["lines"]) == switched["line_count"] == 2


class SetupTests:
    def test_invalid_yaml_and_schema_errors_are_readable(self):
        with pytest.raises(ValueError, match="YAML syntax error"):
            setup_api.parse_config("translation: [1", Path("."))
        with pytest.raises(ValueError, match="ollama.temperature"):
            setup_api.parse_config("ollama: {temperature: nope}", Path("."))

    def test_validate_setup_reports_missing_source_existing_job_and_models(self):
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory) / "runs"
            (runs / "taken").mkdir(parents=True)
            with patch.object(setup_api, "installed_models", return_value=["gemma4:31b"]):
                result = setup_api.validate_setup(
                    "ollama: {model: qwen3.8:latest}", Path(directory), runs,
                    str(Path(directory) / "missing.epub"), "taken",
                )
            assert not result["ok"]
            text = " | ".join(result["problems"])
            assert "source file not found" in text
            assert "already exists" in text
            assert "qwen3.8:latest" in text
            assert {m["model"]: m["installed"] for m in result["models"]}["gemma4:31b"] is True

    def test_config_names_cannot_escape_the_config_directory(self):
        with pytest.raises(ValueError):
            setup_api.config_path(Path("."), "../secrets.yaml")
        with pytest.raises(ValueError):
            job_path(Path("runs"), "../elsewhere")


class GlossaryViewTests:
    def test_paused_gate_is_editable_and_reviewed_file_feeds_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, config = paused_glossary_workspace(Path(directory))
            payload = glossary_payload(workspace)
            assert payload["ready"] and payload["editable"]
            assert payload["approve_status"] == "paused"
            assert {e["english"] for e in payload["entries"]} == {"Qelwright", "Vraxwright"}
            assert "D0000-S000001" in payload["evidence"]

            reviewed = [dict(payload["entries"][0], chinese="奎尔莱特")]
            path = write_reviewed_glossary(workspace, reviewed)
            result = run_glossary_approval_stage(workspace, config, reviewed_file=path)

            assert [e.chinese for e in result.entries] == ["奎尔莱特"]
            after = glossary_payload(workspace)
            assert after["approve_status"] == "completed" and not after["editable"]

    def test_reviewed_glossary_is_schema_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = paused_glossary_workspace(Path(directory))
            entries = glossary_payload(workspace)["entries"]
            with pytest.raises(ValueError, match="empty"):
                write_reviewed_glossary(workspace, [])
            with pytest.raises(ValueError, match="CJK"):
                write_reviewed_glossary(workspace, [dict(entries[0], chinese="Qelwright!")])


class ServerTests:
    def serve(self, app: UiApp):
        port = [0]
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, port))
        port[0] = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port[0]}"

        def call(path, body=None, headers=None):
            request = urllib.request.Request(
                base + path,
                data=None if body is None else json.dumps(body).encode(),
                headers={"Content-Type": "application/json", **(headers or {})},
            )
            try:
                with urllib.request.urlopen(request) as response:
                    return response.status, response.read().decode("utf-8")
            except urllib.error.HTTPError as error:
                return error.code, error.read().decode("utf-8")

        call.base = base
        return server, call

    def test_pages_guards_and_glossary_approval_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = paused_glossary_workspace(Path(directory))
            app = UiApp(workspace.root.parent, Path(directory) / "configs", [])
            server, call = self.serve(app)
            token = {"X-UI-Token": app.token}
            try:
                status, page = call("/jobs/fixture/glossary")
                assert status == 200 and '<div id="root">' in page
                asset = re.search(r'src="/assets/([^"]+)"', page)[1]
                assert call(f"/assets/{asset}")[0] == 200
                assert call("/assets/..%2Fserver.py")[0] == 404
                assert json.loads(call("/api/session")[1])["token"] == app.token
                assert call("/api/setup", headers={"Host": "evil.example:80"})[0] == 403
                assert call("/api/jobs/fixture/resume", {})[0] == 403  # no token
                assert call("/api/jobs/..%2Fx/progress")[0] in {404, 422}

                status, body = call("/api/jobs/fixture/glossary")
                assert status == 200 and json.loads(body)["editable"]
                assert [j["job_id"] for j in list_jobs(app.runs)] == ["fixture"]

                entries = json.loads(body)["entries"]
                with patch.object(UiApp, "launch", return_value={"running": True}) as launch:
                    status, _ = call(
                        "/api/jobs/fixture/glossary/approve",
                        {"entries": entries, "llm": True},
                        token,
                    )
                assert status == 200
                args = launch.call_args.args[1]
                assert args[0] == "approve" and "--llm-glossary" in args and "--resume" in args
                assert Path(args[args.index("--glossary") + 1]).is_file()

                status, body = call("/api/jobs/fixture/glossary/approve", {}, token)
                assert status == 422 and "submit reviewed entries" in json.loads(body)["error"]
            finally:
                server.shutdown()
                server.server_close()

    def test_draft_lifecycle_gates_start_on_current_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "configs").mkdir()
            template = base / "config.example.yaml"
            template.write_text("translation: {direction: en-zh}\n", encoding="utf-8")
            source = base / "My Book.epub"
            source.write_bytes(b"epub")
            app = UiApp(base / "runs", base / "configs", [], template)

            assert drafts.suggest_names(str(source)) == {"config": "my-book.yaml", "job_id": "my-book"}
            created = app.create_job({"source": str(source), "config": "my-book.yaml", "job_id": ""})
            assert created["job_id"] == "my-book" and created["created_config"]
            assert (base / "configs" / "my-book.yaml").read_text(encoding="utf-8") == template.read_text(encoding="utf-8")
            with pytest.raises(ValueError, match="already exists"):
                app.create_job({"source": str(source), "config": "my-book.yaml", "job_id": ""})

            info = app.job_info("my-book")
            assert (info["kind"], info["overall"], info["validated"]) == ("draft", "draft", False)
            assert [j["overall"] for j in list_jobs(app.runs)] == ["draft"]
            assert app.job_config("my-book")["editable"]

            with patch.object(UiApp, "launch") as launch, pytest.raises(ValueError, match="validate"):
                app.start_job("my-book")
            launch.assert_not_called()

            with patch.object(setup_api, "installed_models", return_value=["qwen3.8:latest", "gemma4:31b", "gemma4:26b"]):
                check = app.validate_job("my-book", {"text": "ollama: {model: qwen3.8:latest}\nglossary: {extraction_model: qwen3.8:latest}\n"})
            assert check["validated"], check["problems"]
            assert app.job_info("my-book")["validated"]

            # Any later edit to the file invalidates the validation.
            (base / "configs" / "my-book.yaml").write_text("ollama: {temperature: 0.5}\n", encoding="utf-8")
            assert not app.job_info("my-book")["validated"]
            with patch.object(setup_api, "installed_models", return_value=["qwen3.8:latest", "gemma4:31b", "gemma4:26b"]):
                app.validate_job("my-book", {"text": "ollama: {model: qwen3.8:latest}\nglossary: {extraction_model: qwen3.8:latest}\n"})
            with patch.object(UiApp, "launch", return_value={"running": True}) as launch:
                app.start_job("my-book")
            args = launch.call_args.args[1]
            assert args[:2] == ["run", str(source.resolve())] and args[args.index("--job-id") + 1] == "my-book"
            assert drafts.load_draft(app.runs, "my-book")["launched"]

    def test_existing_config_is_reused_and_drafts_can_be_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "configs").mkdir()
            (base / "configs" / "shared.yaml").write_text("ollama: {temperature: 0.1}\n", encoding="utf-8")
            source = base / "book.rtf"
            source.write_bytes(b"{\\rtf1}")
            app = UiApp(base / "runs", base / "configs", [], None)
            created = app.create_job({"source": str(source), "config": "shared.yaml", "job_id": "a"})
            assert not created["created_config"]
            assert "0.1" in app.job_config("a")["text"]
            assert app.discard_draft("a") == {"discarded": True}
            assert list_jobs(app.runs) == []
            with pytest.raises(ValueError, match="source must be"):
                app.create_job({"source": str(base / "configs" / "shared.yaml"), "config": "x.yaml", "job_id": "x"})

    def test_upload_stores_book_without_overwriting_a_different_file(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = paused_glossary_workspace(Path(directory))
            app = UiApp(workspace.root.parent, Path(directory) / "configs", [])
            server, call = self.serve(app)
            try:
                def upload(name, data, token=app.token):
                    request = urllib.request.Request(
                        f"{call.base}/api/uploads?name={urllib.parse.quote(name)}",
                        data=data,
                        headers={"X-UI-Token": token},
                    )
                    try:
                        with urllib.request.urlopen(request) as response:
                            return response.status, json.loads(response.read())
                    except urllib.error.HTTPError as error:
                        return error.code, json.loads(error.read())

                assert upload("book.epub", b"one", token="wrong")[0] == 403
                status, first = upload("book.epub", b"one")
                assert status == 200 and Path(first["path"]).read_bytes() == b"one"
                assert upload("book.epub", b"one")[1]["path"] == first["path"]
                second = upload("book.epub", b"two")[1]["path"]
                assert second != first["path"] and Path(second).read_bytes() == b"two"
                assert upload("notes.txt", b"x")[0] == 422
                assert upload("../evil.epub", b"x")[1]["path"].endswith("evil.epub")  # name only, no traversal
            finally:
                server.shutdown()
                server.server_close()

    def test_stop_requires_a_dashboard_launched_run(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = paused_glossary_workspace(Path(directory))
            app = UiApp(workspace.root.parent, Path(directory), [])
            with pytest.raises(ValueError, match="use Pause"):
                app.stop_job("fixture")
            info = app.job_info("fixture")
            assert info["kind"] == "job" and not info["can_stop"]
            app.pause_job("fixture")
            assert app.job_info("fixture")["pause_requested"]


def test_launch_runs_cli_and_reports_failure_tail():
    with tempfile.TemporaryDirectory() as directory:
        app = UiApp(Path(directory) / "runs", Path(directory), [])
        state = app.launch("nojob", ["resume", str(Path(directory) / "missing")], "resume")
        assert state["label"] == "resume"
        app._processes["nojob"]["process"].wait(timeout=60)
        final = app.process_state("nojob")
        assert final["exit_code"] == 1 and final["outcome"] == "failed" and "error" in final["output_tail"]

        # A usage error also exits 2 like "paused", but it is a failure.
        app.launch("usage", ["status", str(Path(directory) / "missing")], "status")
        app._processes["usage"]["process"].wait(timeout=60)
        assert app.process_state("usage")["outcome"] == "failed"


class ConfigApiTests:
    def test_schema_covers_every_section_with_defaults(self):
        sections = {s["key"]: s for s in setup_api.config_schema()["sections"]}
        assert set(sections) >= {"ollama", "translation", "glossary", "audit", "workflow"}
        fields = {f["path"]: f for s in sections.values() for f in s["fields"]}
        assert fields["translation.style"]["type"] == "enum"
        assert "literary" in fields["translation.style"]["enum"]
        assert fields["audit.quantity.enabled"]["type"] == "boolean"  # nested model flattened
        assert fields["translation.custom_style_file"]["nullable"]
        assert fields["ollama.temperature"]["maximum"] == 2.0
        assert fields["glossary.seed_glossaries"]["item_type"] == "string"

    def test_parse_dump_round_trip_and_field_errors(self):
        values = setup_api.parse_values("ollama:\n  model: qwen3.8:latest\nworkflow: {llm_glossary_review: true}\n")
        assert setup_api.parse_values(setup_api.dump_values(values)) == values
        errors = setup_api.check_config("ollama: {temperature: 5}\n", Path("."))
        assert [e["path"] for e in errors] == ["ollama.temperature"]
        assert setup_api.check_config("translation: [1", Path("."))[0]["path"] == ""
        assert setup_api.check_config("", Path(".")) == []


def test_failed_start_returns_the_draft_to_draft():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        (base / "configs").mkdir()
        source = base / "book.epub"
        source.write_bytes(b"epub")
        app = UiApp(base / "runs", base / "configs", [], None)
        app.create_job({"source": str(source), "config": "b.yaml", "job_id": "b"})
        draft = drafts.load_draft(app.runs, "b")
        draft["launched"] = "2026-01-01T00:00:00+00:00"  # started, but no workspace appeared
        drafts.save_draft(app.runs, draft)
        assert app.job_info("b")["overall"] == "draft"
        assert not drafts.load_draft(app.runs, "b")["launched"]


def _epub(path: Path, *, cover_item: str, cover_member: str, cover_bytes: bytes) -> Path:
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>Qelwright Tales</dc:title><dc:creator>A. Author</dc:creator><dc:language>en</dc:language>"
            '<meta name="cover" content="cover-img"/></metadata><manifest>'
            f"{cover_item}</manifest></package>",
        )
        archive.writestr(cover_member, cover_bytes)
    return path


class BookInfoTests:
    def test_reads_title_author_and_epub2_cover(self):
        from book_agent.web.book_info import allowed_book, book_cover, book_info

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            book = _epub(root / "tales.epub", cover_item='<item id="cover-img" href="images/c.jpg" media-type="image/jpeg"/>',
                         cover_member="OEBPS/images/c.jpg", cover_bytes=b"\xff\xd8jpeg")
            info = book_info(allowed_book(str(book), [root]))
            assert (info["title"], info["authors"], info["language"], info["has_cover"]) == ("Qelwright Tales", ["A. Author"], "en", True)
            assert book_cover(book) == (b"\xff\xd8jpeg", "image/jpeg")
            with pytest.raises(ValueError, match="unknown book"):
                allowed_book(str(book), [root / "elsewhere"])

    def test_svg_cover_is_never_served(self):
        from book_agent.web.book_info import book_cover, book_info

        with tempfile.TemporaryDirectory() as directory:
            book = _epub(Path(directory) / "svg.epub", cover_item='<item id="cover-img" href="c.svg" media-type="image/svg+xml"/>',
                         cover_member="OEBPS/c.svg", cover_bytes=b"<svg onload='alert(1)'/>")
            assert not book_info(book)["has_cover"]
            with pytest.raises(ValueError, match="raster"):
                book_cover(book)


class RequestRobustnessTests:
    """Malformed requests get a JSON error response; the server never drops the connection."""

    serve = ServerTests.serve  # reuse the helper without re-running ServerTests

    def _raw(self, base, path, data, headers):
        request = urllib.request.Request(base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_binary_or_non_object_bodies_are_rejected_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = paused_glossary_workspace(Path(directory))
            app = UiApp(workspace.root.parent, Path(directory) / "configs", [])
            server, call = self.serve(app)
            headers = {"X-UI-Token": app.token, "Content-Type": "application/json"}
            try:
                # The reported failure: EPUB bytes (not UTF-8) reaching a JSON endpoint.
                binary = b"PK\x03\x04\x14\x00\x00\x00\xfa\xb1\xab" + bytes(range(128, 256))
                status, body = self._raw(call.base, "/api/jobs/fixture/start", binary, headers)
                assert status == 400 and "UTF-8 JSON" in body["error"]
                status, body = self._raw(call.base, "/api/jobs/new", b"{not json", headers)
                assert status == 400
                status, body = self._raw(call.base, "/api/jobs/new", b"[1, 2]", headers)
                assert status == 400 and "JSON object" in body["error"]
                # Still serving afterwards.
                assert call("/api/setup")[0] == 200
            finally:
                server.shutdown()
                server.server_close()

    def test_binary_epub_upload_uses_the_upload_route(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = paused_glossary_workspace(Path(directory))
            app = UiApp(workspace.root.parent, Path(directory) / "configs", [])
            server, call = self.serve(app)
            try:
                epub_bytes = b"PK\x03\x04\xfa\xb1\xab" * 100
                status, body = self._raw(
                    call.base, "/api/uploads?name=book.epub", epub_bytes, {"X-UI-Token": app.token}
                )
                assert status == 200 and Path(body["path"]).read_bytes() == epub_bytes
            finally:
                server.shutdown()
                server.server_close()

    def test_unexpected_errors_return_json_500(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = paused_glossary_workspace(Path(directory))
            app = UiApp(workspace.root.parent, Path(directory) / "configs", [])
            server, call = self.serve(app)
            try:
                path = drafts.store_upload(app.runs, "book.epub", __import__("io").BytesIO(b"x"), 1)
                with patch("book_agent.web.server.book_cover", side_effect=RuntimeError("boom")):
                    status, body = call(f"/api/book/cover?path={urllib.parse.quote(str(path))}")
                assert status == 500 and "boom" in json.loads(body)["error"]
                assert call("/api/setup")[0] == 200
            finally:
                server.shutdown()
                server.server_close()


class YamlEditorFeedbackTests:
    def test_syntax_errors_carry_line_column_and_context(self):
        result = setup_api.parse_for_editor("ollama:\n  model: [unclosed\n  num_ctx: 1\n")
        assert result["values"] is None
        error = result["syntax_error"]
        assert (error["line"], error["context_line"]) == (3, 2)
        assert error["column"] and "flow sequence" in error["message"]

    def test_non_mapping_root_and_valid_yaml(self):
        assert setup_api.parse_for_editor("- a\n- b\n")["syntax_error"]["line"] == 1
        assert setup_api.parse_for_editor("ollama: {model: x}\n") == {"values": {"ollama": {"model": "x"}}, "syntax_error": None}
        # Only syntax is judged here: a wrong value type still parses.
        assert setup_api.parse_for_editor("ollama: {temperature: nope}\n")["syntax_error"] is None


def test_book_endpoints_accept_job_sources_but_only_books():
    from book_agent.web.book_info import allowed_book

    with tempfile.TemporaryDirectory() as directory:
        workspace, _ = paused_glossary_workspace(Path(directory))
        app = UiApp(workspace.root.parent, Path(directory) / "configs", [])
        source = app.job_info("fixture")["source_path"]
        assert Path(source) == workspace.source_file
        assert allowed_book(source, app.book_roots()) == workspace.source_file.resolve()
        with pytest.raises(ValueError, match="unknown book"):
            allowed_book(str(workspace.state_file), app.book_roots())  # inside runs, but not a book


class EstimateTests:
    def test_running_stage_uses_its_own_pace(self):
        from book_agent.web.estimate import _current_stage_estimate
        from book_agent.web.jobs import StageActivity

        activity = StageActivity(counter=(5, 14), first="2026-01-01T00:00:00+00:00", unit_started="2026-01-01T00:04:00+00:00")
        result = _current_stage_estimate("translate", activity, "2026-01-01T00:04:30+00:00")
        # 4 units in 240 s -> 60 s/unit; 10 units left, 30 s already into the current one.
        assert (result["done"], result["seconds_per_unit"], result["remaining_seconds"]) == (4, 60.0, 570)

    def test_first_unit_prefers_history_then_call_time(self):
        from book_agent.web.estimate import _current_stage_estimate
        from book_agent.web.jobs import StageActivity

        fresh = StageActivity(counter=(1, 6), first="2026-01-01T00:00:00+00:00", llm_calls=2, call_seconds=24.0)
        assert _current_stage_estimate("x", fresh, "2026-01-01T00:00:10+00:00", history_seconds=300)["remaining_seconds"] == 300
        assert _current_stage_estimate("x", fresh, "2026-01-01T00:00:10+00:00")["remaining_seconds"] == 72

    def test_elapsed_call_times_are_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.log"
            path.write_text(
                LOG.replace("elapsed 13.4s", "elapsed 1.5m"),
                encoding="utf-8",
            )
            tracker = LogTracker(path)
            tracker.advance()
            assert tracker.stages["translate"].call_seconds == 90.0

    def test_history_scales_by_segments_and_skips_gates_and_disabled_stages(self):
        from book_agent.web import estimate as est

        with tempfile.TemporaryDirectory() as directory:
            workspace, config = paused_glossary_workspace(Path(directory))
            human = config.model_copy(update={"workflow": config.workflow.model_copy(update={"require_glossary_review": True, "llm_glossary_review": False})})
            with patch.object(est, "history_rates", return_value=({"translate": 0.5, "audit_translation": 2.0}, 2)), \
                 patch.object(est, "segment_count", return_value=100):
                result = est.estimate(workspace, workspace.root.parent, human)
            by_stage = {p["stage"]: p["seconds"] for p in result["pending"]}
            assert by_stage == {"translate": 50, "audit_translation": 200}
            assert "approve_glossary" in result["excludes"]
            assert "reprose_translation" not in result["unknown_stages"]  # disabled by default
            assert result["remaining_seconds"] == 250 and result["history_jobs"] == 2


def test_paused_and_stopped_exits_are_not_reported_as_failures():
    with tempfile.TemporaryDirectory() as directory:
        app = UiApp(Path(directory) / "runs", Path(directory), [])
        (Path(directory) / "runs").mkdir()
        for code, outcome in ((0, "completed"), (2, "paused"), (130, "cancelled"), (1, "failed")):
            output = Path(directory) / f"{code}.out"
            output.write_text("last line of output\n", encoding="utf-8")

            class Done:
                def poll(self, code=code):
                    return code

            app._processes["job"] = {"process": Done(), "label": "run", "output": output, "started": "now"}
            state = app.process_state("job")
            assert state["outcome"] == outcome
            # Output is surfaced only for real failures (the reported bug showed it for exit 2).
            assert bool(state["output_tail"]) == (outcome == "failed")


def test_glossary_payload_labels_categories_in_english_but_keeps_values():
    from book_agent.schemas import GlossaryCategory

    with tempfile.TemporaryDirectory() as directory:
        workspace, _ = paused_glossary_workspace(Path(directory))
        payload = glossary_payload(workspace)
        assert payload["categories"] == [c.value for c in GlossaryCategory]  # what gets saved
        assert payload["category_labels"][GlossaryCategory.PERSON.value] == "Person"
        assert set(payload["category_labels"].values()) == {
            "Person", "Place", "Organization", "Item", "Technology", "Concept", "Term", "Other",
        }
        assert payload["other_category"] == GlossaryCategory.OTHER.value


def test_glossary_is_locked_while_an_approval_runs():
    with tempfile.TemporaryDirectory() as directory:
        workspace, _ = paused_glossary_workspace(Path(directory))
        app = UiApp(workspace.root.parent, Path(directory), [])
        assert app.glossary_state("fixture")["editable"]
        output = Path(directory) / "approve.out"
        output.write_text("", encoding="utf-8")

        class Proc:
            code = None

            def poll(self):
                return self.code

        proc = Proc()
        app._processes["fixture"] = {"process": proc, "label": "glossary approval", "output": output, "started": "now"}
        state = app.glossary_state("fixture")
        assert state["approval_running"] and not state["editable"]

        proc.code = 1  # the approval failed: editable again, with the failure reported
        state = app.glossary_state("fixture")
        assert not state["approval_running"] and state["editable"]
        assert state["process"]["outcome"] == "failed"


class RerunTests:
    def test_preview_lists_downstream_stages_and_glossary_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = paused_glossary_workspace(Path(directory))
            app = UiApp(workspace.root.parent, Path(directory), [])
            preview = app.rerun_preview("fixture", "resolve_glossary")
            names = [item["name"] for item in preview["stages"]]
            assert names[:3] == ["resolve_glossary", "approve_glossary", "preprocess"]
            assert names[-1] == "validate_epub"
            codes = {item["code"] for item in preview["warnings"]}
            assert codes == {"glossary_approval", "full_translation"}

    def test_review_gates_pending_and_unknown_stages_cannot_be_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = paused_glossary_workspace(Path(directory))
            app = UiApp(workspace.root.parent, Path(directory), [])
            with pytest.raises(ValueError, match="waiting for your review"):
                app.rerun_preview("fixture", "approve_glossary")  # paused at the glossary gate
            with pytest.raises(ValueError, match="is pending"):
                app.rerun_preview("fixture", "translate")
            with pytest.raises(ValueError, match="unknown workflow stage"):
                app.rerun_preview("fixture", "nope")
            with patch.object(UiApp, "launch") as launch, pytest.raises(ValueError, match="waiting for your review"):
                app.rerun_job("fixture", {"stage": "approve_glossary"})
            launch.assert_not_called()

    @pytest.mark.parametrize("status", [StageStatus.FAILED, StageStatus.PAUSED])
    def test_failed_or_interrupted_stage_can_be_rerun(self, status):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = paused_glossary_workspace(Path(directory))
            connection = connect_state(workspace.state_file)
            try:
                set_stage_status(connection, "resolve_glossary", status, message="interrupted")
            finally:
                connection.close()
            app = UiApp(workspace.root.parent, Path(directory), [])
            preview = app.rerun_preview("fixture", "resolve_glossary")
            assert preview["status"] == status.value
            assert preview["stages"][0]["name"] == "resolve_glossary"
            with patch.object(UiApp, "launch", return_value={"running": True}) as launch:
                app.rerun_job("fixture", {"stage": "resolve_glossary"})
            assert launch.call_args.args[1][:1] == ["retry"]

    def test_rerun_launches_retry_with_resume_and_refuses_while_running(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = paused_glossary_workspace(Path(directory))
            app = UiApp(workspace.root.parent, Path(directory), [])
            with patch.object(UiApp, "launch", return_value={"running": True}) as launch:
                app.rerun_job("fixture", {"stage": "resolve_glossary"})
            args, label = launch.call_args.args[1], launch.call_args.args[2]
            assert args[0] == "retry" and label == "rerun"
            assert args[args.index("--stage") + 1] == "resolve_glossary" and "--resume" in args

            running = {**app.job_info("fixture"), "running": True}
            with patch.object(UiApp, "job_info", return_value=running), patch.object(UiApp, "launch") as launch:
                with pytest.raises(ValueError, match="pause or stop"):
                    app.rerun_job("fixture", {"stage": "resolve_glossary"})
            launch.assert_not_called()

    def test_manual_review_warning_needs_saved_review_work(self):
        from tests.test_review_ui import paused_workspace

        with tempfile.TemporaryDirectory() as directory:
            workspace = paused_workspace(directory)
            job = workspace.root.name
            app = UiApp(workspace.root.parent, Path(directory), [])
            codes = {w["code"] for w in app.rerun_preview(job, "validate_repaired")["warnings"]}
            assert "manual_review" not in codes
            session = app.review(job)
            session.save(session.payload()["worksheet"])  # saved review work exists now
            codes = {w["code"] for w in app.rerun_preview(job, "validate_repaired")["warnings"]}
            assert "manual_review" in codes and "full_translation" not in codes


class EndpointCoverageTests(ServerTests):
    """Every API route answers over HTTP, and no route is left without such a test."""

    def test_job_review_and_config_routes_over_http(self):
        from tests.test_review_ui import paused_workspace

        with tempfile.TemporaryDirectory() as directory:
            workspace = paused_workspace(directory)
            job = workspace.root.name
            configs = Path(directory) / "configs"
            configs.mkdir()
            app = UiApp(workspace.root.parent, configs, [])
            server, call = self.serve(app)
            token = {"X-UI-Token": app.token}

            def get(path):
                status, text = call(path)
                return status, json.loads(text)

            def post(path, body):
                status, text = call(path, body, token)
                return status, json.loads(text)

            try:
                status, progress = get(f"/api/jobs/{job}/progress?after=0")
                assert status == 200 and "progress" in progress and "status" in progress
                status, estimate = get(f"/api/jobs/{job}/estimate")
                assert status == 200 and "pending" in estimate
                assert get(f"/api/jobs/{job}/info")[1]["kind"] == "job"
                assert get(f"/api/jobs/{job}/config")[0] == 200

                status, review = get(f"/api/jobs/{job}/review")
                assert status == 200 and review["compile_limit"] == 0
                worksheet = review["worksheet"]
                assert post(f"/api/jobs/{job}/review/save", {"worksheet": worksheet}) == (200, {"saved": True})
                segment = worksheet["resolutions"][0]["segment_id"]
                status, check = post(f"/api/jobs/{job}/review/check", {"segment_id": segment, "decision": "accept"})
                assert status == 200 and check["blocking"] == []
                status, applied = post(f"/api/jobs/{job}/review/apply", {"worksheet": worksheet, "partial": True})
                assert status == 422 and "compile limit" in applied["error"]
                assert get(f"/api/jobs/{job}/review/compile")[1]["state"] == "idle"
                with patch.object(ReviewSession, "start_compile", return_value={"state": "running"}):
                    assert post(f"/api/jobs/{job}/review/compile", {})[1] == {"state": "running"}

                status, preview = get(f"/api/jobs/{job}/rerun?stage=validate_repaired")
                assert status == 200 and preview["stages"][0]["name"] == "validate_repaired"
                with patch.object(UiApp, "launch", return_value={"running": True}):
                    assert post(f"/api/jobs/{job}/rerun", {"stage": "validate_repaired"})[0] == 200
                with patch.object(UiApp, "launch") as launch:
                    assert post(f"/api/jobs/{job}/rerun", {"stage": "validate_epub"})[0] == 422  # not run yet
                launch.assert_not_called()

                text = "ollama: {temperature: 0.2}\n"
                status, saved = post("/api/config/save", {"name": "mine.yaml", "text": text})
                assert status == 200 and Path(saved["path"]).read_text(encoding="utf-8") == text
                assert get("/api/config?name=mine.yaml")[1]["text"] == text
                assert get("/api/config?name=..%2Fescape.yaml")[0] == 422
                assert get("/api/config/schema")[0] == 200
                assert post("/api/config/parse", {"text": text})[0] == 200
                assert post("/api/config/dump", {"values": {"ollama": {"temperature": 0.2}}})[0] == 200
                assert post("/api/config/check", {"text": "ollama: [1"})[1]["errors"]
                with patch.object(setup_api, "installed_models", return_value=["gemma4:26b"]):
                    assert post("/api/config/validate", {"text": text})[0] == 200
                    assert get("/api/models")[1] == {"installed": ["gemma4:26b"]}
                assert get("/api/jobs/suggest?source=My%20Book.epub")[1]["job_id"] == "my-book"
                assert get("/api/book?path=missing.epub")[0] == 422
            finally:
                server.shutdown()
                server.server_close()

    def test_draft_and_run_control_routes_over_http(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = paused_glossary_workspace(Path(directory))
            base = Path(directory)
            (base / "configs").mkdir()
            source = base / "book.epub"
            source.write_bytes(b"epub")
            app = UiApp(workspace.root.parent, base / "configs", [], None)
            server, call = self.serve(app)
            token = {"X-UI-Token": app.token}

            def post(path, body):
                status, text = call(path, body, token)
                return status, json.loads(text)

            try:
                assert post("/api/jobs/new", {"source": str(source), "config": "d.yaml", "job_id": "d"})[0] == 200
                models = ["qwen3.8:latest", "gemma4:31b", "gemma4:26b"]
                with patch.object(setup_api, "installed_models", return_value=models):
                    status, check = post("/api/jobs/d/validate", {"text": "ollama: {model: qwen3.8:latest}\n"})
                assert status == 200 and "validated" in check
                assert post("/api/jobs/d/discard", {}) == (200, {"discarded": True})

                assert post("/api/jobs/fixture/pause", {}) == (200, {"pause_requested": True})
                status, stopped = post("/api/jobs/fixture/stop", {})
                assert status == 422 and "use Pause" in stopped["error"]
            finally:
                server.shutdown()
                server.server_close()

    def test_every_route_is_exercised_over_http(self):
        from book_agent.web import server as server_module

        source = Path(server_module.__file__).read_text(encoding="utf-8")
        routes = set(re.findall(r'\("(GET|POST)", "([^"]+)"\)', source))
        tests = "".join(path.read_text(encoding="utf-8") for path in Path(__file__).parent.glob("test_*.py"))
        missing = sorted(
            f"{method} {route}"
            for method, route in routes
            if not re.search(rf'/api/(jobs/[^/"\s]+/)?{re.escape(route)}(?=["?])', tests)
        )
        assert missing == [], "API routes without an HTTP test: " + ", ".join(missing)
