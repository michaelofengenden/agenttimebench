"""Session log -> plain-text chat messages, as R-replay and R-scrubbed send them.

The reconstruction the replays used: Claude Code session records and Codex rollout items become chat
messages, and harness roles are mapped to API roles per format. Hidden reasoning (thinking, redacted_thinking,
reasoning items) is never reconstructed; tool calls and results become text with a header line:

    [tool_call name=<name> id=<id>]\\n<arguments as JSON>
    [tool_result id=<id>]\\n<output>
"""
import json
from copy import deepcopy

# Harness role -> API role (roles not listed are kept).
ROLE_MAPS = {
    "claude": {"developer": "user", "system": "user", "tool": "user", "function": "user"},
    "codex": {"developer": "system", "tool": "user", "function": "user"},
}


def _plain(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_blocks(content):
    """(text, tool-result framings) of a message's content."""
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return _plain(content), []
    chunks, framings = [], []
    for block in content:
        if isinstance(block, str):
            chunks.append(block)
            continue
        if not isinstance(block, dict):
            chunks.append(_plain(block))
            continue
        kind = str(block.get("type", "")).lower()
        if kind in {"text", "input_text", "output_text"}:
            chunks.append(str(block.get("text", "")))
        elif kind in {"thinking", "redacted_thinking", "reasoning"}:
            continue                                    # hidden reasoning is never replayed
        elif kind in {"tool_use", "function_call", "custom_tool_call"}:
            name = block.get("name", "unknown")
            identifier = block.get("id", block.get("call_id", ""))
            payload = block.get("input", block.get("arguments", block.get("params", {})))
            chunks.append(f"[tool_call name={name} id={identifier}]\n{_plain(payload)}")
        elif kind in {"tool_result", "function_call_output", "custom_tool_call_output"}:
            identifier = block.get("tool_use_id", block.get("call_id", block.get("id", "")))
            framings.append(str(block.get("framing", f"[tool_result id={identifier}]")))
            result_text, _ = _content_blocks(block.get("content", block.get("output", block.get("result", ""))))
            chunks.append(result_text)
        else:
            chunks.append(_plain(block))
    return "\n".join(chunks), framings


def _compose(role, content, framings=()):
    """A message whose tool-result framing (if any) precedes its content."""
    if not "\n".join(framings):
        return {"role": role, "content": content}
    return {"role": role, "content": "\n".join([*framings, content])}


def claude_messages(records):
    """Claude Code session JSONL records -> messages (user/assistant/system records only)."""
    out = []
    for record in records:
        record_type = str(record.get("type", "")).lower()
        message = record.get("message")
        if not isinstance(message, dict) or record_type not in {"user", "assistant", "system"}:
            continue
        content, framings = _content_blocks(message.get("content", ""))
        out.append(_compose(str(message.get("role", record_type)), content, framings))
    return out


def codex_messages(records):
    """Codex rollout JSONL records -> messages (response_item records only)."""
    out = []
    for record in records:
        payload = record.get("payload")
        if record.get("type") != "response_item" or not isinstance(payload, dict):
            continue
        item_type = str(payload.get("type", "")).lower()
        if item_type == "message":
            content, framings = _content_blocks(payload.get("content", ""))
            out.append(_compose(str(payload.get("role", "assistant")), content, framings))
        elif item_type in {"function_call", "custom_tool_call"}:
            name = payload.get("name", "unknown")
            identifier = payload.get("call_id", payload.get("id", ""))
            arguments = payload.get("arguments", payload.get("input", {}))
            out.append({"role": "assistant", "content": f"[tool_call name={name} id={identifier}]\n{_plain(arguments)}"})
        elif item_type in {"function_call_output", "custom_tool_call_output"}:
            identifier = payload.get("call_id", payload.get("id", ""))
            framing = str(payload.get("framing", f"[tool_result id={identifier}]"))
            output = payload.get("output", payload.get("content", ""))
            out.append(_compose("tool", _plain(output), [framing]))
    return out


def api_messages(records, session_format):
    """Session records -> [{"role", "content"}] with the format's role map applied."""
    messages = claude_messages(records) if session_format == "claude" else codex_messages(records)
    if not messages:
        raise ValueError("no model-visible messages in the session")
    role_map = ROLE_MAPS[session_format]
    return [{"role": role_map.get(m["role"], m["role"]), "content": m["content"]} for m in messages]


def request_payload(model, request_fields, messages):
    """OpenAI chat-completions body: the request fields, then model, then messages (the key order that was sent)."""
    payload = deepcopy(dict(request_fields))
    payload["model"] = model
    payload["messages"] = messages
    return payload
