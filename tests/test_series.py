import json
import tempfile
from pathlib import Path

import pytest

from book_agent.cli import main
from book_agent.config import AppConfig
from book_agent.glossary import load_glossary_file
from book_agent.hashing import sha256_file
from book_agent.schemas import GlossaryCategory, GlossaryResult
from book_agent.series import (
    overlay_for_job,
    add_books,
    bind_book,
    build_workbench,
    create_series,
    decide_terms,
    import_version,
    load_manifest,
    publish_workbench,
    series_root,
    series_status,
    version_glossary_path,
)
from book_agent.series_binding import load_series_binding
from book_agent.stages.decompile import run_decompile_stage
from book_agent.stages.glossary import (
    load_glossary_draft,
    run_glossary_approval_stage,
    run_glossary_extraction_stage,
    run_glossary_resolution_stage,
)
from book_agent.stages.preprocess import _load_effective_glossary, run_preprocessing_stage
from book_agent.state import connect_state, get_stage_status
from book_agent.workspace import create_job_workspace, open_job_workspace
from tests.epub_fixture import make_epub
from tests.test_glossary_stages import FakeGlossaryClient, entry, resolution_result

CONFIG = AppConfig.model_validate({"glossary": {"extraction_chunk_tokens": 100}})


def resolved_book(runs: Path, job_id: str, terms: dict[str, str], text: str = ""):
    """A job paused at glossary approval whose resolved draft holds ``terms``.

    ``text`` replaces the fixture chapter's prose, for term-mention evidence.
    """
    chapter = (
        "<?xml version='1.0'?><html xmlns='http://www.w3.org/1999/xhtml'><head><title>T</title></head>"
        f"<body><h1>Chapter One</h1><p>{text}</p></body></html>"
    ).encode("utf-8") if text else None
    epub = make_epub(runs.parent / f"{job_id}.epub", **({"chapter": chapter} if chapter else {}))
    workspace = create_job_workspace(epub, runs, AppConfig(), job_id=job_id)
    run_decompile_stage(workspace)
    ordered = sorted(terms.items(), key=lambda item: item[0].casefold())
    candidates = GlossaryResult(entries=[entry(en, zh, ["D0000-S000001"]) for en, zh in ordered])
    run_glossary_extraction_stage(workspace, CONFIG, FakeGlossaryClient([candidates]))
    run_glossary_resolution_stage(
        workspace,
        CONFIG,
        FakeGlossaryClient(
            [
                resolution_result(
                    *(
                        (f"T{index:05d}", zh, GlossaryCategory.PERSON)
                        for index, (_, zh) in enumerate(ordered, start=1)
                    )
                )
            ]
        ),
    )
    return workspace


def approve_draft(workspace, glossary_file: Path | None = None):
    if glossary_file is None:
        glossary_file = workspace.root / "reviewed.glossary.json"
        glossary_file.write_text(load_glossary_draft(workspace).model_dump_json(), encoding="utf-8")
    run_glossary_approval_stage(workspace, CONFIG, reviewed_file=glossary_file)


def checkpoint(workspace):
    connection = connect_state(workspace.state_file)
    try:
        row = get_stage_status(connection, "preprocess")
        return row["status"], row["attempts"], row["input_hash"], row["updated_at"]
    finally:
        connection.close()


def stage_status(workspace, stage):
    connection = connect_state(workspace.state_file)
    try:
        return get_stage_status(connection, stage)["status"]
    finally:
        connection.close()


@pytest.fixture
def runs():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "runs"
        path.mkdir()
        yield path


def two_book_series(runs: Path):
    resolved_book(runs, "qel-01", {"Qelmar": "凯尔玛", "Vraxwright": "弗拉克斯赖特", "Ostrel": "奥斯特雷"})
    resolved_book(runs, "qel-02", {"Qelmar": "凯尔玛", "Vraxwright": "弗拉克赖特", "Durnhal": "杜恩哈尔"})
    create_series(runs, "qel", "The Qel Cycle", "en-zh")
    add_books(runs, "qel", ["qel-01", "qel-02"])


def terms_by_english(workbench):
    return {term.english: term for term in workbench.terms}


class SeriesManifestTests:
    def test_create_add_and_refuse_duplicates_or_other_directions(self, runs):
        resolved_book(runs, "qel-01", {"Qelmar": "凯尔玛"})
        create_series(runs, "qel", "The Qel Cycle", "en-zh")
        manifest = add_books(runs, "qel", ["qel-01"])
        assert [(b.job_id, b.volume) for b in manifest.books] == [("qel-01", 1)]
        with pytest.raises(ValueError, match="already in series"):
            add_books(runs, "qel", ["qel-01"])
        with pytest.raises(ValueError, match="already exists"):
            create_series(runs, "qel", "again", "en-zh")
        create_series(runs, "zh", "Reverse", "zh-en")
        with pytest.raises(ValueError, match="already in series qel"):
            add_books(runs, "zh", ["qel-01"])
        resolved_book(runs, "other", {"Durnhal": "杜恩哈尔"})
        with pytest.raises(ValueError, match="translates en-zh"):
            add_books(runs, "zh", ["other"])
        with pytest.raises(ValueError, match="invalid series id"):
            series_root(runs, "../escape")


class WorkbenchTests:
    def test_build_classifies_consensus_conflicts_and_single_book_terms(self, runs):
        two_book_series(runs)
        workbench = build_workbench(runs, "qel")
        terms = terms_by_english(workbench)
        assert (terms["Qelmar"].origin, terms["Qelmar"].decision) == ("consensus", "keep")
        assert (terms["Vraxwright"].origin, terms["Vraxwright"].decision) == ("conflict", "pending")
        assert set(terms["Vraxwright"].variants()) == {"弗拉克斯赖特", "弗拉克赖特"}
        assert (terms["Ostrel"].origin, terms["Ostrel"].decision) == ("single_book", "drop")
        assert workbench.boundaries == {"qel-01": "resolved", "qel-02": "resolved"}

    def test_decisions_need_a_reason_and_survive_a_rebuild(self, runs):
        two_book_series(runs)
        terms = terms_by_english(build_workbench(runs, "qel"))
        with pytest.raises(ValueError, match="reason"):
            decide_terms(runs, "qel", [terms["Vraxwright"].term_id], "keep", reason="")
        decide_terms(
            runs, "qel", [terms["Vraxwright"].term_id], "keep",
            reason="Book 1 spelling is canonical.", chinese="弗拉克斯赖特",
        )
        decide_terms(runs, "qel", [terms["Ostrel"].term_id], "keep", reason="Main place; promote.")
        rebuilt = terms_by_english(build_workbench(runs, "qel"))
        assert (rebuilt["Vraxwright"].decision, rebuilt["Vraxwright"].chinese) == ("keep", "弗拉克斯赖特")
        assert (rebuilt["Ostrel"].decision, rebuilt["Ostrel"].decided_by) == ("keep", "user")

    def test_publish_freezes_kept_terms_and_writes_book_overlays(self, runs):
        two_book_series(runs)
        terms = terms_by_english(build_workbench(runs, "qel"))
        version = publish_workbench(runs, "qel")
        assert version.version == "v001" and version.term_count == 1
        published = load_glossary_file(version_glossary_path(runs, "qel", "v001"))
        assert [(e.english, e.chinese) for e in published.entries] == [("Qelmar", "凯尔玛")]
        report = json.loads(version_glossary_path(runs, "qel", "v001").with_name("v001.report.json").read_text(encoding="utf-8"))
        assert report["pending_excluded"] == ["Vraxwright"]
        overlay = series_root(runs, "qel") / "overlays" / "v001" / "qel-02.glossary.review.json"
        assert overlay.is_file()
        assert terms["Qelmar"].decision == "keep"

        # The workbench stays open for the next version, with v001 carried and locked.
        from book_agent.series import load_workbench

        after = load_workbench(runs, "qel")
        assert after is not None and after.based_on == "v001"
        carried = terms_by_english(after)["Qelmar"]
        assert (carried.origin, carried.locked_from, carried.decision) == ("carried", "v001", "keep")
        with pytest.raises(ValueError, match="nothing changed"):
            publish_workbench(runs, "qel")


class VersionAndBindingTests:
    def test_new_volume_publishes_v002_without_touching_a_preprocessed_book(self, runs):
        two_book_series(runs)
        build_workbench(runs, "qel")
        publish_workbench(runs, "qel")
        workspace = open_job_workspace(runs / "qel-01")
        bind_book(runs, "qel", "qel-01")
        approve_draft(workspace)
        run_preprocessing_stage(workspace, CONFIG)
        assert stage_status(workspace, "preprocess") == "completed"

        resolved_book(runs, "qel-03", {"Qelmar": "凯尔玛", "Ostrel": "奥斯特雷"})
        add_books(runs, "qel", ["qel-03"])
        terms = terms_by_english(build_workbench(runs, "qel"))
        assert (terms["Qelmar"].origin, terms["Qelmar"].locked_from) == ("carried", "v001")
        assert (terms["Ostrel"].origin, terms["Ostrel"].decision) == ("consensus", "keep")
        assert publish_workbench(runs, "qel").version == "v002"

        # v001 is untouched, and the book pinned to it keeps its checkpoint.
        v001 = version_glossary_path(runs, "qel", "v001")
        assert load_manifest(runs, "qel").versions[0].glossary_sha256 == sha256_file(v001)
        before = checkpoint(workspace)
        run_preprocessing_stage(workspace, CONFIG)
        assert checkpoint(workspace) == before  # same input hash, not rerun
        assert load_series_binding(workspace.root).version == "v001"

        with pytest.raises(ValueError, match="use --upgrade"):
            bind_book(runs, "qel", "qel-01", "v002")
        result = bind_book(runs, "qel", "qel-01", "v002", upgrade=True)
        assert result["reset"][0] == "preprocess"
        assert stage_status(workspace, "preprocess") == "pending"

    def test_bound_glossary_reaches_preprocessing_and_book_entries_win(self, runs):
        two_book_series(runs)
        terms = terms_by_english(build_workbench(runs, "qel"))
        decide_terms(runs, "qel", [terms["Vraxwright"].term_id], "keep", reason="Canonical.", chinese="弗拉克斯赖特")
        publish_workbench(runs, "qel")
        workspace = open_job_workspace(runs / "qel-02")
        bind_book(runs, "qel", "qel-02")
        approve_draft(workspace)  # the book keeps its own 弗拉克赖特
        glossary, hashes = _load_effective_glossary(workspace, CONFIG)
        assert "series_binding" in hashes
        by_term = {e.english: e.chinese for e in glossary.entries}
        assert by_term["Vraxwright"] == "弗拉克赖特" and by_term["Qelmar"] == "凯尔玛"

    def test_a_tampered_version_file_is_refused(self, runs):
        two_book_series(runs)
        build_workbench(runs, "qel")
        publish_workbench(runs, "qel")
        workspace = open_job_workspace(runs / "qel-01")
        bind_book(runs, "qel", "qel-01")
        approve_draft(workspace)
        path = version_glossary_path(runs, "qel", "v001")
        path.write_text(path.read_text(encoding="utf-8").replace("凯尔玛", "凯尔马"), encoding="utf-8")
        with pytest.raises(ValueError, match="changed after it was published"):
            _load_effective_glossary(workspace, CONFIG)

    def test_import_and_unchanged_publish(self, runs):
        two_book_series(runs)
        source = runs.parent / "legacy-series.json"
        source.write_text(
            GlossaryResult(entries=[entry("Qelmar", "凯尔玛")]).model_dump_json(), encoding="utf-8"
        )
        assert import_version(runs, "qel", source).version == "v001"
        with pytest.raises(ValueError, match="nothing changed"):
            import_version(runs, "qel", source)
        terms = terms_by_english(build_workbench(runs, "qel"))
        assert terms["Qelmar"].origin == "carried"
        with pytest.raises(ValueError, match="unlock"):
            decide_terms(runs, "qel", [terms["Qelmar"].term_id], "drop", reason="Try dropping.")
        decide_terms(runs, "qel", [terms["Qelmar"].term_id], "drop", reason="Retired.", unlock=True)

    def test_status_reports_books_boundaries_and_pins(self, runs):
        two_book_series(runs)
        build_workbench(runs, "qel")
        publish_workbench(runs, "qel")
        bind_book(runs, "qel", "qel-01")
        status = series_status(runs, "qel")
        assert [(b["job_id"], b["glossary"], b["version"]) for b in status["books"]] == [
            ("qel-01", "resolved", "v001"),
            ("qel-02", "resolved", None),
        ]
        with pytest.raises(ValueError, match="not in series"):
            bind_book(runs, "qel", "nope")


class SeriesCliTests:
    def test_cli_round_trip(self, runs, capsys):
        resolved_book(runs, "qel-01", {"Qelmar": "凯尔玛"})
        resolved_book(runs, "qel-02", {"Qelmar": "凯尔玛"})
        def run(*args):
            capsys.readouterr()
            code = main(["series", args[0], "--runs", str(runs), "qel", *args[1:]])
            return code, capsys.readouterr()

        assert run("create", "--direction", "en-zh")[0] == 0
        assert run("add", "qel-01", "qel-02")[0] == 0
        code, out = run("build")
        assert code == 0 and json.loads(out.out)["by_origin_and_decision"] == {"consensus/keep": 1}
        code, out = run("publish")
        assert code == 0 and json.loads(out.out)["version"] == "v001"
        assert run("bind", "qel-01")[0] == 0
        code, out = run("status")
        assert [b["version"] for b in json.loads(out.out)["books"]] == ["v001", None]
        code, out = run("publish")  # the carried workbench holds no new terms
        assert code == 1 and "nothing changed since v001" in out.err


class FakeSuggestionClient:
    """Answers each case with ``answer(case)``; records every prompt."""

    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def generate_structured(self, prompt, schema, **_kwargs):
        from book_agent.ollama_client import GenerationMetrics, GenerationResult, StructuredGenerationResult

        self.prompts.append(prompt)
        cases = json.loads(prompt.split("Cases:\n", 1)[1].split("\n\nThe previous response", 1)[0])
        value = schema.model_validate({"decisions": [self.answer(case) for case in cases]})
        return StructuredGenerationResult(
            value=value,
            generation=GenerationResult(content=value.model_dump_json(), thinking="", metrics=GenerationMetrics()),
        )


class SuggestionTests:
    def test_conflict_suggestion_is_scoped_validated_and_applied_only_on_accept(self, runs):
        from book_agent.series_llm import accept_suggestions, suggest

        two_book_series(runs)
        terms = terms_by_english(build_workbench(runs, "qel"))
        client = FakeSuggestionClient(
            lambda case: {"term_id": case["term_id"], "selected_chinese": "弗拉克斯赖特", "rationale": "Book 1 form."}
        )
        result = suggest(runs, "qel", "conflicts", client, CONFIG)
        assert result == {"asked": 1, "suggested": 1, "calls": 1}
        assert "Vraxwright" in client.prompts[0] and "Qelmar" not in client.prompts[0]
        workbench = terms_by_english(load_workbench_or_fail(runs))
        assert workbench["Vraxwright"].suggestion.chinese == "弗拉克斯赖特"
        assert workbench["Vraxwright"].decision == "pending"  # nothing applied yet

        accept_suggestions(runs, "qel", [terms["Vraxwright"].term_id], True)
        accepted = terms_by_english(load_workbench_or_fail(runs))["Vraxwright"]
        assert (accepted.decision, accepted.chinese, accepted.decided_by) == ("keep", "弗拉克斯赖特", "llm-accepted")
        assert accepted.suggestion is None and accepted.reason.startswith("LLM")

    def test_an_invented_variant_fails_validation(self, runs):
        from book_agent.series_llm import suggest

        two_book_series(runs)
        build_workbench(runs, "qel")
        client = FakeSuggestionClient(
            lambda case: {"term_id": case["term_id"], "selected_chinese": "维拉克赖特", "rationale": "Invented."}
        )
        with pytest.raises(ValueError, match="not one of the listed variants"):
            suggest(runs, "qel", "conflicts", client, CONFIG)
        assert len(client.prompts) == CONFIG.workflow.max_retries + 1
        assert terms_by_english(load_workbench_or_fail(runs))["Vraxwright"].suggestion is None

    def test_generic_and_promote_flags_reject_and_checkpoint_reuse(self, runs):
        from book_agent.series_llm import accept_suggestions, suggest

        two_book_series(runs)
        terms = terms_by_english(build_workbench(runs, "qel"))
        promote = FakeSuggestionClient(
            lambda case: {"term_id": case["term_id"], "promote": case["english"] == "Ostrel", "rationale": "Recurring place."}
        )
        assert suggest(runs, "qel", "promote", promote, CONFIG)["suggested"] == 1
        generic = FakeSuggestionClient(lambda case: {"term_id": case["term_id"], "generic": True, "rationale": "Ordinary word."})
        suggest(runs, "qel", "generic", generic, CONFIG)
        after = terms_by_english(load_workbench_or_fail(runs))
        assert after["Ostrel"].suggestion.kind == "promote"
        assert after["Qelmar"].suggestion.kind == "drop_generic"

        accept_suggestions(runs, "qel", [terms["Ostrel"].term_id], True)
        accept_suggestions(runs, "qel", [terms["Qelmar"].term_id], False)
        after = terms_by_english(load_workbench_or_fail(runs))
        assert (after["Ostrel"].decision, after["Ostrel"].decided_by) == ("keep", "llm-accepted")
        assert (after["Qelmar"].decision, after["Qelmar"].suggestion) == ("keep", None)
        assert after["Qelmar"].dismissed == ["drop_generic"]

        # A rejected suggestion is not asked about again, even after a rebuild.
        build_workbench(runs, "qel")
        nobody = FakeSuggestionClient(lambda case: pytest.fail(f"asked again about {case['english']}"))
        assert suggest(runs, "qel", "generic", nobody, CONFIG)["asked"] == 0

    def test_an_interrupted_run_resumes_from_saved_batches(self, runs):
        from book_agent.series import _save_workbench
        from book_agent.series_llm import suggest

        two_book_series(runs)
        before = build_workbench(runs, "qel")
        first = FakeSuggestionClient(lambda case: {"term_id": case["term_id"], "generic": False, "rationale": "Name."})
        assert suggest(runs, "qel", "generic", first, CONFIG)["calls"] == 1
        _save_workbench(runs, before)  # as if the run stopped before applying its answers
        again = FakeSuggestionClient(lambda case: pytest.fail("the saved batch should answer"))
        assert suggest(runs, "qel", "generic", again, CONFIG)["calls"] == 0

    def test_decisions_made_during_a_run_win(self, runs, monkeypatch):
        from book_agent import series_llm

        two_book_series(runs)
        terms = terms_by_english(build_workbench(runs, "qel"))

        def answer(case):
            # The user decides the term while the model is still thinking.
            decide_terms(runs, "qel", [case["term_id"]], "drop", reason="Decided by hand.")
            return {"term_id": case["term_id"], "selected_chinese": "弗拉克赖特", "rationale": "x"}

        series_llm.suggest(runs, "qel", "conflicts", FakeSuggestionClient(answer), CONFIG)
        final = terms_by_english(load_workbench_or_fail(runs))["Vraxwright"]
        assert (final.decision, final.suggestion) == ("drop", None)
        assert terms["Vraxwright"].term_id == final.term_id


def load_workbench_or_fail(runs):
    from book_agent.series import load_workbench

    workbench = load_workbench(runs, "qel")
    assert workbench is not None
    return workbench


class CategoryDecisionTests:
    def test_category_changes_apply_to_one_term_and_reach_the_version(self, runs):
        two_book_series(runs)
        terms = terms_by_english(build_workbench(runs, "qel"))
        qelmar = terms["Qelmar"].term_id
        with pytest.raises(ValueError, match="one term at a time"):
            decide_terms(runs, "qel", [qelmar, terms["Ostrel"].term_id], "keep", reason="Both places.", category="place")
        with pytest.raises(ValueError, match="unknown glossary category"):
            decide_terms(runs, "qel", [qelmar], "keep", reason="Wrong.", category="planet")
        decide_terms(runs, "qel", [qelmar], "keep", reason="It is a place.", category="place")
        publish_workbench(runs, "qel")
        [published] = load_glossary_file(version_glossary_path(runs, "qel", "v001")).entries
        assert published.category is GlossaryCategory.PLACE

        # A published term's category is locked like its translation.
        carried = terms_by_english(build_workbench(runs, "qel"))["Qelmar"]
        with pytest.raises(ValueError, match="unlock"):
            decide_terms(runs, "qel", [carried.term_id], "keep", reason="Back to a person.", category="person")
        decide_terms(runs, "qel", [carried.term_id], "keep", reason="Back to a person.", category="person", unlock=True)



class EvidenceTests:
    def make_series(self, runs):
        resolved_book(runs, "qel-01", {"Qelmar": "凯尔玛", "Hudson": "哈德逊"}, "Hudson came. Qelmar ran.")
        resolved_book(runs, "qel-02", {"Qelmar": "凯尔玛"}, "Mrs. Hudson said Qelmar laughed at the Qelmarian.")
        create_series(runs, "qel", "Qel", "en-zh")
        add_books(runs, "qel", ["qel-01", "qel-02"])

    def test_build_counts_whole_word_mentions_in_every_book(self, runs):
        self.make_series(runs)
        terms = terms_by_english(build_workbench(runs, "qel"))
        hudson = terms["Hudson"]
        assert hudson.origin == "single_book" and list(hudson.books) == ["qel-01"]
        assert hudson.mentions == {"qel-01": 1, "qel-02": 1}  # the other book's text uses it too
        assert terms["Qelmar"].mentions == {"qel-01": 1, "qel-02": 1}  # "Qelmarian" is not a mention

    def test_term_evidence_lists_every_book_with_glossary_entries_and_passages(self, runs):
        from book_agent.series import term_evidence

        self.make_series(runs)
        hudson = terms_by_english(build_workbench(runs, "qel"))["Hudson"]
        evidence = term_evidence(runs, "qel", hudson.term_id)
        first, second = evidence["books"]
        assert (first["job_id"], first["volume"]) == ("qel-01", 1)
        assert [entry["chinese"] for entry in first["glossary"]] == ["哈德逊"]
        assert second["glossary"] == [] and second["mentions"] == 1
        assert any("Mrs. Hudson" in snippet for snippet in second["snippets"])
        with pytest.raises(ValueError, match="unknown term ID"):
            term_evidence(runs, "qel", "T99999")

    def test_book_titles_drop_project_gutenberg_suffixes(self):
        from types import SimpleNamespace

        from book_agent.series import book_title

        def title(name):
            return book_title(SimpleNamespace(source_file=Path(name), root=Path("job")))

        assert title("A Study In Scarlet.pg244-images-3.epub") == "A Study In Scarlet"
        assert title("THE HOUND OF THE BASKERVILLES pg2852-images-3.epub") == "THE HOUND OF THE BASKERVILLES"
        assert title("Plain Book.epub") == "Plain Book"


class PromotedTermReachesBooksTests:
    def test_a_promoted_term_applies_to_a_book_whose_glossary_never_had_it(self, runs):
        """Keep on a single-book term publishes it; every pinned book then uses it."""
        resolved_book(runs, "qel-01", {"Qelmar": "凯尔玛", "Hudson": "哈德逊"}, "Hudson came. Qelmar ran.")
        resolved_book(runs, "qel-02", {"Qelmar": "凯尔玛"}, "Mrs. Hudson met Qelmar.")
        create_series(runs, "qel", "Qel", "en-zh")
        add_books(runs, "qel", ["qel-01", "qel-02"])
        hudson = terms_by_english(build_workbench(runs, "qel"))["Hudson"]
        assert hudson.origin == "single_book" and hudson.mentions == {"qel-01": 1, "qel-02": 1}

        decide_terms(runs, "qel", [hudson.term_id], "keep", reason="Recurring character; promote.")
        publish_workbench(runs, "qel")

        # The second book's own glossary is unchanged: the overlay only harmonizes
        # entries it already has.
        overlay = overlay_for_job(runs, "qel-02")
        assert "Hudson" not in {entry["english"] for entry in overlay["entries"]}

        # But preprocessing merges the pinned series version in, so the book uses it.
        workspace = open_job_workspace(runs / "qel-02")
        bind_book(runs, "qel", "qel-02")
        approve_draft(workspace)
        glossary, _ = _load_effective_glossary(workspace, CONFIG)
        assert {e.english: e.chinese for e in glossary.entries}["Hudson"] == "哈德逊"
