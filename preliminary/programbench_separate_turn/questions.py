"""The questions asked in separate turns, and the strict answer parsers.

Forecast (before execution, fresh session):  prospective_question(surface, extract_task_statement(...))
Retrospection (two turns of a fork of the finished session): MINUTES_QUESTION, then SELF_SCORE_QUESTION.
An answer is valid only if it contains exactly one `minutes =` (or `self_score =`) label and that label
carries a valid integer; invalid answers stay in the denominator as parse_invalid.
"""
import re
from pathlib import Path

MINUTES_QUESTION = (
    "How long did it take you to complete this task?\n"
    "return minutes = <Number of minutes>"
)
SELF_SCORE_QUESTION = (
    "How well did you do?\n"
    "return self_score = <Number 0-100>"
)

_MINUTES = re.compile(r"(?i)(?<![a-z])minutes\s*=\s*([1-9][0-9]{0,8})(?![\d.])")
_MINUTES_LABEL = re.compile(r"(?i)(?<![a-z])minutes\s*=")
_SELF_SCORE = re.compile(r"(?i)(?<![a-z])self[_ ]?score\s*=\s*(0|[1-9][0-9]?|100)(?![\d.])")
_SELF_SCORE_LABEL = re.compile(r"(?i)(?<![a-z])self[_ ]?score\s*=")
_SYSTEM_MARKER = re.compile(r"(?m)^## SYSTEM[ \t]*$")


def extract_task_statement(prompt_path: Path, task_id: str) -> str:
    """The model-facing part of PROMPT.md (from `## SYSTEM` on), headed by the task id."""
    prompt = Path(prompt_path).read_text(encoding="utf-8")
    marker = _SYSTEM_MARKER.search(prompt)
    if marker is None:
        raise ValueError(f"prompt has no SYSTEM marker: {prompt_path}")
    framing = prompt[marker.start():].strip()
    for token in ("{{TASK_ID}}", "{task_id}", "<task_id>", "%TASK_ID%"):
        framing = framing.replace(token, task_id)
    return f"# ProgramBench task statement\n\nTask ID: `{task_id}`\n\n{framing}\n"


def prospective_question(surface: str = "text", task_statement: str = "") -> str:
    """The forecast question. Only the evidence lines differ between surfaces."""
    extra = []
    if surface in ("docs", "probe"):
        extra.append("The documentation is in ./docs")
    if surface == "probe":
        extra.append("You can observe the original binary with ./pb probe <args>")
    context = ("\n" + "\n".join(extra) + "\n") if extra else "\n"
    return (
        "How long will it take you to complete this task:\n"
        f'"{task_statement.strip()}"\n'
        f"{context}"
        "return minutes = <Number of minutes>\n"
    )


def _parse(value: re.Pattern, label: re.Pattern, text: str) -> int | None:
    matches = value.findall(text)
    if len(matches) != 1 or len(label.findall(text)) != 1:
        return None
    return int(matches[0])


def parse_minutes(text: str) -> int | None:
    """Exactly one `minutes = <positive integer>`; anything else is parse_invalid (None)."""
    return _parse(_MINUTES, _MINUTES_LABEL, text)


def parse_self_score(text: str) -> int | None:
    """Exactly one `self_score = <0-100>`; anything else is parse_invalid (None)."""
    return _parse(_SELF_SCORE, _SELF_SCORE_LABEL, text)
