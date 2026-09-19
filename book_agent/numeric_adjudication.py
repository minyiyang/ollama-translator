"""LLM rulings on segments that fail the rule-based numeric check.

The token check in :func:`book_agent.audit._audit_number_integrity` is cheap but
cannot tell ``two parts and a half`` -> ``两份半`` from a real change.  It is
therefore only a trigger: each failing segment is judged by the quantity model,
and the ruling, not the rule, decides whether the finding stands.

Rulings are keyed by the exact source and translation text and stored in the job
workspace, so a later recomputation of the audit (repair, final validation,
manual review) reuses them and a changed translation is judged again.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .atomic_io import atomic_write_text
from .ollama_client import StructuredOutputError
from .quantities import (
    QuantityAuditResult,
    build_quantity_audit_prompt,
    validate_quantity_audit_scope,
)
from .stage_progress import llm_role_kwargs, request_context_bucket

NUMBER_RULE_SOURCE = "number-rule-deterministic"
VERDICTS_FILE = "audit/numeric-rulings.json"


class NumericRuling(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str
    status: Literal["match", "mismatch", "uncertain"]
    message: str = ""
    model: str = ""


def ruling_key(source_text: str, target_text: str) -> str:
    """Identify a ruling by the exact texts it judged."""
    payload = json.dumps([source_text.strip(), target_text.strip()], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _path(workspace) -> Path:
    return Path(workspace.root) / VERDICTS_FILE


def load_numeric_rulings(workspace) -> dict[str, NumericRuling]:
    """Return the stored rulings; a missing or unreadable file yields none."""
    path = _path(workspace)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rulings: dict[str, NumericRuling] = {}
    for key, value in raw.items() if isinstance(raw, dict) else ():
        try:
            rulings[key] = NumericRuling.model_validate(value)
        except ValueError:
            continue
    return rulings


def save_numeric_rulings(workspace, rulings: Mapping[str, NumericRuling]) -> None:
    path = _path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(
            {key: value.model_dump() for key, value in sorted(rulings.items())},
            ensure_ascii=False,
            indent=1,
        ),
    )


def apply_numeric_rulings(issues, texts: Mapping[str, tuple[str, str]], rulings):
    """Settle rule findings that have a ruling for the current text.

    ``match`` drops the finding; ``mismatch`` keeps it with the model's reason;
    ``uncertain`` or no ruling keeps the rule finding (fail closed).
    """
    if not rulings:
        return list(issues)
    settled = []
    for issue in issues:
        if issue.source != NUMBER_RULE_SOURCE or issue.segment_id not in texts:
            settled.append(issue)
            continue
        ruling = rulings.get(ruling_key(*texts[issue.segment_id]))
        if ruling is None or ruling.status == "uncertain":
            settled.append(issue)
        elif ruling.status == "mismatch":
            settled.append(
                issue.model_copy(
                    update={"message": f"numeric content differs from source: {ruling.message}"}
                )
            )
    return settled


def _validated_decision(result, segment_id, source_text, target_text):
    """Check a ruling strictly only where it would clear a finding.

    A ``mismatch`` keeps the segment blocked either way, so a missing mismatch
    type or a loosely quoted excerpt does not void it; any other status must pass
    the full grounding checks.
    """
    if len(result.decisions) != 1 or result.decisions[0].segment_id != segment_id:
        raise ValueError("quantity audit must return exactly the allowed segment ID")
    decision = result.decisions[0]
    if decision.status == "mismatch":
        return decision
    return validate_quantity_audit_scope(
        result, segment_id, source_text, target_text
    ).decisions[0]


def numeric_rulings_enabled(config) -> bool:
    """Rulings replace the rule verdict only while the typed checker is off."""
    quantity = config.audit.quantity
    return not quantity.enabled and quantity.adjudicate_rule_findings


def rule_numeric_findings(workspace, config, client, documents) -> dict[str, NumericRuling]:
    """Obtain a ruling for every current rule finding and return all rulings.

    ``documents`` holds ``(source_document, translated_document)`` pairs.  Without
    a client only the stored rulings are returned, so new findings fail closed.
    """
    if not numeric_rulings_enabled(config):
        return {}
    rulings = load_numeric_rulings(workspace)
    if client is None:
        return rulings
    from .audit import audit_translated_document

    pending = []
    for source, translated in documents:
        audit = audit_translated_document(source, translated, config.audit, rulings)
        issues = [item for item in audit.issues if item.source == NUMBER_RULE_SOURCE]
        if not issues:
            continue
        sources = {item.segment_id: item.processed_text for item in source.segments}
        texts = {
            item.segment_id: (sources[item.segment_id], item.translated_text)
            for item in translated.segments
            if item.segment_id in sources
        }
        pending.append((source, issues, texts))
    if pending:
        adjudicate_numeric_rule_findings(workspace, config, client, pending)
        rulings = load_numeric_rulings(workspace)
    return rulings


def adjudicate_numeric_rule_findings(
    workspace,
    config,
    client,
    documents,
) -> int:
    """Ask the quantity model about every rule finding without a ruling.

    ``documents`` yields ``(source_document, issues, texts)`` where ``texts`` maps a
    segment ID to its current ``(source_text, target_text)``.  Rulings are saved
    after each call so an interrupted run resumes where it stopped.  Returns the
    number of model calls made.
    """
    quantity = config.audit.quantity
    rulings = load_numeric_rulings(workspace)
    pending: list[tuple[object, str, str, str]] = []
    seen: set[str] = set()
    for source, issues, texts in documents:
        for issue in issues:
            if issue.source != NUMBER_RULE_SOURCE or issue.segment_id not in texts:
                continue
            key = ruling_key(*texts[issue.segment_id])
            if key in rulings or key in seen:
                continue
            seen.add(key)
            pending.append((source, issue.segment_id, *texts[issue.segment_id]))
    calls = 0
    for index, (source, segment_id, source_text, target_text) in enumerate(pending, 1):
        ids = [item.segment_id for item in source.segments]
        position = ids.index(segment_id) if segment_id in ids else -1
        prompt = build_quantity_audit_prompt(
            segment_id,
            source_text,
            target_text,
            None,
            preceding_source=(
                source.segments[position - 1].processed_text if position > 0 else "(none)"
            ),
            following_source=(
                source.segments[position + 1].processed_text
                if 0 <= position < len(ids) - 1
                else "(none)"
            ),
        )
        bucket = request_context_bucket(
            prompt,
            minimum=min(config.ollama.min_num_ctx, quantity.max_num_ctx),
            maximum=quantity.max_num_ctx,
            schema=QuantityAuditResult,
        )
        ruling: NumericRuling | None = None
        error = ""
        for retry in range(1, config.workflow.max_retries + 2):
            request = prompt
            if error:
                request += (
                    "\n\nThe previous structured response was invalid. Return a complete "
                    f"corrected result. Validation error: {error}"
                )
            calls += 1
            try:
                generated = client.generate_structured(
                    request,
                    QuantityAuditResult,
                    model=quantity.model,
                    context_maximum=int(bucket),
                    think=quantity.thinking,
                    progress_label=(
                        f"numeric-ruling={index}/{len(pending)} id={segment_id} "
                        f"model={quantity.model} attempt={retry}/{config.workflow.max_retries + 1}"
                    ),
                    **llm_role_kwargs(client, "audit.quantity.base"),
                    max_output_tokens=1_024,
                    max_attempts=1,
                )
                decision = _validated_decision(
                    generated.value, segment_id, source_text, target_text
                )
            except (StructuredOutputError, ValueError) as exc:
                error = str(exc)
                continue
            status = decision.status
            if status == "match" and decision.confidence < quantity.min_decision_confidence:
                status = "uncertain"
            ruling = NumericRuling(
                segment_id=segment_id,
                status=status,
                message=decision.message,
                model=quantity.model,
            )
            break
        if ruling is None:
            ruling = NumericRuling(
                segment_id=segment_id,
                status="uncertain",
                message=f"no valid ruling: {error}",
                model="fallback",
            )
        rulings[ruling_key(source_text, target_text)] = ruling
        save_numeric_rulings(workspace, rulings)
    return calls
