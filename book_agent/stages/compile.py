"""Resumable translated document compilation stage."""

from __future__ import annotations

import json

from ..atomic_io import atomic_write_text
from ..config import AppConfig
from ..epub_compile import EpubCompilationReport, compile_epub_package
from ..hashing import sha256_file
from ..manual_review import (
    ManualReviewDecision,
    ManualReviewWorksheet,
    ManualReviewWorksheetResolution,
)
from ..pipeline_state import (
    WorkflowStage,
    build_stage_input_hash,
    build_stage_output_hash,
    invalidate_stage_and_dependents,
    stage_is_current,
)
from ..rtf import compile_rtf_document
from ..state import (
    StageStatus,
    connect_state,
    get_job_metadata,
    get_stage_status,
    initialize_state,
    record_artifact,
    set_job_metadata,
    set_stage_status,
)
from ..workspace import JobWorkspace
from .decompile import load_decompile_manifest
from .preprocess import load_preprocessed_documents
from .audit import load_document_audits
from .repair import load_repaired_documents
from .reprose import load_reprosed_documents
from .review_repaired import load_repaired_review_results
from .translate import load_translated_documents
from .validate_repaired import (
    load_repaired_document_validations,
    load_repaired_validation_report,
    load_validated_repaired_documents,
)


COMPILE_STAGE_VERSION = "6"


def run_epub_compile_stage(
    workspace: JobWorkspace,
    config: AppConfig,
) -> EpubCompilationReport:
    """Compile the validated repaired draft into an atomic EPUB artifact."""
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        decompile = _require_completed(connection, WorkflowStage.DECOMPILE)
        validated = _require_completed(connection, WorkflowStage.VALIDATE_REPAIRED)
        validation_report = load_repaired_validation_report(workspace)
        unresolved_count = len(validation_report.review_segment_ids)
        unresolved_limit = config.workflow.compile_max_unresolved_review_segments
        manual_review_path = write_unresolved_review_report(
            workspace,
            validation_report.review_segment_ids,
            unresolved_limit,
            connection=connection,
            validation_report=validation_report,
        )
        if unresolved_count > unresolved_limit:
            review_ids = ", ".join(validation_report.review_segment_ids)
            report_path = get_job_metadata(connection, "repaired_validation_report")
            defect_count = validation_report.remaining_defect_count
            approval_count = validation_report.approval_required_count
            if unresolved_count and defect_count == 0 and approval_count == 0:
                defect_count = unresolved_count
            raise RuntimeError(
                "repaired draft has "
                f"{defect_count} unresolved defect(s) and "
                f"{approval_count} approval-only segment(s) "
                f"({unresolved_count} total), exceeding the configured "
                f"compile limit of {unresolved_limit}; compilation is refused. IDs: {review_ids}. "
                f"Review report: {report_path or 'not recorded'}. "
                f"Manual review details: {manual_review_path}"
            )
        input_hash = build_stage_input_hash(
            {
                "decompile": str(decompile["output_hash"]),
                "validated_repaired": str(validated["output_hash"]),
                "unresolved_review_ids": "\n".join(
                    validation_report.review_segment_ids
                ),
                "unresolved_review_limit": str(unresolved_limit),
                "strip_print_page_markers": str(
                    config.epub.strip_print_page_markers
                ),
                "insert_missing_chapter_headings": str(
                    config.epub.insert_missing_chapter_headings
                ),
                "chapter_heading_labels": "\n".join(
                    config.epub.chapter_heading_labels
                ),
                "stage_version": COMPILE_STAGE_VERSION,
            }
        )
        if stage_is_current(
            connection,
            WorkflowStage.COMPILE,
            input_hash,
            artifact_root=workspace.root,
        ):
            return load_epub_compilation_report(workspace, connection=connection)
        previous = get_stage_status(connection, WorkflowStage.COMPILE.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, WorkflowStage.COMPILE)
            previous = get_stage_status(connection, WorkflowStage.COMPILE.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection,
            WorkflowStage.COMPILE.value,
            StageStatus.RUNNING,
            attempts=attempts,
            input_hash=input_hash,
        )
        manifest = load_decompile_manifest(workspace)
        repaired = load_validated_repaired_documents(workspace)
        manifest_relative = get_job_metadata(connection, "decompile_manifest")
        if not manifest_relative:
            raise FileNotFoundError("decompile manifest is not recorded")
        package_root = workspace.directory(manifest_relative).parent / "package"
        output_name = f"{workspace.source_file.stem}.translated.epub"
        output_path = workspace.directory("output") / output_name
        report = (
            compile_rtf_document(manifest, repaired, output_path)
            if manifest.source_format == "rtf"
            else compile_epub_package(
                package_root,
                manifest,
                repaired,
                output_path,
                strip_print_page_markers=(
                    config.epub.strip_print_page_markers
                ),
                insert_missing_chapter_headings=(
                    config.epub.insert_missing_chapter_headings
                ),
                chapter_heading_labels=config.epub.chapter_heading_labels,
            )
        )
        report_path = workspace.directory("reports") / f"compile-{input_hash[:16]}.json"
        atomic_write_text(report_path, report.model_dump_json(indent=2))
        _record_file(connection, workspace, output_path, "compiled_epub")
        _record_file(connection, workspace, report_path, "epub_compilation_report")
        set_job_metadata(
            connection,
            "compiled_document",
            output_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "document_compilation_report",
            report_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "compiled_epub",
            output_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "epub_compilation_report",
            report_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "compiled_unresolved_review_segments",
            "\n".join(validation_report.review_segment_ids),
        )
        output_hash = build_stage_output_hash(connection, WorkflowStage.COMPILE)
        set_stage_status(
            connection,
            WorkflowStage.COMPILE.value,
            StageStatus.COMPLETED,
            attempts=attempts,
            input_hash=input_hash,
            output_hash=output_hash,
        )
        return report
    except Exception as error:
        _mark_failed(connection, error)
        raise
    finally:
        connection.close()


def load_epub_compilation_report(
    workspace: JobWorkspace,
    *,
    connection=None,
) -> EpubCompilationReport:
    """Load the published compilation report (legacy-compatible name)."""
    owns_connection = connection is None
    active = connection or connect_state(workspace.state_file)
    try:
        relative = get_job_metadata(active, "document_compilation_report") or get_job_metadata(
            active, "epub_compilation_report"
        )
        if not relative:
            raise FileNotFoundError("document compilation report is not recorded")
        path = workspace.directory(relative)
        return EpubCompilationReport.model_validate_json(path.read_text(encoding="utf-8"))
    finally:
        if owns_connection:
            active.close()


def load_compiled_epub_path(workspace: JobWorkspace) -> str:
    """Return the compiled document path (legacy-compatible name)."""
    connection = connect_state(workspace.state_file)
    try:
        relative = get_job_metadata(connection, "compiled_document") or get_job_metadata(
            connection, "compiled_epub"
        )
        if not relative:
            raise FileNotFoundError("compiled document is not recorded")
        path = workspace.directory(relative)
        if not path.is_file():
            raise FileNotFoundError(path)
        return str(path)
    finally:
        connection.close()


def run_document_compile_stage(
    workspace: JobWorkspace,
    config: AppConfig,
) -> EpubCompilationReport:
    """Format-neutral alias for the historical EPUB-named stage API."""
    return run_epub_compile_stage(workspace, config)


def load_document_compilation_report(
    workspace: JobWorkspace,
    *,
    connection=None,
) -> EpubCompilationReport:
    """Load the compiled EPUB report for either supported source format."""
    return load_epub_compilation_report(workspace, connection=connection)


def load_compiled_document_path(workspace: JobWorkspace) -> str:
    """Return the recorded compiled EPUB path."""
    return load_compiled_epub_path(workspace)


def write_unresolved_review_report(
    workspace: JobWorkspace,
    unresolved_ids: list[str] | None = None,
    compile_limit: int | None = None,
    *,
    connection=None,
    validation_report=None,
) -> str:
    """Publish a consolidated report and fail-closed editable verdict worksheet."""
    owns_connection = connection is None
    active = connection or connect_state(workspace.state_file)
    try:
        validation_report = validation_report or load_repaired_validation_report(
            workspace, connection=active
        )
        if unresolved_ids is None:
            unresolved_ids = validation_report.review_segment_ids
        if compile_limit is None:
            config = AppConfig.model_validate_json(
                workspace.config_file.read_text(encoding="utf-8")
            )
            compile_limit = config.workflow.compile_max_unresolved_review_segments
        return _write_unresolved_review_report(
            active,
            workspace,
            unresolved_ids,
            compile_limit,
            validation_report,
        )
    finally:
        if owns_connection:
            active.close()


def _write_unresolved_review_report(
    connection,
    workspace: JobWorkspace,
    unresolved_ids: list[str],
    compile_limit: int,
    validation_report,
) -> str:
    """Build all final-review evidence without requiring reviewers to chase artifacts."""
    unresolved = set(unresolved_ids)
    defect_ids = set(validation_report.defect_segment_ids) & unresolved
    approval_ids = set(validation_report.approval_segment_ids) & unresolved
    # Reports produced before the classified schema are conservatively treated
    # as defects so old workspaces remain reviewable.
    if unresolved and not defect_ids and not approval_ids:
        defect_ids = set(unresolved)
    manifest = load_decompile_manifest(workspace, connection=connection)
    sources = {
        item.manifest_id: item for item in load_preprocessed_documents(workspace)
    }
    repaired_documents = {
        item.document.manifest_id: item
        for item in load_validated_repaired_documents(workspace)
    }
    validations = {
        item.document_id: item
        for item in load_repaired_document_validations(workspace)
    }
    chapters = {item.manifest_id: item for item in manifest.documents}
    raw_documents = _documents_by_manifest(_safe_load(load_translated_documents, workspace))
    repaired_stage_documents = _repaired_documents_by_manifest(
        _safe_load(load_repaired_documents, workspace)
    )
    reprosed_documents = _repaired_documents_by_manifest(
        _safe_load(load_reprosed_documents, workspace)
    )
    initial_audits = {
        item.document_id: item for item in _safe_load(load_document_audits, workspace)
    }
    repaired_review_results = _safe_load(load_repaired_review_results, workspace) or {}

    segments = []
    for document_id, source in sorted(sources.items(), key=lambda item: item[1].order):
        repaired = repaired_documents.get(document_id)
        validation = validations.get(document_id)
        if repaired is None:
            continue
        source_by_id = {item.segment_id: item for item in source.segments}
        target_by_id = {
            item.segment_id: item for item in repaired.document.segments
        }
        repair_by_id = {item.segment_id: item for item in repaired.repairs}
        source_segments = list(source.segments)
        source_index = {
            item.segment_id: index for index, item in enumerate(source_segments)
        }
        raw_by_id = _segments_by_id(raw_documents.get(document_id))
        repaired_stage_by_id = _segments_by_id(
            repaired_stage_documents.get(document_id)
        )
        reprosed_by_id = _segments_by_id(reprosed_documents.get(document_id))
        initial_audit = initial_audits.get(document_id)
        review_result = repaired_review_results.get(document_id)
        chapter = chapters.get(document_id)
        for segment_id in sorted(unresolved & set(source_by_id)):
            source_segment = source_by_id[segment_id]
            target_segment = target_by_id.get(segment_id)
            repair = repair_by_id.get(segment_id)
            findings = []

            if initial_audit is not None:
                for issue in initial_audit.issues:
                    if issue.segment_id == segment_id:
                        findings.append(_audit_finding("initial_translation_audit", issue))
                for comparison in initial_audit.quantity_comparisons:
                    if (
                        comparison.segment_id == segment_id
                        and comparison.status != "match"
                    ):
                        findings.append(_quantity_finding("initial_quantity_audit", comparison))

            if repair is not None:
                for issue in repair.issues:
                    findings.append(_audit_finding("repair_history", issue))
                if repair.message:
                    findings.append(
                        {
                            "origin": "repair",
                            "category": "repair",
                            "severity": "high",
                            "message": repair.message,
                            "suggested_fix": "",
                        }
                    )

            if validation is not None:
                for issue in validation.deterministic_audit.issues:
                    if issue.segment_id == segment_id:
                        findings.append(_audit_finding("final_deterministic_audit", issue))
                for verification in validation.semantic_verifications:
                    if verification.segment_id == segment_id:
                        findings.append(
                            {
                                "origin": "final_semantic_verification",
                                "category": "verification",
                                "severity": "high" if not verification.passed else "info",
                                "message": verification.message,
                                "suggested_fix": "",
                            }
                        )
                for comparison in validation.deterministic_audit.quantity_comparisons:
                    if (
                        comparison.segment_id == segment_id
                        and comparison.status != "match"
                    ):
                        findings.append(_quantity_finding("final_quantity_audit", comparison))

            verifier_history = []
            if review_result is not None:
                verifier_history = [
                    {
                        "passed": item.passed,
                        "current_acceptable": item.current_acceptable,
                        "message": item.message,
                    }
                    for item in review_result.verifications
                    if item.segment_id == segment_id
                ]

            findings = _deduplicate_findings(findings)
            review_kind = (
                "approval_required" if segment_id in approval_ids else "defect"
            )
            suggestions = list(
                dict.fromkeys(
                    item["suggested_fix"]
                    for item in findings
                    if item["suggested_fix"].strip()
                )
            )
            if review_kind == "approval_required":
                suggestions = [
                    "Confirm that the source-grounded high-risk repair is correct, or "
                    "provide a replacement translation. No reproduced defect remains."
                ]
            elif not suggestions:
                reasons = "; ".join(
                    item["message"] for item in findings if item["message"].strip()
                )
                suggestions = [
                    "Manually revise the current translation to resolve: "
                    + (reasons or "the unresolved validation finding")
                ]

            index = source_index[segment_id]
            previous_source = (
                source_segments[index - 1].original_text if index > 0 else ""
            )
            next_source = (
                source_segments[index + 1].original_text
                if index + 1 < len(source_segments)
                else ""
            )
            versions = _translation_versions(
                raw_by_id.get(segment_id),
                repaired_stage_by_id.get(segment_id),
                reprosed_by_id.get(segment_id),
                target_segment,
            )
            repair_history = []
            if repair is not None:
                repair_history.append(
                    {
                        "disposition": repair.disposition.value,
                        "original_translation": repair.original_translation,
                        "repaired_translation": repair.repaired_translation,
                        "attempts": repair.attempts,
                        "message": repair.message,
                        "issues": [
                            _audit_finding("repair_history", issue)
                            for issue in repair.issues
                        ],
                    }
                )

            segments.append(
                {
                    "chapter_order": source.order,
                    "chapter_title": chapter.title if chapter is not None else "",
                    "document_id": document_id,
                    "archive_path": source.archive_path,
                    "segment_id": segment_id,
                    "review_kind": review_kind,
                    "source_text": source_segment.original_text,
                    "processed_source_text": source_segment.processed_text,
                    "previous_source_context": previous_source,
                    "next_source_context": next_source,
                    "current_translation": (
                        target_segment.translated_text if target_segment is not None else ""
                    ),
                    "repair_disposition": repair.disposition.value if repair is not None else "",
                    "translation_versions": versions,
                    "repair_history": repair_history,
                    "verifier_history": verifier_history,
                    "findings": findings,
                    "fix_suggestions": suggestions,
                }
            )

    report = {
        "schema_version": 4,
        "workspace": str(workspace.root),
        "unresolved_count": len(unresolved_ids),
        "unresolved_defect_count": len(defect_ids),
        "approval_required_count": len(approval_ids),
        "defect_segment_ids": sorted(defect_ids),
        "approval_segment_ids": sorted(approval_ids),
        "compile_limit": compile_limit,
        "segments": segments,
        "recurring_terminology_findings": _recurring_terminology_findings(
            segments
        ),
    }
    reports_root = workspace.directory("reports")
    json_path = reports_root / "unresolved-review-segments.json"
    markdown_path = reports_root / "unresolved-review-segments.md"
    worksheet_path = reports_root / "final-human-review.decisions.json"
    atomic_write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(markdown_path, _render_manual_review_markdown(report))
    validation_stage = get_stage_status(
        connection, WorkflowStage.VALIDATE_REPAIRED.value
    )
    draft_output_hash = str(validation_stage["output_hash"]) if validation_stage else ""
    worksheet = ManualReviewWorksheet(
        workspace=str(workspace.root),
        draft_output_hash=draft_output_hash,
        instructions=[
            "Review each case against source and immediate context.",
            "Change every decision from 'pending' to 'accept' or 'replace'.",
            "For 'replace', provide translated_text or bounded replacements.",
            "Give a concise source-grounded reason for every completed decision.",
            "Run resolve-review with this file; stale or pending worksheets are refused.",
        ],
        resolutions=[
            ManualReviewWorksheetResolution(
                segment_id=item["segment_id"],
                source_text=item["source_text"],
                current_translation=item["current_translation"],
                findings=[finding["message"] for finding in item["findings"]],
                suggested_fixes=item["fix_suggestions"],
                decision=ManualReviewDecision.PENDING,
            )
            for item in segments
        ],
    )
    atomic_write_text(worksheet_path, worksheet.model_dump_json(indent=2) + "\n")
    _record_file(connection, workspace, json_path, "unresolved_review_segments_json")
    _record_file(connection, workspace, markdown_path, "unresolved_review_segments_markdown")
    _record_file(connection, workspace, worksheet_path, "manual_review_worksheet")
    relative = markdown_path.relative_to(workspace.root).as_posix()
    set_job_metadata(connection, "unresolved_review_segments_report", relative)
    set_job_metadata(
        connection,
        "manual_review_worksheet",
        worksheet_path.relative_to(workspace.root).as_posix(),
    )
    return relative


def _audit_finding(origin, issue) -> dict[str, str]:
    return {
        "origin": origin,
        "category": issue.category.value,
        "severity": issue.severity.value,
        "message": issue.message,
        "suggested_fix": issue.suggested_fix,
        "source_quote": issue.source_quote,
        "translation_quote": issue.translation_quote,
    }


def _quantity_finding(origin, comparison) -> dict[str, str]:
    mismatch_messages = [item.message for item in comparison.mismatches]
    detail = "; ".join(mismatch_messages) or comparison.reason
    message = f"quantity comparison: {comparison.status}"
    if detail:
        message += f" — {detail}"
    return {
        "origin": origin,
        "category": "quantity",
        "severity": "high" if comparison.status == "mismatch" else "info",
        "message": message,
        "suggested_fix": "",
        "source_quote": "",
        "translation_quote": "",
    }


def _recurring_terminology_findings(segments: list[dict]) -> list[dict[str, object]]:
    """Surface repeated terminology diagnoses without mutating approved glossaries."""
    grouped: dict[str, dict[str, object]] = {}
    for segment in segments:
        for finding in segment["findings"]:
            if finding["category"] != "terminology" and not finding.get(
                "source_quote", ""
            ).strip():
                continue
            source_quote = finding.get("source_quote", "").strip()
            key = source_quote.casefold() or " ".join(
                finding["message"].casefold().split()
            )
            item = grouped.setdefault(
                key,
                {
                    "source_quote": source_quote,
                    "messages": [],
                    "segment_ids": [],
                    "translation_quotes": [],
                    "recommended_action": (
                        "Review once as a possible book/series glossary correction, then "
                        "verify every listed occurrence in context."
                    ),
                },
            )
            item["messages"].append(finding["message"])
            item["segment_ids"].append(segment["segment_id"])
            if finding.get("translation_quote"):
                item["translation_quotes"].append(finding["translation_quote"])
    result = []
    for item in grouped.values():
        ids = list(dict.fromkeys(item["segment_ids"]))
        if len(ids) < 2:
            continue
        result.append(
            {
                **item,
                "messages": list(dict.fromkeys(item["messages"])),
                "segment_ids": ids,
                "translation_quotes": list(
                    dict.fromkeys(item["translation_quotes"])
                ),
                "occurrence_count": len(ids),
            }
        )
    return sorted(
        result,
        key=lambda item: (-int(item["occurrence_count"]), str(item["source_quote"])),
    )


def _safe_load(loader, workspace):
    try:
        return loader(workspace)
    except (FileNotFoundError, RuntimeError, ValueError):
        return []


def _documents_by_manifest(documents) -> dict:
    return {item.manifest_id: item for item in documents or []}


def _repaired_documents_by_manifest(documents) -> dict:
    return {item.document.manifest_id: item.document for item in documents or []}


def _segments_by_id(document) -> dict:
    if document is None:
        return {}
    return {item.segment_id: item for item in document.segments}


def _translation_versions(raw, repaired, reprosed, current) -> list[dict[str, str]]:
    stages = (
        ("raw_translation", raw),
        ("semantic_repair", repaired),
        ("prose_rewrite_candidate", reprosed),
        ("current_validated_draft", current),
    )
    versions: list[dict[str, str]] = []
    for stage, segment in stages:
        if segment is None:
            continue
        text = segment.translated_text
        if versions and versions[-1]["text"] == text:
            versions[-1]["stage"] += f", {stage}"
        else:
            versions.append({"stage": stage, "text": text})
    return versions


def _deduplicate_findings(findings: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    result = []
    for finding in findings:
        key = tuple(finding.values())
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result


def _render_manual_review_markdown(report: dict) -> str:
    lines = [
        "# Unresolved translation review segments — final human-review worksheet",
        "",
        f"Unresolved segments: {report['unresolved_count']}",
        f"Concrete unresolved defects: {report.get('unresolved_defect_count', report['unresolved_count'])}",
        f"Approval-only segments: {report.get('approval_required_count', 0)}",
        f"Configured compile limit: {report['compile_limit']}",
        "",
        "This report consolidates source context, translation versions, repair history, "
        "and all recorded validation findings.",
        "",
        "Complete the companion `final-human-review.decisions.json` file. Change every "
        "`decision` from `pending` to `accept` or `replace`, provide a reason, and for "
        "replacements supply either the complete `translated_text` or exact bounded "
        "`replacements`.",
        "",
        "Apply and approve the completed worksheet with:",
        "",
        "```powershell",
        "python -m book_agent.cli resolve-review `",
        f"    \"{report.get('workspace', '')}\" `",
        f"    \"{report.get('workspace', '')}\\reports\\final-human-review.decisions.json\" `",
        "    --approve-final --resume",
        "```",
        "",
    ]
    if not report["segments"]:
        lines.append("No unresolved review segments.")
        return "\n".join(lines) + "\n"

    recurring = report.get("recurring_terminology_findings", [])
    if recurring:
        lines.extend(
            [
                "## Recurring terminology candidates",
                "",
                "These are review suggestions only; the approved glossary is not changed automatically.",
                "",
            ]
        )
        for item in recurring:
            label = item["source_quote"] or item["messages"][0]
            lines.append(
                f"- `{label}` — {item['occurrence_count']} occurrences: "
                + ", ".join(f"`{value}`" for value in item["segment_ids"])
            )
        lines.append("")

    for index, segment in enumerate(report["segments"], start=1):
        title = segment["chapter_title"] or segment["document_id"]
        lines.extend(
            [
                f"## {index}. Chapter {segment['chapter_order']}: {title}",
                "",
                f"- Segment: `{segment['segment_id']}`",
                f"- Review kind: `{segment.get('review_kind', 'defect')}`",
                f"- Document: `{segment['document_id']}`",
                f"- Source path: `{segment['archive_path']}`",
                f"- Repair disposition: `{segment['repair_disposition'] or 'not recorded'}`",
                "",
                "### Previous source context",
                "",
                *_indented_text(
                    segment["previous_source_context"] or "(start of document)"
                ),
                "",
                "### Source",
                "",
                *_indented_text(segment["source_text"]),
                "",
                "### Current translation",
                "",
                *_indented_text(segment["current_translation"]),
                "",
                "### Next source context",
                "",
                *_indented_text(segment["next_source_context"] or "(end of document)"),
                "",
                "### Translation history",
                "",
            ]
        )
        for version in segment["translation_versions"]:
            lines.extend(
                [
                    f"#### {version['stage']}",
                    "",
                    *_indented_text(version["text"]),
                    "",
                ]
            )
        lines.extend(["### Findings", ""])
        for finding in segment["findings"]:
            lines.append(
                f"- [{finding['origin']} / {finding['category']} / "
                f"{finding['severity']}] {finding['message']}"
            )
            if finding["suggested_fix"]:
                lines.append(f"  - Suggested fix: {finding['suggested_fix']}")
        if not segment["findings"]:
            lines.append("- No detailed finding was recorded.")
        if segment["verifier_history"]:
            lines.extend(["", "### Repair/rewrite verifier history", ""])
            for verdict in segment["verifier_history"]:
                lines.append(
                    "- passed="
                    f"`{str(verdict['passed']).lower()}`, current acceptable="
                    f"`{str(verdict['current_acceptable']).lower()}` — {verdict['message']}"
                )
        lines.extend(["", "### Manual fix suggestions", ""])
        lines.extend(f"- {suggestion}" for suggestion in segment["fix_suggestions"])
        lines.extend(
            [
                "",
                "### Reviewer verdict",
                "",
                "Record this verdict in the companion decisions JSON file.",
                "",
                "- Decision: `pending`",
                "- Reason: *(required for accept or replace)*",
                "- Approved translation: *(required only for replace)*",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _indented_text(value: str) -> list[str]:
    lines = value.splitlines() or [""]
    return [f"    {line}" for line in lines]


def _record_file(connection, workspace, path, kind) -> None:
    record_artifact(
        connection,
        path.relative_to(workspace.root).as_posix(),
        WorkflowStage.COMPILE.value,
        kind,
        sha256_file(path),
        path.stat().st_size,
    )


def _require_completed(connection, stage):
    record = get_stage_status(connection, stage.value)
    if record is None or record["status"] != StageStatus.COMPLETED.value:
        raise RuntimeError(f"required stage is not complete: {stage.value}")
    return record


def _mark_failed(connection, error) -> None:
    previous = get_stage_status(connection, WorkflowStage.COMPILE.value)
    if previous is None or previous["status"] == StageStatus.COMPLETED.value:
        return
    set_stage_status(
        connection,
        WorkflowStage.COMPILE.value,
        StageStatus.FAILED,
        attempts=int(previous["attempts"]),
        message=str(error),
        input_hash=str(previous["input_hash"]),
    )
