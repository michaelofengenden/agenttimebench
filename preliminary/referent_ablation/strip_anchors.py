"""Remove run-budget and deadline sentences from a task instruction before elicitation.

Each prompt asks how long a task will take, so any sentence that already states a time budget
("You have a maximum of 8 hours.") is removed first. The rules are applied in order and
then runs of three or more newlines are collapsed. Tasks whose budget is part of the task
itself (KEEP_SOURCES) are left unchanged.

Usage: python strip_anchors.py <source> < instruction.txt > stripped.txt
"""
import re
import sys

STRIP = [
    r'\s*and a \d+-?\s*hour budget',
    r'(?im)^\s*\d+\.\s*You have a maximum of \d+\s*hours?\..*$',
    r'(?im)^.*You have a maximum of [\d,]+ active CPU seconds \(\d+\s*hours?\).*$',
    r'(?im)^.*\bTime budget:\s*\d+\s*s?\s*\(\d+\s*min\)\.?',
    r'(?im)^.*(save|checkpoint|submit).{0,40}every ~?\d+\s*hours?.*$',
    r'(?im)^.*within the contest window.*$',
    r'(?i),?\s*for a maximum runtime of \d+\s*(?:day|hour)s?',
]
# Matched against the safe-id prefix of the source name (see safe_id).
KEEP_SOURCES = {"ProgramBench", "YC-Bench", "Agents_Last_Exam", "OSWorld", "OSWorld_2.0"}


def safe_id(source: str, task_id: str = "") -> str:
    """File-name key used for instruction files and receipts."""
    name = f"{source}__{task_id}" if task_id else source
    return re.sub(r'[^A-Za-z0-9._-]', '_', name)


def strip_anchors(text: str, source: str) -> str:
    if safe_id(source) in KEEP_SOURCES:
        return text
    for pattern in STRIP:
        text = re.sub(pattern, "", text)
    return re.sub(r'\n{3,}', '\n\n', text)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python strip_anchors.py <source> < instruction.txt")
    text = sys.stdin.buffer.read().decode("utf-8")
    sys.stdout.buffer.write(strip_anchors(text, sys.argv[1]).encode("utf-8"))
