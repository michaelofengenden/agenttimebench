"""Normalize only the verified AIME and Arena JSON sources, without loaders."""

from pathlib import Path
import hashlib
import json
import os


ROOT = Path(__file__).resolve().parent


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_verified(relative):
    path = ROOT / relative
    receipt = json.loads((ROOT / "download-receipt.json").read_text())
    entry = next(item for item in receipt if item["output_path"] == str(path))
    assert entry["status"] == "downloaded_verified"
    data = path.read_bytes()
    assert len(data) == entry["bytes"]
    assert sha256(data) == entry["sha256"]
    return data.decode("utf-8")


def objects(text):
    decoder = json.JSONDecoder()
    position = 0
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        if position == len(text):
            break
        value, position = decoder.raw_decode(text, position)
        assert isinstance(value, dict)
        yield value


def save(task, rows, details):
    assert rows
    assert all(set(row) == {"question", "answer"} for row in rows)
    assert all(isinstance(row["question"], str) and row["question"] for row in rows)
    data = json.dumps(rows, indent=2, ensure_ascii=False).encode("utf-8")
    destination = ROOT / "posttrain" / task / "test_data.json"
    destination.write_bytes(data)
    return {
        "task": task,
        "status": "normalized_from_pinned_raw_sources",
        "path": str(destination),
        "rows": len(rows),
        "bytes": len(data),
        "sha256": sha256(data),
        "details": details,
    }


def main():
    os.umask(0o077)
    aime = list(objects(read_verified("posttrain/aime2025/raw/test.jsonl")))
    assert all("problem" in row and "answer" in row for row in aime)
    results = [save(
        "aime2025",
        [{"question": row["problem"], "answer": row["answer"]} for row in aime],
        {"mapping": "problem -> question; answer unchanged", "input_rows": len(aime)},
    )]

    seen = {}
    rows = []
    input_counts = {}
    duplicate_uid_count = 0
    duplicate_uid_changed_prompt_count = 0
    for version in ("v0.1", "v2.0"):
        entries = list(objects(read_verified(
            f"posttrain/arenahardwriting/raw/question-{version}.jsonl"
        )))
        input_counts[version] = len(entries)
        for entry in entries:
            uid = entry.get("uid", entry.get("question_id"))
            assert uid is not None
            prompt = entry.get("prompt", "")
            if isinstance(prompt, list):
                prompt = json.dumps(prompt, ensure_ascii=False)
            assert isinstance(prompt, str) and prompt
            if uid in seen:
                duplicate_uid_count += 1
                duplicate_uid_changed_prompt_count += seen[uid] != prompt
                continue
            seen[uid] = prompt
            rows.append({"question": prompt, "answer": ""})
    results.append(save("arenahardwriting", rows, {
        "mapping": "v0.1 then v2.0; first occurrence per uid; prompt -> question; empty answer",
        "input_rows": input_counts,
        "duplicate_uid_count": duplicate_uid_count,
        "duplicate_uid_changed_prompt_count": duplicate_uid_changed_prompt_count,
        "all_upstream_prompts_present_and_nonempty": True,
    }))
    (ROOT / "normalization-receipt.json").write_text(
        json.dumps(results, indent=2) + "\n"
    )
    for result in results:
        print(result["task"], result["rows"], "rows", result["sha256"])


if __name__ == "__main__":
    main()
