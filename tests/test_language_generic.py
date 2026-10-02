"""Any language pair at the generic tier (docs/GENERIC_LANGUAGES.md, phase 2).

A language without a written profile gets a generic one from its code; checks
its profile cannot support are skipped and listed, never run with English or
Chinese rules; a pair other than en-zh/zh-en translates end to end.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from zipfile import ZipFile

import pytest

from book_agent.audit import _audit_language
from book_agent.config import AppConfig, AuditConfig, TranslationConfig
from book_agent.languages import (
    Language,
    LanguagePair,
    TranslationDirection,
    glossary_supported,
    language_support,
    leftover_scripts,
    profile,
    scripts_disjoint,
)
from book_agent.ollama_client import StructuredOutputError
from book_agent.pipeline_state import WorkflowStage
from book_agent.quantities import QuantityAuditResult
from book_agent.style_sheet import extraction_instructions
from book_agent.translation import _build_marker_examples
from book_agent.workflow import (
    approve_final_draft,
    approve_glossary,
    default_stage_runners,
    run_workflow,
    workflow_status,
)
from book_agent.workspace import create_job_workspace
from tests.epub_fixture import make_epub
from tests.test_audit_stage import FakeAuditClient
from tests.test_repair_stage import FakeRepairClient
from tests.test_translate_stage import FakeTranslationClient
from tests.test_validate_repaired_stage import FakeVerificationClient

EN_CHAPTER = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Fixture</title></head>
<body><h1>Chapter One</h1><p>Aster opened the <em>small</em> door.</p>
<p>&#8220;Off with her head!&#8221; the Queen shouted at Aster.</p></body></html>"""


# -- codes, profiles, pairs -------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "stored", "name", "tier", "scripts", "spaced"),
    [
        ("en", "en", "English", "tuned", ("Latin",), True),
        ("zh-Hans", "zh", "Simplified Chinese", "tuned", ("Han",), False),
        ("ja", "ja", "Japanese", "generic", ("Han", "Hiragana", "Katakana"), False),
        ("de", "de", "German", "generic", ("Latin",), True),
        ("pt-br", "pt-BR", "Portuguese (BR)", "generic", ("Latin",), True),
        ("zh-TW", "zh-TW", "Chinese (Traditional, TW)", "generic", ("Han",), False),
        ("sr-Latn", "sr-Latn", "Serbian (Latin)", "generic", ("Latin",), True),
        ("en-GB", "en-GB", "English (GB)", "tuned", ("Latin",), True),
        ("xx", "xx", "xx", "generic", (), True),
    ],
)
def test_any_code_gets_a_profile(code, stored, name, tier, scripts, spaced):
    language = Language(code)
    rules = profile(language)
    assert (language.value, language.display_name, rules.tier, rules.scripts, rules.spaced_words) == (
        stored, name, tier, scripts, spaced,
    )


def test_a_generic_profile_has_only_what_its_code_gives():
    rules = profile("ja")
    assert not (rules.glossary_field or rules.stock_phrases or rules.marker_examples or rules.pronouns)
    assert not (rules.address_forms or rules.quotes or rules.convention_checks or rules.number_words)
    assert rules.script_pattern.search("アスター") and not rules.script_pattern.search("Aster")
    # An unknown script still recognizes letters, so a passage counts as prose.
    assert profile("xx").script_pattern.search("ǂʼ word")


@pytest.mark.parametrize("bad", ["", "e", "en_", "123", "en--GB", None, 5])
def test_malformed_codes_are_rejected(bad):
    with pytest.raises(ValueError):
        Language(bad)


def test_pairs_keep_the_legacy_spelling_and_write_others_with_an_arrow():
    assert LanguagePair("en>zh-Hans") is TranslationDirection.EN_TO_ZH
    assert LanguagePair.of("zh-CN", "en") is TranslationDirection.ZH_TO_EN
    pair = LanguagePair("pt-BR>ja")
    assert (pair.value, pair.slug, pair.source_language, pair.target_language) == ("pt-BR>ja", "pt-BR-ja", "pt-BR", "ja")
    assert [item.value for item in LanguagePair] == ["en-zh", "zh-en"]
    for bad in ("en-ja", "en>en", "en>ja>de"):
        with pytest.raises(ValueError):
            LanguagePair(bad)


@pytest.mark.parametrize(
    ("pair", "disjoint", "leftover"),
    [
        ("en-zh", True, ("Latin",)),
        ("zh-en", True, ("Han",)),
        ("en>ja", True, ("Latin",)),
        ("en>de", False, ()),
        ("zh>ja", False, ()),  # Japanese writes Han too: a kanji run is not left-over Chinese
        ("ja>zh", False, ("Hiragana", "Katakana")),  # but kana in a Chinese translation is
        ("en>xx", False, ()),  # an unknown script tells nothing
    ],
)
def test_scripts_tell_the_sides_apart_only_when_they_differ(pair, disjoint, leftover):
    direction = LanguagePair(pair)
    assert (scripts_disjoint(direction), leftover_scripts(direction)) == (disjoint, leftover)


# -- config -----------------------------------------------------------------------------


def test_todays_pairs_serialize_exactly_as_before():
    for direction in ("en-zh", "zh-en"):
        data = json.loads(TranslationConfig.model_validate({"direction": direction}).model_dump_json())
        assert "source_language" not in data and "target_language" not in data
        assert data["direction"] == direction
    assert TranslationConfig.model_validate(
        {"source_language": "zh-Hans", "target_language": "en"}
    ).model_dump_json() == TranslationConfig.model_validate({"direction": "zh-en"}).model_dump_json()


def test_another_pair_is_stored_as_two_codes_and_round_trips():
    config = AppConfig.model_validate({"translation": {"source_language": "fr", "target_language": "ja"}})
    data = json.loads(config.translation.model_dump_json())
    assert (data["direction"], data["source_language"], data["target_language"]) == ("fr>ja", "fr", "ja")
    assert AppConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize(
    "values",
    [
        {"translation": {"source_language": "en"}},
        {"translation": {"direction": "en-zh", "source_language": "en", "target_language": "ja"}},
        {"translation": {"direction": "en>ja"}, "reprose": {"enabled": True}},
        {"translation": {"direction": "en>ja"}, "glossary": {"seed_glossaries": ["seed.json"]}},
    ],
)
def test_config_refuses_what_a_generic_pair_cannot_do(values):
    with pytest.raises(ValueError):
        AppConfig.model_validate(values)


# -- checks degrade -----------------------------------------------------------------------


def test_todays_pairs_skip_nothing_they_ran_before():
    assert language_support(TranslationDirection.EN_TO_ZH)["skipped"] == []
    # zh-en never had the Chinese punctuation and character report; it is now named.
    assert [item["check"] for item in language_support(TranslationDirection.ZH_TO_EN)["skipped"]] == [
        "punctuation conventions and character report"
    ]


def test_a_same_script_pair_lists_every_check_it_cannot_run():
    support = language_support(LanguagePair("en>de"))
    assert support["target"] == {"code": "de", "name": "German", "tier": "generic"}
    assert {item["check"] for item in support["skipped"]} == {
        "glossary and style sheet",
        "untranslated text",
        "left-over source words",
        "number words",
        "punctuation conventions and character report",
        "formulaic phrases",
        "marker examples",
        "prose rewrite",
    }
    assert not glossary_supported(LanguagePair("en>de"))


def _language_issues(pair: str, source: str, target: str) -> list[str]:
    issues = []
    _audit_language("S1", source, target, LanguagePair(pair), AuditConfig(), issues)
    return [issue.message for issue in issues]


def test_the_untranslated_check_follows_the_scripts():
    english = "She was very tired after the long walk home."
    # Different scripts: an English sentence in a Japanese translation is caught.
    found = _language_issues("en>ja", english, english)
    assert {
        "translation contains no Japanese text",
        f"possible untranslated English phrase: {english[:-1]}",
        "translation is identical to source",
    } <= set(found)
    assert _language_issues("en>ja", english, "彼女は長い帰り道の後でとても疲れていた。") == []
    # Same script: German prose is not "untranslated English"; only an identical long line is.
    assert _language_issues("en>de", english, "Sie war nach dem langen Heimweg sehr müde.") == []
    assert _language_issues("en>de", english, english) == ["translation is identical to source"]
    assert _language_issues("en>de", "Paris.", "Paris.") == []
    # Kana left in a Chinese translation of Japanese is caught; kanji is not.
    assert _language_issues("ja>zh", "彼女はとても疲れていた。", "她非常疲倦。") == []
    assert _language_issues("ja>zh", "彼女はとても疲れていた。", "她非常疲倦ですから。") == [
        "possible untranslated Japanese text: ですから"
    ]
    assert _language_issues("zh>ja", "她非常疲倦。", "彼女は非常に疲れていた。") == []


def test_prompts_leave_out_what_the_profile_lacks():
    assert _build_marker_examples(LanguagePair("en>ja"), "few-shot") == ""
    assert "Marker examples" in _build_marker_examples(TranslationDirection.EN_TO_ZH, "few-shot")
    instructions = extraction_instructions(LanguagePair("en>ja"), max_characters=5, max_expressions=5)
    assert "pronoun" not in instructions and "Japanese" in instructions


# -- end to end ---------------------------------------------------------------------------


class _Auditor(FakeAuditClient):
    """Declines numeric rulings, which a same-script pair asks for: "Chapter One"
    has no number the rules can find in German. The segment is left "uncertain"."""

    def generate_structured(self, prompt, schema, **kwargs):
        if schema is QuantityAuditResult:
            raise StructuredOutputError("this fake gives no numeric ruling")
        return super().generate_structured(prompt, schema, **kwargs)


def _runners(translated: str) -> dict:
    translator = FakeTranslationClient(translated_text=translated)
    auditor = _Auditor()
    repairer = FakeRepairClient()
    verifier = FakeVerificationClient()
    runners = default_stage_runners()
    from book_agent.stages.audit import run_translation_audit_stage
    from book_agent.stages.repair import run_translation_repair_stage
    from book_agent.stages.repair_review import run_review_repair_stage
    from book_agent.stages.review_repaired import run_repaired_review_stage
    from book_agent.stages.translate import run_translation_stage
    from book_agent.stages.validate_repaired import run_repaired_validation_stage

    runners.update({
        WorkflowStage.TRANSLATE: lambda w, c, _: run_translation_stage(w, c, translator),
        WorkflowStage.AUDIT_TRANSLATION: lambda w, c, _: run_translation_audit_stage(w, c, auditor),
        WorkflowStage.REPAIR_TRANSLATION: lambda w, c, _: run_translation_repair_stage(w, c, repairer),
        WorkflowStage.REVIEW_REPAIRED: lambda w, c, _: run_repaired_review_stage(w, c, verifier),
        WorkflowStage.REPAIR_REVIEW: lambda w, c, _: run_review_repair_stage(w, c, verifier),
        WorkflowStage.VALIDATE_REPAIRED: lambda w, c, _: run_repaired_validation_stage(w, c, verifier),
    })
    return runners, translator


@pytest.mark.parametrize(("pair", "translated"), [("en>ja", "訳文です。"), ("en>de", "Übersetzung des Absatzes.")])
def test_a_generic_pair_translates_end_to_end(pair, translated):
    config = AppConfig.model_validate({"translation": {"direction": pair}, "audit": {"semantic_enabled": True}})
    target = LanguagePair(pair).target_language.value
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        workspace = create_job_workspace(make_epub(base / "book.epub", chapter=EN_CHAPTER), base / "runs", config)
        runners, translator = _runners(translated)
        result = None
        for _ in range(6):  # through the glossary and final-review gates
            result = run_workflow(workspace, config, client=object(), stage_runners=runners)
            if result.result != "paused":
                break
            if result.stage == WorkflowStage.APPROVE_GLOSSARY.value:
                approve_glossary(workspace, config=config)
            else:
                approve_final_draft(workspace)
        assert result is not None and result.result == "complete", result
        status = workflow_status(workspace)
        assert {stage["name"]: stage["status"] for stage in status["stages"]}[WorkflowStage.VALIDATE_EPUB.value] == "completed"
        assert "glossary and style sheet" in {item["check"] for item in status["languages"]["skipped"]}

        prompts = "\n".join(translator.prompts)
        assert f"into {profile(target).display_name}" in prompts
        assert "Chinese" not in prompts and "Marker examples" not in prompts

        output = next((workspace.root / "output").rglob("*.epub"))
        with ZipFile(output) as archive:
            chapter = next(
                archive.read(name).decode("utf-8") for name in archive.namelist() if name.endswith("chapter.xhtml")
            )
        assert translated in chapter
