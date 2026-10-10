"""Changing a started job's config (docs/OUTPUT_AND_CONFIG_UX.md, 4): what each setting affects, and saving it."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from book_agent import cli
from book_agent.config import AppConfig
from book_agent.config_impact import (
    LOCKED,
    affected_stages,
    config_changes,
    config_impact,
    first_stage,
    locked_reason,
    rerun_roots,
    setting_paths,
)
from book_agent.pipeline_state import WorkflowStage
from book_agent.stages.compile import run_epub_compile_stage
from book_agent.state import connect_state, get_job_metadata, get_stage_status
from book_agent.web.messages import UserError
from book_agent.web.server import UiApp
from book_agent.workflow import (
    ExitCode,
    config_change_log,
    load_workspace_config,
    replace_workspace_config,
    workflow_status,
)
from tests.test_cli_commands import run
from tests.test_compile_stages import prepare_workspace

BASE = {"audit": {"semantic_enabled": False}}


def config(**sections) -> AppConfig:
    return AppConfig.model_validate({**BASE, **sections})


def stage_hash(workspace, stage: str) -> str:
    connection = connect_state(workspace.state_file)
    try:
        return str(get_stage_status(connection, stage)["input_hash"])
    finally:
        connection.close()


class MapTests:
    def test_every_setting_has_a_stage_or_is_said_to_have_none(self):
        # A setting added to the config without a line in the table fails here.
        paths = setting_paths()
        assert len(paths) > 140 and "audit.quantity.model" in paths and "output.format" in paths
        for path in paths:
            stage = first_stage(path)
            assert stage is None or isinstance(stage, WorkflowStage), path

    def test_a_setting_the_table_does_not_know_is_an_error_not_a_guess(self):
        with pytest.raises(KeyError, match="no pipeline stage is recorded for the setting cloud.model"):
            first_stage("cloud.model")

    @pytest.mark.parametrize(
        "path, stage",
        [
            ("glossary.extraction_model", "extract_glossary"),
            ("consistency.style_sheet.enabled", "extract_glossary"),
            ("ollama.model", "resolve_glossary"),
            ("glossary.book_glossaries", "resolve_glossary"),
            ("workflow.llm_glossary_review", "approve_glossary"),
            ("consistency.story_context.enabled", "build_story_context"),
            ("consistency.story_context.chapters_before", "preprocess"),
            ("translation.style", "translate"),
            ("ollama.temperature", "translate"),
            ("budget.source_tokens", "translate"),
            ("translation.fallback_models", "rescue_translation"),
            ("audit.model", "audit_translation"),
            ("audit.quantity.enabled", "audit_translation"),
            ("subtitles.characters_per_second", "audit_translation"),
            ("consistency.conventions", "audit_consistency"),
            ("audit.repair_min_severity", "audit_consistency"),
            ("audit.repair_model", "repair_translation"),
            ("reprose.enabled", "reprose_translation"),
            ("audit.verifier_model", "review_repaired"),
            ("audit.semantic_verification_policy", "validate_repaired"),
            ("translation.translated_title", "translate_title"),
            ("epub.strip_print_page_markers", "compile"),
            ("output.format", "compile"),
            ("workflow.compile_max_unresolved_review_segments", "compile"),
        ],
    )
    def test_a_setting_first_affects_the_stage_that_reads_it(self, path, stage):
        assert first_stage(path).value == stage

    @pytest.mark.parametrize("path", ["ollama.timeout_seconds", "ollama.host", "workflow.max_retries", "ollama.keep_alive"])
    def test_how_a_job_is_run_affects_nothing_already_done(self, path):
        assert first_stage(path) is None and not locked_reason(path)

    def test_the_direction_and_the_paths_are_locked(self):
        assert locked_reason("translation.direction") == "direction" == locked_reason("translation.target_language")
        assert locked_reason("paths.runs") == "paths" == locked_reason("paths.prompts")
        assert locked_reason("translation.style") == "" and set(LOCKED.values()) == {"direction", "paths"}


class ChangeTests:
    def test_changed_settings_are_listed_with_their_values_and_stage(self):
        before = config()
        after = config(
            output={"format": "docx"}, ollama={"timeout_seconds": 900}, translation={"style": "concise"},
            audit={"semantic_enabled": False, "model": "other-model:1b"},
        )
        changes = {change["path"]: change for change in config_changes(before, after)}
        # A section left at its defaults is not written into a config, and is still compared.
        assert changes["output.format"] == {
            "path": "output.format", "before": "source", "after": "docx", "stage": "compile", "stages": ["compile"], "locked": "",
        }
        assert changes["ollama.timeout_seconds"]["stage"] == "" and changes["ollama.timeout_seconds"]["after"] == 900
        assert changes["translation.style"]["stage"] == "translate"
        assert changes["audit.model"]["stage"] == "audit_translation"
        assert len(changes) == 4 and config_changes(before, config()) == []

    def test_a_locked_setting_is_marked_as_one(self):
        # Another pair of languages changes the direction and the two languages it stands for.
        changes = {change["path"]: change["locked"] for change in config_changes(config(), config(translation={"direction": "en>de"}))}
        assert changes["translation.direction"] == "direction" and set(changes.values()) == {"direction"}

    def test_the_job_is_rerun_from_the_earliest_finished_stage_a_change_affects(self):
        changes = config_changes(
            config(), config(output={"format": "docx"}, translation={"style": "concise"}, ollama={"timeout_seconds": 900})
        )
        finished = {stage.value: "completed" for stage in WorkflowStage}
        impact = config_impact(changes, finished)
        assert impact["rerun_stage"] == "translate" and impact["locked"] == []
        assert {change["path"]: change["finished"] for change in impact["changes"]} == {
            "ollama.timeout_seconds": False, "translation.style": True, "output.format": True,
        }
        # Stopped before the audit: the translation is done and is affected; the compile is still to come.
        stopped = {**finished, **{stage.value: "pending" for stage in list(WorkflowStage)[8:]}}
        assert stopped["translate"] == "completed" and stopped["audit_translation"] == "pending"
        assert config_impact(changes, stopped)["rerun_stage"] == "translate"
        only_output = [change for change in changes if change["path"] != "translation.style"]
        assert config_impact(only_output, stopped)["rerun_stage"] == ""  # applies to what runs next
        # A stage that failed part-way has not finished: it starts again with the new setting when the job resumes.
        failed = {**stopped, "translate": "failed"}
        assert config_impact(changes, failed)["rerun_stage"] == ""

    def test_a_rerun_of_one_stage_redoes_its_dependents_and_not_the_other_branch(self):
        # The glossary and the story summaries both start from the source and meet again at the preprocessing.
        glossary, story = WorkflowStage.EXTRACT_GLOSSARY, WorkflowStage.BUILD_STORY_CONTEXT
        assert rerun_roots([glossary, story]) == [glossary, story]
        assert rerun_roots([story, glossary, WorkflowStage.TRANSLATE, WorkflowStage.COMPILE]) == [glossary, story]
        assert rerun_roots([WorkflowStage.RESOLVE_GLOSSARY, WorkflowStage.PREPROCESS]) == [WorkflowStage.RESOLVE_GLOSSARY]
        assert rerun_roots([WorkflowStage.COMPILE, WorkflowStage.TRANSLATE, WorkflowStage.COMPILE]) == [WorkflowStage.TRANSLATE]
        assert rerun_roots([WorkflowStage.DECOMPILE, story]) == [WorkflowStage.DECOMPILE] and rerun_roots([]) == []

    def test_changes_to_the_glossary_and_to_the_story_summaries_rerun_both(self):
        # Reviewed on PR #11: the rerun of the glossary's extraction left the summaries as they were.
        changes = config_changes(
            config(),
            config(glossary={"extraction_max_entries": 77}, consistency={"story_context": {"enabled": True}}),
        )
        assert {change["path"]: change["stage"] for change in changes} == {
            "glossary.extraction_max_entries": "extract_glossary", "consistency.story_context.enabled": "build_story_context",
        }
        impact = config_impact(changes, {stage.value: "completed" for stage in WorkflowStage})
        assert impact["rerun_stages"] == ["extract_glossary", "build_story_context"]
        assert impact["rerun_stage"] == "extract_glossary"
        # One on its own asks for itself alone; a later stage is covered by either.
        only_story = [change for change in changes if change["path"].startswith("consistency.")]
        assert config_impact(only_story, {stage.value: "completed" for stage in WorkflowStage})["rerun_stages"] == ["build_story_context"]
        with_output = [*changes, *config_changes(config(), config(output={"format": "docx"}))]
        assert config_impact(with_output, {stage.value: "completed" for stage in WorkflowStage})["rerun_stages"] == [
            "extract_glossary", "build_story_context",
        ]

    def test_the_default_model_also_writes_the_story_summaries_that_name_no_model_of_their_own(self):
        # Reviewed on PR #11: the summaries kept the old model's words.
        summarised = {"consistency": {"story_context": {"enabled": True}}}
        own_model = {"consistency": {"story_context": {"enabled": True, "model": "summaries:1b"}}}
        other = {"ollama": {"model": "other-model:1b"}}
        finished = {stage.value: "completed" for stage in WorkflowStage}

        def stages(before: dict, after: dict) -> list[str]:
            [change] = [item for item in config_changes(config(**before), config(**after)) if item["path"] == "ollama.model"]
            assert config_impact([change], finished)["rerun_stages"] == change["stages"]
            return change["stages"]

        assert stages(summarised, {**summarised, **other}) == ["resolve_glossary", "build_story_context"]
        # With a model of their own, or switched off, the summaries do not depend on the default model.
        assert stages(own_model, {**own_model, **other}) == ["resolve_glossary"]
        assert stages({}, other) == ["resolve_glossary"]
        # Switched on in the same change: the summaries are written by the new default model.
        assert stages({}, {**summarised, **other}) == ["resolve_glossary", "build_story_context"]
        assert affected_stages("ollama.timeout_seconds", config(), config(**summarised)) == []

    def test_a_locked_change_is_named_and_asks_for_no_rerun(self):
        changes = config_changes(config(), config(translation={"direction": "en>de"}))
        impact = config_impact(changes, {stage.value: "completed" for stage in WorkflowStage})
        assert "translation.direction" in impact["locked"] and impact["rerun_stage"] == ""


class DriftTests:
    """The table against the pipeline: a changed setting makes its mapped stage's input stale, and no earlier stage's."""

    @pytest.mark.parametrize(
        "sections",
        [
            {"epub": {"strip_print_page_markers": True}},
            {"output": {"format": "md"}},
            {"workflow": {"compile_max_unresolved_review_segments": 7}},
        ],
    )
    def test_a_compile_setting_makes_the_compile_stale_and_nothing_before_it(self, sections):
        [path] = [change["path"] for change in config_changes(config(), config(**sections))]
        assert first_stage(path) is WorkflowStage.COMPILE
        with tempfile.TemporaryDirectory() as directory:
            workspace = prepare_workspace(Path(directory), config())
            run_epub_compile_stage(workspace, config())
            compiled, validated = stage_hash(workspace, "compile"), stage_hash(workspace, "validate_repaired")
            run_epub_compile_stage(workspace, config(**sections))
            assert stage_hash(workspace, "compile") != compiled
            assert stage_hash(workspace, "validate_repaired") == validated

    @pytest.mark.parametrize(
        "sections",
        [
            {"ollama": {"timeout_seconds": 900, "keep_alive": "5m"}},
            {"workflow": {"max_retries": 7}},
            {"translation": {"style": "concise"}},  # read by an earlier stage: the compile does not go stale over it
            {"audit": {"semantic_enabled": False, "repair_min_severity": "high"}},
        ],
    )
    def test_a_setting_mapped_elsewhere_or_nowhere_leaves_the_compile_as_it_is(self, sections):
        for change in config_changes(config(), config(**sections)):
            assert first_stage(change["path"]) is not WorkflowStage.COMPILE
        with tempfile.TemporaryDirectory() as directory:
            workspace = prepare_workspace(Path(directory), config())
            run_epub_compile_stage(workspace, config())
            compiled = stage_hash(workspace, "compile")
            run_epub_compile_stage(workspace, config(**sections))
            assert stage_hash(workspace, "compile") == compiled

    def test_a_translation_setting_makes_the_translation_stale_and_not_the_preprocessing(self):
        from book_agent.stages.preprocess import run_preprocessing_stage
        from book_agent.stages.translate import run_translation_stage
        from tests.test_subtitles import CONFIG, SCENE, _Subtitler, _translated

        plain = AppConfig.model_validate(CONFIG)
        with tempfile.TemporaryDirectory() as directory:
            workspace = _translated(Path(directory), SCENE, plain)
            translated, preprocessed = stage_hash(workspace, "translate"), stage_hash(workspace, "preprocess")
            for sections, stale in (
                ({"ollama": {"temperature": 0.9}}, True),
                ({"translation": {"direction": "en>de", "de_ai_strength": "moderate"}}, True),
                ({"ollama": {"timeout_seconds": 900}}, False),
                ({"audit": {"semantic_enabled": False, "repair_min_severity": "high"}}, False),
                ({"output": {"format": "vtt"}}, False),
            ):
                changed = AppConfig.model_validate({**CONFIG, **sections})
                paths = [change["path"] for change in config_changes(plain, changed)]
                assert all((first_stage(path) is WorkflowStage.TRANSLATE) == stale for path in paths), paths
                run_preprocessing_stage(workspace, changed)
                run_translation_stage(workspace, changed, _Subtitler(SCENE))
                assert (stage_hash(workspace, "translate") != translated) == stale, sections
                assert stage_hash(workspace, "preprocess") == preprocessed, sections
                run_translation_stage(workspace, plain, _Subtitler(SCENE))
                assert stage_hash(workspace, "translate") == translated


def metadata(workspace, key: str) -> str:
    connection = connect_state(workspace.state_file)
    try:
        return get_job_metadata(connection, key) or ""
    finally:
        connection.close()


def compiled_job(directory: str, **sections):
    """A job taken through the compile, with every stage before it finished."""
    workspace = prepare_workspace(Path(directory), config(**sections))
    run_epub_compile_stage(workspace, config(**sections))
    return workspace, UiApp(workspace.root.parent, Path(directory), [])


class SaveTests:
    def test_the_snapshot_and_its_recorded_hash_are_replaced_together_and_the_change_is_written_down(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = prepare_workspace(Path(directory), config())
            before, first_hash = load_workspace_config(workspace), metadata(workspace, "config_sha256")
            after = config(output={"format": "docx"})
            assert config_change_log(workspace) == []
            record = replace_workspace_config(workspace, after, config_changes(before, after))
            assert load_workspace_config(workspace).output.format == "docx"
            assert metadata(workspace, "config_sha256") == record["config_sha256"] != first_hash
            assert record["previous_config_sha256"] == first_hash
            assert record["changes"] == [
                {"path": "output.format", "before": "source", "after": "docx", "stage": "compile", "stages": ["compile"]}
            ]
            replace_workspace_config(workspace, before, config_changes(after, before))
            log = config_change_log(workspace)
            assert [entry["changes"][0]["after"] for entry in log] == ["docx", "source"] and log[0]["at"]
            assert metadata(workspace, "config_sha256") == first_hash

    def test_the_dashboard_says_what_a_change_does_before_anything_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, app = compiled_job(directory)
            job = workspace.root.name
            served = app.job_config(job)
            assert served["editable"] is False and served["unlockable"] is True and served["changes"] == []
            assert served["locked"] == {
                "translation.direction": "direction", "translation.source_language": "direction",
                "translation.target_language": "direction", "paths.": "paths",
            }
            text = served["text"]
            assert app.config_preview(job, {"text": text})["changes"] == []

            changed = text + "\noutput:\n  format: docx\n"
            preview = app.config_preview(job, {"text": changed})
            assert preview["errors"] == [] and preview["rerun_stage"] == "compile" and preview["locked"] == []
            [change] = preview["changes"]
            assert (change["path"], change["before"], change["after"], change["stage"], change["finished"]) == (
                "output.format", "source", "docx", "compile", True,
            )
            assert load_workspace_config(workspace).output.format == "source"  # only a preview

    def test_a_change_that_no_finished_stage_read_asks_for_no_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = prepare_workspace(Path(directory), config())  # compiled never: the compile is still to run
            app = UiApp(workspace.root.parent, Path(directory), [])
            text = app.job_config(workspace.root.name)["text"]
            preview = app.config_preview(workspace.root.name, {"text": text + "\noutput:\n  format: docx\n"})
            assert preview["rerun_stage"] == "" and preview["changes"][0]["finished"] is False
            preview = app.config_preview(workspace.root.name, {"text": text.replace("timeout_seconds: ", "timeout_seconds: 9")})
            assert [change["stage"] for change in preview["changes"]] == [""] and preview["rerun_stage"] == ""

    def test_saving_replaces_the_jobs_config_and_resets_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, app = compiled_job(directory)
            job = workspace.root.name
            text = app.job_config(job)["text"]
            before = [(stage["name"], stage["status"]) for stage in workflow_status(workspace)["stages"]]
            saved = app.save_job_config(job, {"text": text + "\noutput:\n  format: docx\n"})
            assert saved["saved"] is True and saved["rerun_stage"] == "compile" and "config" not in saved
            assert load_workspace_config(workspace).output.format == "docx"
            # The rerun is a step of its own: nothing of the pipeline is touched by the save.
            assert [(stage["name"], stage["status"]) for stage in workflow_status(workspace)["stages"]] == before
            served = app.job_config(job)
            assert "format: docx" in served["text"] and len(served["changes"]) == 1
            assert served["changes"][0]["changes"][0]["path"] == "output.format"
            # Saved again unchanged: nothing to do, and nothing written down.
            assert app.save_job_config(job, {"text": served["text"]})["saved"] is False
            assert len(config_change_log(workspace)) == 1
            # The stage named can be rerun as from the Progress tab.
            assert app.rerun_preview(job, "compile")["stage"] == "compile"

    def test_a_save_whose_effect_is_not_the_one_the_page_showed_is_refused(self):
        # Reviewed on PR #11: an edit made after the preview was saved with the earlier preview's rerun.
        with tempfile.TemporaryDirectory() as directory:
            workspace, app = compiled_job(directory)
            job = workspace.root.name
            text = app.job_config(job)["text"]
            one = text + "\noutput:\n  format: docx\n"
            two = one.replace("style: literary", "style: concise")
            assert two != one and app.config_preview(job, {"text": two})["rerun_stages"] == ["translate"]
            # The page showed the first text's effect and sends the second text.
            for shown in (["compile"], []):
                with pytest.raises(UserError, match="do not have the effect that was shown"):
                    app.save_job_config(job, {"text": two, "rerun_stages": shown})
            assert load_workspace_config(workspace).output.format == "source" and config_change_log(workspace) == []
            # What was shown is what is saved.
            saved = app.save_job_config(job, {"text": one, "rerun_stages": ["compile"]})
            assert saved["saved"] and saved["rerun"] is None and saved["rerun_stages"] == ["compile"]

    def test_a_save_with_a_rerun_resets_every_branch_the_change_reaches_and_resumes_once(self):
        from book_agent.state import StageStatus, set_stage_status

        with tempfile.TemporaryDirectory() as directory:
            workspace, app = compiled_job(directory)
            job = workspace.root.name
            # The fixture runs the stages it needs; a finished job has them all complete.
            connection = connect_state(workspace.state_file)
            try:
                for stage in workflow_status(workspace)["stages"]:
                    if stage["status"] != StageStatus.COMPLETED.value:
                        set_stage_status(connection, stage["name"], StageStatus.COMPLETED)
            finally:
                connection.close()
            text = app.job_config(job)["text"]
            import yaml

            values = yaml.safe_load(text)
            values["glossary"]["extraction_max_entries"] = 77
            values["consistency"]["story_context"]["enabled"] = True
            changed = yaml.safe_dump(values, sort_keys=False, allow_unicode=True)
            preview = app.config_preview(job, {"text": changed})
            assert preview["rerun_stages"] == ["extract_glossary", "build_story_context"]

            # The dialog lists both branches' stages, each once, in the pipeline's order.
            listed = [stage["name"] for stage in app.rerun_preview(job, "extract_glossary", ["build_story_context"])["stages"]]
            assert listed[:5] == ["extract_glossary", "resolve_glossary", "approve_glossary", "build_story_context", "preprocess"]
            assert len(listed) == len(set(listed))
            assert "build_story_context" not in [stage["name"] for stage in app.rerun_preview(job, "extract_glossary")["stages"]]

            with patch.object(app, "launch", return_value={"started": True}) as launch:
                saved = app.save_job_config(job, {"text": changed, "rerun": True, "rerun_stages": preview["rerun_stages"]})
            assert saved["saved"] and saved["rerun"] == {"started": True}
            # One run is started, from the glossary; the summaries' stage was reset before it.
            assert launch.call_count == 1 and launch.call_args.args[1][-3:] == ["--stage", "extract_glossary", "--resume"]
            statuses = {stage["name"]: stage["status"] for stage in workflow_status(workspace)["stages"]}
            assert statuses["build_story_context"] == "pending" and statuses["decompile"] == "completed"
            assert load_workspace_config(workspace).consistency.story_context.enabled is True

    def test_a_config_with_problems_or_a_locked_setting_changed_is_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, app = compiled_job(directory)
            job = workspace.root.name
            text = app.job_config(job)["text"]

            preview = app.config_preview(job, {"text": text.replace("temperature: ", "temperature: hot")})
            assert preview["errors"][0]["path"] == "ollama.temperature" and preview["changes"] == []
            with pytest.raises(UserError, match="the configuration has problems; nothing was saved"):
                app.save_job_config(job, {"text": text.replace("temperature: ", "temperature: hot")})
            assert app.config_preview(job, {"text": "ollama: [unclosed"})["errors"]

            # An output format this job cannot be given, as when a new job is validated.
            preview = app.config_preview(job, {"text": text + "\noutput:\n  format: vtt\n"})
            assert preview["errors"] == [{"path": "output.format", "message": preview["errors"][0]["message"]}]
            assert "a subtitle format, and this is a book" in preview["errors"][0]["message"]

            other = text.replace("direction: en-zh", "direction: en>de")
            assert other != text
            preview = app.config_preview(job, {"text": other})
            assert "translation.direction" in preview["locked"] and not [item for item in preview["locked"] if item.startswith("paths.")]
            with pytest.raises(UserError, match="cannot be changed once a job has started: .*translation.direction"):
                app.save_job_config(job, {"text": other})
            assert load_workspace_config(workspace).translation.direction.value == "en-zh"
            assert config_change_log(workspace) == []

    def test_a_running_job_and_a_job_not_started_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, app = compiled_job(directory)
            job = workspace.root.name
            text = app.job_config(job)["text"]
            with patch.object(app, "job_info", return_value={"running": True}):
                assert app.job_config(job)["unlockable"] is False
                for call in (app.config_preview, app.save_job_config):
                    with pytest.raises(UserError, match="the job is running; pause or stop it before changing its configuration"):
                        call(job, {"text": text + "\noutput:\n  format: docx\n"})
            assert load_workspace_config(workspace).output.format == "source"
            with pytest.raises(UserError, match="no job or draft named nowhere"):
                app.config_preview("nowhere", {"text": text})


class CommandLineTests:
    def proposed(self, workspace, **sections) -> Path:
        path = workspace.root.parent.parent / "changed.yaml"
        data = {**load_workspace_config(workspace).model_dump(mode="json"), **sections}
        path.write_text(json.dumps(data), encoding="utf-8")  # JSON is YAML
        return path

    def test_config_diff_names_the_stage_each_change_affects(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = compiled_job(directory)
            path = self.proposed(workspace, output={"format": "docx"}, ollama={**load_workspace_config(workspace).ollama.model_dump(mode="json"), "timeout_seconds": 900})
            code, output, _ = run(["config-diff", str(workspace.root), "--config", str(path)])
            assert code == ExitCode.COMPLETE
            assert "- output.format: 'source' -> 'docx' (first affects compile, which has finished)" in output
            assert "- ollama.timeout_seconds:" in output and "(applies to what runs next)" in output
            assert f'rerun from compile: book-agent retry "{workspace.root}" --stage compile --resume' in output
            assert load_workspace_config(workspace).output.format == "source"
            diff = json.loads(run(["config-diff", str(workspace.root), "--config", str(path), "--json"])[1])
            assert diff["rerun_stage"] == "compile" and diff["change_count"] == 2 and diff["locked"] == []

    def test_config_apply_saves_and_with_rerun_resets_the_stage_for_the_next_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = compiled_job(directory)
            path = self.proposed(workspace, output={"format": "docx"})
            code, output, _ = run(["config-apply", str(workspace.root), "--config", str(path)])
            assert code == ExitCode.COMPLETE and "Saved as the job's configuration." in output
            assert "rerun from compile" in output and load_workspace_config(workspace).output.format == "docx"
            statuses = {stage["name"]: stage["status"] for stage in workflow_status(workspace)["stages"]}
            assert statuses["compile"] == "completed" and len(config_change_log(workspace)) == 1

            path = self.proposed(workspace, output={"format": "md"})
            code, output, _ = run(["config-apply", str(workspace.root), "--config", str(path), "--rerun"])
            assert code == ExitCode.COMPLETE and "Reset for the next resume: compile, validate_epub" in output
            statuses = {stage["name"]: stage["status"] for stage in workflow_status(workspace)["stages"]}
            assert (statuses["compile"], statuses["validate_repaired"]) == ("pending", "completed")

    def test_config_apply_names_and_resets_both_branches_of_the_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = compiled_job(directory)
            captured = load_workspace_config(workspace).model_dump(mode="json")
            path = self.proposed(
                workspace,
                glossary={**captured["glossary"], "extraction_max_entries": 77},
                consistency={**captured["consistency"], "story_context": {**captured["consistency"]["story_context"], "enabled": True}},
            )
            statuses = {stage["name"]: stage["status"] for stage in workflow_status(workspace)["stages"]}
            finished = {**workflow_status(workspace), "stages": [{"name": name, "status": "completed"} for name in statuses]}
            with patch.object(cli, "workflow_status", return_value=finished):
                code, output, _ = run(["config-diff", str(workspace.root), "--config", str(path)])
                assert code == ExitCode.COMPLETE and "rerun from extract_glossary and build_story_context: " in output
                # The other branch is reset first; the last command resumes.
                assert output.index("--stage build_story_context;") < output.index("--stage extract_glossary --resume")
                code, output, _ = run(["config-apply", str(workspace.root), "--config", str(path), "--rerun"])
            assert code == ExitCode.COMPLETE
            reset = output.split("Reset for the next resume: ")[1].splitlines()[0].split(", ")
            assert reset[:4] == ["extract_glossary", "resolve_glossary", "approve_glossary", "build_story_context"]
            assert len(reset) == len(set(reset))

    def test_config_apply_refuses_a_locked_setting_a_running_job_and_a_format_the_job_cannot_have(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = compiled_job(directory)
            translation = load_workspace_config(workspace).translation.model_dump(mode="json")
            path = self.proposed(workspace, translation={**translation, "direction": "en>de"})
            code, _, error = run(["config-apply", str(workspace.root), "--config", str(path)])
            assert code == ExitCode.FAILED and "cannot be changed once a job has started: " in error and "translation.direction" in error
            path = self.proposed(workspace, output={"format": "srt"})
            code, _, error = run(["config-apply", str(workspace.root), "--config", str(path)])
            assert code == ExitCode.FAILED and "a subtitle format, and this is a book" in error
            path = self.proposed(workspace, output={"format": "docx"})
            running = {"stages": [{"name": "translate", "status": "running"}]}
            with patch.object(cli, "workflow_status", return_value={**workflow_status(workspace), **running}):
                code, _, error = run(["config-apply", str(workspace.root), "--config", str(path)])
            assert code == ExitCode.FAILED and "the job is running" in error
            assert load_workspace_config(workspace).output.format == "source" and config_change_log(workspace) == []


class HttpTests:
    def test_the_preview_and_the_save_are_served_and_need_the_pages_token(self):
        from tests.test_web_ui import ServerTests

        with tempfile.TemporaryDirectory() as directory:
            workspace, app = compiled_job(directory)
            job = workspace.root.name
            changed = app.job_config(job)["text"] + "\noutput:\n  format: docx\n"
            server, call = ServerTests().serve(app)
            token = {"X-UI-Token": app.token}
            try:
                assert call(f"/api/jobs/{job}/config/preview", {"text": changed})[0] == 403  # no token
                status, body = call(f"/api/jobs/{job}/config/preview", {"text": changed}, token)
                preview = json.loads(body)
                # The parsed config is the server's own business: the page is sent what it shows.
                assert status == 200 and set(preview) == {"errors", "changes", "locked", "rerun_stage", "rerun_stages"}
                assert preview["rerun_stage"] == "compile" and preview["changes"][0]["path"] == "output.format"
                assert load_workspace_config(workspace).output.format == "source"

                assert call(f"/api/jobs/{job}/config", {"text": changed})[0] == 403
                status, body = call(f"/api/jobs/{job}/config", {"text": changed}, token)
                assert status == 200 and json.loads(body)["saved"] is True
                assert load_workspace_config(workspace).output.format == "docx"
                served = json.loads(call(f"/api/jobs/{job}/config")[1])
                assert served["unlockable"] is True and len(served["changes"]) == 1

                # A refusal reaches the page as its message and its code.
                locked = served["text"].replace("direction: en-zh", "direction: en>de")
                status, body = call(f"/api/jobs/{job}/config", {"text": locked}, token)
                refusal = json.loads(body)
                assert status >= 400 and refusal["code"] == "config_locked" and "translation.direction" in refusal["error"]
            finally:
                server.shutdown()
                server.server_close()
