"""R-replay and R-scrubbed: the parent's transcript, rebuilt from its session log, sent to the model's API (no tools).

    python replay.py --job RUNID__replay__k1 --parents parents.csv --sessions DIR [--out results]

R-replay: inline images become a text placeholder, reconstruct.api_messages rebuilds the model-visible messages
(hidden reasoning excluded), request_removal.strip_request_obj removes the requested duration and every restatement of
it, whitespace-only messages are dropped and QUESTION is appended. R-scrubbed: the same rebuilt transcript (before the
request removal) goes through scrub.scrub_messages instead. One chat-completions request per answer (k1 and k2 send
the same body; request_fields has the flags); the body is saved to <out>/requests/ for press.py.
"""
import gzip
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import common
import reconstruct
import scrub
from common import AGENTS, QUESTION
from request_removal import strip_request_obj

MAX_TOKENS = 32768
REQUEST_TIMEOUT_S = 1200            # per HTTP attempt
MAX_TRIES = 4
RETRY_CODES = {429, 500, 502, 503, 504}
IMAGE_PLACEHOLDER = "[image omitted from text replay: {media}]"


def request_fields(agent):
    return {"reasoning": {"effort": "max"}, "max_tokens": MAX_TOKENS,
            "provider": {"order": [AGENTS[agent]["provider"]], "allow_fallbacks": False, "require_parameters": True},
            "usage": {"include": True},
            "transforms": []}                       # no OpenRouter middle-out compression


def _replace_images(value):
    if isinstance(value, dict):
        if value.get("type") == "image":            # Claude: {"type":"image","source":{...,"data":b64}}
            media = (value.get("source") or {}).get("media_type", "image")
            return {"type": "text", "text": IMAGE_PLACEHOLDER.format(media=media)}
        if value.get("type") == "input_image":      # Codex: {"type":"input_image","image_url":"data:..."}
            url = str(value.get("image_url", ""))
            media = url[5:url.index(";")] if url.startswith("data:") and ";" in url else "image"
            return {"type": "input_text", "text": IMAGE_PLACEHOLDER.format(media=media)}
        return {k: _replace_images(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_images(v) for v in value]
    return value


def read_session(path):
    """Session JSONL records, inline images replaced by a short text placeholder."""
    records = []
    for line in open(path, encoding="utf-8"):
        if line.strip():
            rec = json.loads(line)
            records.append(_replace_images(rec) if '"image' in line or '"input_image' in line else rec)
    return records


def _finish(agent, messages):
    sent = [m for m in messages if m["content"].strip()]
    return reconstruct.request_payload(AGENTS[agent]["openrouter"], request_fields(agent),
                                       [*sent, {"role": "user", "content": QUESTION}])


def replay_payload(parent, records):
    """R-replay request body: the rebuilt transcript without the requested duration or any restatement of it."""
    fmt = AGENTS[parent["agent"]]["session_format"]
    api = strip_request_obj(reconstruct.api_messages(records, fmt), parent["request_min"])
    return _finish(parent["agent"], api)


def scrubbed_payload(parent, records):
    """R-scrubbed request body: the rebuilt transcript with every time cue removed (scrub.py)."""
    fmt = AGENTS[parent["agent"]]["session_format"]
    body = [m for m in reconstruct.api_messages(records, fmt) if m["content"].strip()]
    year = next(r["timestamp"][:4] for r in records if r.get("timestamp"))     # the year the session ran in
    return _finish(parent["agent"], scrub.scrub_messages(body, parent["request_min"], year))


def call_openrouter(payload, key):
    """-> (response or None, error or None). Retries 429/5xx (HTTP or in the body) with backoff, at most MAX_TRIES
    attempts; never retries a transport error (a completion may already have been billed)."""
    for n in range(1, MAX_TRIES + 1):
        req = urllib.request.Request(common.OPENROUTER_BASE + "/chat/completions",
                                     data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").replace(key, "[key]")[:500]
            if exc.code in RETRY_CODES and n < MAX_TRIES:
                wait = min(60, 4 * 2 ** (n - 1)) + random.uniform(0, 3)
                try:
                    wait = max(wait, min(120, float(exc.headers.get("Retry-After", "0"))))
                except (TypeError, ValueError):
                    pass
                time.sleep(wait)
                continue
            return None, f"HTTP {exc.code}: {detail}"
        except Exception as exc:  # transport, timeout, decode
            return None, f"transport error: {type(exc).__name__}: {str(exc).replace(key, '[key]')}"[:500]
        err = body.get("error")
        if err:
            code = err.get("code") if isinstance(err, dict) else None
            if isinstance(code, int) and code in RETRY_CODES and n < MAX_TRIES:
                time.sleep(min(60, 4 * 2 ** (n - 1)) + random.uniform(0, 3))
                continue
            return body, f"provider error in body: {json.dumps(err)[:500]}"
        return body, None
    return None, "exhausted retries"


def answer_fields(parent, resp, error):
    """status, minutes, final_text, error of one chat-completions answer."""
    text, finish = "", None
    if resp and resp.get("choices"):
        choice = resp["choices"][0]
        text, finish = (choice.get("message") or {}).get("content") or "", choice.get("finish_reason")
    model = (resp or {}).get("model")
    slug = AGENTS[parent["agent"]]["openrouter"]
    if not error and model and not re.fullmatch(re.escape(slug) + r"(-\d{4}-\d{2}-\d{2}|-\d{8})?", model):
        error = f"model mismatch: requested {slug}, got {model}"
    if not error and finish == "content_filter" and not text.strip():
        error = "empty completion, finish_reason content_filter"
    return {**common.answer(text, error), "finish_reason": finish}


def save_request(out, job, payload):
    path = Path(out) / "requests" / f"{job}.request.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)


def main(argv=None):
    args, parent, condition, k = common.runner_args(argv, "R-replay and R-scrubbed")
    if condition not in ("replay", "scrubbed"):
        sys.exit(f"{args.job}: replay.py runs R-replay and R-scrubbed only")
    try:
        records = read_session(common.session_file(args.sessions, parent["run_id"]))
        build = replay_payload if condition == "replay" else scrubbed_payload
        payload = build(parent, records)
    except Exception as exc:
        common.write_record(args.out, args.job, parent, condition, k,
                            **common.answer("", f"request build failed: {type(exc).__name__}: {exc}"))
        return 1
    save_request(args.out, args.job, payload)
    resp, error = call_openrouter(payload, common.openrouter_key())
    rec = common.write_record(args.out, args.job, parent, condition, k, **answer_fields(parent, resp, error))
    return 0 if rec["status"] != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
