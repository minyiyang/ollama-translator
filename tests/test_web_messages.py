"""The dashboard's coded messages: what the server sends, and that the interface catalog has each."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from book_agent.web.messages import (
    MESSAGES,
    RERUN_WARNINGS,
    STAGE_MESSAGES,
    UserError,
    error_payload,
)

REPO = Path(__file__).resolve().parents[1]
CATALOG = json.loads((REPO / "frontend" / "src" / "i18n" / "en.json").read_text(encoding="utf-8"))

# Each family of codes and the catalog prefix the dashboard looks it up under.
FAMILIES = {
    "server.error.": MESSAGES,
    "server.rerun.": RERUN_WARNINGS,
    "server.stage.": STAGE_MESSAGES,
}


class UserErrorTests:
    def test_reads_as_its_english_text_and_is_a_value_error(self):
        error = UserError("job_exists", job="a1")
        assert str(error) == "a job named a1 already exists"
        assert isinstance(error, ValueError)

    def test_payload_carries_the_code_and_values_beside_the_text(self):
        assert error_payload(UserError("series_no_version", series="qel", version=3)) == {
            "error": "series qel has no version 3",
            "code": "series_no_version",
            "params": {"series": "qel", "version": "3"},
        }

    def test_payload_of_any_other_error_is_its_text_alone(self):
        assert error_payload(ValueError("boom")) == {"error": "boom"}

    def test_refuses_a_code_it_does_not_know(self):
        with pytest.raises(KeyError):
            UserError("no_such_code")


class CatalogTests:
    @pytest.mark.parametrize("prefix", FAMILIES)
    def test_english_catalog_has_every_code_with_the_servers_text(self, prefix):
        # The English text is written twice, here and in the catalog; they must not drift.
        wanted = {prefix + code: text for code, text in FAMILIES[prefix].items()}
        found = {key: CATALOG.get(key) for key in wanted}
        assert found == wanted

    @pytest.mark.parametrize("prefix", FAMILIES)
    def test_catalog_has_no_code_the_server_never_sends(self, prefix):
        known = {prefix + code for code in FAMILIES[prefix]}
        assert {key for key in CATALOG if key.startswith(prefix)} - known == set()

    def test_no_text_holds_what_the_message_format_reads_as_syntax(self):
        # The same text is a Python format string here and an ICU message in the catalog.
        for family in FAMILIES.values():
            for code, text in family.items():
                assert not re.search(r"[<>'#]", re.sub(r"\{\w+\}", "", text)), code
                assert "{" not in re.sub(r"\{\w+\}", "", text), code


class StageMessageTests:
    """The dashboard knows these by their English text, so the stages must still write it."""

    @pytest.mark.parametrize(
        ("code", "sources"),
        [
            ("paused_on_request", ["workflow.py"]),
            ("review_required", ["stages/repair.py", "stages/repair_review.py", "stages/validate_repaired.py"]),
            ("glossary_review_required", ["stages/glossary.py"]),
            ("style_sheet_review_required", ["stages/glossary.py"]),
        ],
    )
    def test_stage_still_writes_the_message(self, code, sources):
        literal = re.sub(r"^\{\w+\}", "", STAGE_MESSAGES[code])
        for source in sources:
            assert literal in (REPO / "book_agent" / source).read_text(encoding="utf-8"), source


class RefusalTests:
    """Refusals no other test reaches: each is raised with its code and reads as its English text."""

    @pytest.fixture
    def folder(self):
        with tempfile.TemporaryDirectory() as directory:
            yield Path(directory)

    def app(self, directory):
        from book_agent.web.server import UiApp

        base = Path(directory)
        (base / "configs").mkdir()
        return UiApp(base / "runs", base / "configs", [])

    def refused(self, code, call, **params):
        with pytest.raises(UserError) as raised:
            call()
        assert (raised.value.code, raised.value.params) == (code, {name: str(value) for name, value in params.items()})
        assert str(raised.value) == MESSAGES[code].format(**params)

    def test_a_job_the_server_does_not_have(self, folder):
        app = self.app(folder)
        self.refused("job_unknown", lambda: app.job_info("nope"), job="nope")
        self.refused("job_unknown", lambda: app.job_config("nope"), job="nope")

    def test_what_only_an_unstarted_job_allows(self, folder):
        app = self.app(folder)
        self.refused("job_already_started", lambda: app.start_job("nope"))
        self.refused("validate_started_job", lambda: app.validate_job("nope", {"text": ""}))
        self.refused("discard_started_job", lambda: app.discard_draft("nope"))

    def test_a_source_file_that_is_not_there(self, folder):
        app = self.app(folder)
        missing = folder / "gone.epub"
        self.refused("source_not_found", lambda: app.create_job({"source": str(missing), "config": "gone.yaml", "job_id": ""}), path=missing)

    def test_a_second_command_while_one_runs(self, folder):
        app = self.app(folder)

        class Running:
            def poll(self):
                return None

        app._processes["a1"] = {"process": Running(), "label": "resume"}
        self.refused("already_running", lambda: app.launch("a1", ["resume"], "rerun"), label="resume")

    def test_an_upload_that_stops_early(self, folder):
        import io

        from book_agent.web.drafts import store_upload

        self.refused("upload_incomplete", lambda: store_upload(folder, "a.epub", io.BytesIO(b"abc"), 10))

    def test_an_empty_upload_over_http(self, folder):
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer

        from book_agent.web.server import make_handler

        app = self.app(folder)
        port = [0]
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, port))
        port[0] = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{port[0]}/api/uploads?name=a.epub", data=b"", method="POST", headers={"X-UI-Token": app.token}
            )
            with pytest.raises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=30)
            assert raised.value.code == 413
            assert json.loads(raised.value.read()) == {"error": MESSAGES["upload_size"], "code": "upload_size", "params": {}}
        finally:
            server.shutdown()
            server.server_close()
