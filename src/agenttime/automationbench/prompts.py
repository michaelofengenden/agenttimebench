"""Versioned, dependency-free adaptation of the public task instruction."""

from copy import deepcopy
import hashlib
import json

from . import VARIANT

TURN_BUDGET = "You have a budget of ~50 tool-using turns — favor parallel tool calls and avoid duplicate searches. "


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def adapt_prompt(messages):
    if not isinstance(messages, list) or not messages:
        raise ValueError("Expected native prompt messages")
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] not in {"system", "user"}
            or not isinstance(message["content"], str)
        ):
            raise ValueError("Unsupported native prompt shape")
    matches = [i for i, m in enumerate(messages) if TURN_BUDGET in m["content"]]
    if (
        len(matches) != 1
        or messages[matches[0]]["role"] != "system"
        or sum(m["content"].count(TURN_BUDGET) for m in messages) != 1
    ):
        raise ValueError("Expected exactly one versioned native turn-budget clause")
    adapted = deepcopy(messages)
    adapted[matches[0]]["content"] = adapted[matches[0]]["content"].replace(
        TURN_BUDGET, "", 1
    )
    # Native harnesses retain their own system prompt. Benchmark instructions become a clearly delimited task.
    text = "\n\n".join(
        ("Benchmark instructions:\n" if m["role"] == "system" else "Task:\n")
        + m["content"]
        for m in adapted
    )
    return {
        "variant": VARIANT,
        "messages": adapted,
        "text": text,
        "source_messages_sha256": digest(messages),
        "adapted_messages_sha256": digest(adapted),
        "delivered_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "turn_limit": None,
        "experiment_timeout_seconds": None,
    }
