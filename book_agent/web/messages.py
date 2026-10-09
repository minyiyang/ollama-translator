"""Dashboard messages the user acts on, as a code and values beside the English text.

The dashboard translates a code it knows (``server.error.<code>`` in the interface
catalog, ``frontend/src/i18n``) and shows the English text otherwise. The CLI
and the logs stay English. See docs/LOCALIZATION.md, section 2.5.
"""

from __future__ import annotations

from typing import Any

from ..subtitles import JOB_SOURCE_NAMES
from ..workflow import PAUSED_ON_REQUEST

# Code -> English text, with ``{name}`` for each value. The dashboard's English
# catalog holds the same text under ``server.error.<code>`` (tests/test_web_messages.py).
MESSAGES: dict[str, str] = {
    "already_running": "{label} is already running for this job",
    "approve_nothing": "submit reviewed entries, request LLM review, or both",
    "config_name_invalid": "config name must be a simple .yaml/.yml file name",
    "discard_started_job": "only a job that has not started can be discarded",
    "glossary_empty": "the reviewed glossary is empty; keep at least one entry",
    "import_while_running": "the job is running; wait for it to finish or pause it before importing",
    "job_already_started": "this job has already started; use Resume",
    "job_exists": "a job named {job} already exists",
    "job_not_complete": "the job has not completed; there is no translated book yet",
    "job_unknown": "no job or draft named {job}",
    "jobs_none_chosen": "choose at least one job",
    "no_candidate": "no candidate yet; build it on the Books tab",
    "no_dashboard_run": "no run started from this dashboard is active; use Pause instead",
    "rerun_while_running": "the job is running; pause or stop it before rerunning a stage",
    "series_no_version": "series {series} has no version {version}",
    "source_not_found": "source file not found: {path}",
    "source_unsupported": f"source must be {JOB_SOURCE_NAMES}",
    "stage_not_rerunnable": "only a completed, failed, or paused stage can be rerun; {stage} is {status}",
    "stage_unknown": "unknown workflow stage: {stage}",
    "stage_waiting_review": "{stage} is waiting for your review; finish it on its tab instead",
    "suggestions_none_chosen": "choose at least one suggestion",
    "upload_incomplete": "upload ended early",
    "upload_size": "file is empty or larger than 1 GB",
    "upload_unsupported": f"only {JOB_SOURCE_NAMES} can be uploaded",
    "validate_first": "validate the configuration on the Config tab first",
    "validate_started_job": "only a job that has not started can be validated",
    "xliff_needs_draft": "XLIFF export needs the validated draft; wait for Validate draft to complete",
}

# What a stage says of itself in the job's state, as the stages write it. The
# dashboard recognizes these by their English text (``server.stage.<code>``).
STAGE_MESSAGES: dict[str, str] = {
    "paused_on_request": PAUSED_ON_REQUEST,
    "review_required": "{count} segment(s) require human review",
    "glossary_review_required": "human glossary review required",
    "style_sheet_review_required": "style sheet review required",
}

# What a rerun costs, by the code the rerun preview lists it under (``server.rerun.<code>``).
RERUN_WARNINGS: dict[str, str] = {
    "glossary_approval": "The glossary must be reviewed and approved again; the run pauses at that gate.",
    "full_translation": "The whole book is translated again. This is the most expensive rerun.",
    "manual_review": (
        "The final-draft approval and any unapplied Final review decisions are "
        "discarded, and the review queue is rebuilt from the new draft. Applied "
        "decisions are kept as Text tab edits."
    ),
    "compiled_epub": "The compiled book is replaced by the new output.",
    "text_edits": "Manual text edits are kept; segments whose translation changes become conflicts.",
}


def rerun_warning(code: str) -> dict[str, str]:
    return {"code": code, "message": RERUN_WARNINGS[code]}


class UserError(ValueError):
    """A refusal the user can act on. ``str()`` is the English text."""

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = {name: str(value) for name, value in params.items()}
        super().__init__(MESSAGES[code].format(**self.params))


def error_payload(error: BaseException | str) -> dict[str, Any]:
    """The JSON body for an error: its English text, and its code and values when it has them."""
    payload: dict[str, Any] = {"error": str(error)}
    if isinstance(error, UserError):
        payload["code"] = error.code
        payload["params"] = error.params
    return payload
