"""Temporal-cue scrubber for the `scrubbed` replay arm (frozen regex classes, applied to all message text).

Each match's captured group is replaced by a same-length placeholder such as `<CLOCK_REDACTED>____`
(truncated when the match is shorter), so message lengths do not change. Rules run in the order listed.
The classes cover the cue types in the paper's Timestamps section: absolute timestamps (ISO 8601, epochs,
clock times, `ls -l` dates) and elapsed durations from tool output (`took 3s`, `in 0.52s`, `real 0m1.2s`).
"""
import re

ISO_8601 = re.compile(
    r"(?<!\d)"
    r"(\d{4}-\d{2}-\d{2}"
    r"(?:[Tt ][0-2]\d:[0-5]\d:[0-6]\d"
    r"(?:[.,]\d+)?(?:Z|[+-][0-2]\d:?[0-5]\d)?)?)"
    r"(?!\d)"
)
UNIX_EPOCH_AFTER_CONTEXT = re.compile(
    r"(?i)\b(?:unix(?:\s+epoch)?|epoch|timestamp|"
    r"time_?(?:ms|seconds)?)\s*[:=]?\s*(\d{10}|\d{13})\b"
)
UNIX_EPOCH_BEFORE_CONTEXT = re.compile(
    r"(?i)\b(\d{10}|\d{13})\s*(?:unix(?:\s+epoch)?|epoch|timestamp)\b"
)
CLOCK_TIME = re.compile(
    r"(?<!\d)((?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:[.,]\d+)?)(?!\d)"
)
EXPLICIT_ELAPSED = re.compile(
    r"(?ix)\b"
    r"(?:took|elapsed(?:\s+time)?|duration(?:\s+was|\s+of)?|completed\s+in)"
    r"\s*(?:[:=]\s*)?"
    r"("
    r"\d+(?:\.\d+)?\s*"
    r"(?:milliseconds?|msecs?|ms|seconds?|secs?|s|minutes?|mins?|m|"
    r"hours?|hrs?|h)"
    r")\b"
)
LS_STYLE_DATE = re.compile(
    r"(?x)\b"
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+"
    r"(?:[0-2]?\d:[0-5]\d|\d{4}))\b"
)
STAT_EPOCH = re.compile(
    r"(?i)\b(?:st_)?[mac]time\w*\s*[:=]?\s*(\d{10}|\d{13})\b"
)
DURATION_IN = re.compile(
    r"(?ix)\bin\s+"
    r"(\d+(?:\.\d+)?\s*"
    r"(?:milliseconds?|msecs?|ms|seconds?|secs?|s|minutes?|mins?|m))"
    r"(?![\w.])"
)
TIME_CMD_OUTPUT = re.compile(
    r"(?ix)\b(?:real|user|sys)\s+(\d+m\d+(?:\.\d+)?s)\b"
)
BARE_DECIMAL_SECONDS = re.compile(
    r"(?<![\w.\d])(\d+\.\d+\s?s)(?![\w])"
)

RULES = (
    ("iso8601", ISO_8601),
    ("unix_epoch", UNIX_EPOCH_AFTER_CONTEXT),
    ("unix_epoch", UNIX_EPOCH_BEFORE_CONTEXT),
    ("unix_epoch", STAT_EPOCH),
    ("clock", CLOCK_TIME),
    ("ls_date", LS_STYLE_DATE),
    ("elapsed", EXPLICIT_ELAPSED),
    ("duration", DURATION_IN),
    ("duration", TIME_CMD_OUTPUT),
    ("duration", BARE_DECIMAL_SECONDS),
)


def _placeholder(kind: str, length: int) -> str:
    token = f"<{kind.upper()}_REDACTED>"
    return token[:length] if len(token) >= length else token + "_" * (length - len(token))


def scrub_text(text: str) -> str:
    """Redact every frozen class in order."""
    for kind, pattern in RULES:
        def replace(match, kind=kind):
            start, end = match.start(1) - match.start(), match.end(1) - match.start()
            whole = match.group(0)
            return whole[:start] + _placeholder(kind, end - start) + whole[end:]
        text = pattern.sub(replace, text)
    return text


def scrub_messages(messages: list[dict]) -> list[dict]:
    """Scrub the content of every message (roles untouched)."""
    return [{**message, "content": scrub_text(message["content"])} for message in messages]
