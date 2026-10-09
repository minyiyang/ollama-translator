"""The dashboard's coded messages: what the server sends, and that the interface catalog has each."""

from __future__ import annotations

import json
import re
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
