"""The one re-ask of R-scrubbed: a first reply that gave no usable number gets one follow-up turn.

    python press.py [--out results]

A reply is usable when its status is "ok" and minutes > 0. Every other R-scrubbed reply that is not an API error is
re-asked once: the saved request (<out>/requests/<job>.request.json.gz) is sent again with the first reply as the
assistant turn and PRESS as a new user turn (model, provider pin, effort and max_tokens unchanged). The record is
replaced by the second answer, with "pressed": true and the first reply under "first_reply". The fork conditions and
R-replay are never re-asked.
"""
import argparse
import gzip
import json
from pathlib import Path

import common
import replay

PRESS = ("Tools are not available in this conversation, and an exact measurement is not needed. "
         "Please give your best estimate.\nreturn minutes = <Number of minutes>")
PARENT_FIELDS = ("run_id", "agent", "benchmark", "task", "request_min", "truth_min")


def usable(rec):
    m = rec.get("minutes")
    return rec.get("status") == "ok" and isinstance(m, (int, float)) and m > 0


def eligible(rec):
    return rec["condition"] == "scrubbed" and not rec.get("pressed") and rec.get("status") != "error" and not usable(rec)


def press_payload(saved, first_reply):
    """The saved R-scrubbed request with the first reply and PRESS appended."""
    assert saved["messages"][-1] == {"role": "user", "content": common.QUESTION}
    assert first_reply.strip(), "the first reply is empty"
    return {**saved, "messages": [*saved["messages"], {"role": "assistant", "content": first_reply},
                                  {"role": "user", "content": PRESS}]}


def press(out, rec, key):
    with gzip.open(Path(out) / "requests" / f"{rec['job']}.request.json.gz", "rt", encoding="utf-8") as fh:
        saved = json.load(fh)
    resp, error = replay.call_openrouter(press_payload(saved, rec["final_text"]), key)
    parent = {f: rec[f] for f in PARENT_FIELDS}
    first = {f: rec.get(f) for f in ("status", "minutes", "final_text")}
    return common.write_record(out, rec["job"], parent, rec["condition"], rec["k"], pressed=True, first_reply=first,
                               **replay.answer_fields(parent, resp, error))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results")
    args = ap.parse_args(argv)
    todo = [r for r in (json.loads(p.read_text()) for p in sorted(Path(args.out).glob("*.json"))) if eligible(r)]
    print(f"{len(todo)} R-scrubbed replies to re-ask")
    key = common.openrouter_key()
    for rec in todo:
        try:
            new = press(args.out, rec, key)
            print(f"{rec['job']}: {new['status']} minutes={new['minutes']}")
        except Exception as exc:                       # one failed re-ask must not stop the others
            print(f"{rec['job']}: re-ask failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
