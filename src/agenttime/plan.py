"""Offline allocation accounting. This module does not dispatch attempts."""

import json
from pathlib import Path


def read_config(directory: Path, name: str) -> dict:
    value = json.loads((directory / f"{name}.json").read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{name}.json must contain an object")
    return value


def indexed_records(value: object, label: str) -> dict[str, dict]:
    if not isinstance(value, list):
        raise ValueError(f"{label} entries must be a list")
    records = {}
    for row in value:
        if not isinstance(row, dict):
            raise ValueError(f"Each {label} entry must be an object")
        identity = row.get("id")
        if not isinstance(identity, str) or not identity.strip() or identity != identity.strip():
            raise ValueError(f"Each {label} needs a non-empty ID without surrounding whitespace")
        if identity in records:
            raise ValueError(f"Duplicate {label} ID: {identity}")
        records[identity] = row
    return records


def build_plan(directory: Path, selected: list[str] | None = None) -> dict:
    suite = read_config(directory, "suite")
    registry = read_config(directory, "agents")
    protocol = read_config(directory, "protocol")
    calibration = read_config(directory, "calibration")
    families = indexed_records(suite.get("families"), "family")
    agents = indexed_records(registry.get("agents"), "agent")
    arms = indexed_records(protocol.get("arms"), "arm")

    if not agents:
        raise ValueError("At least one agent is required")
    task_count = 0
    included_families = 0
    for identity, family in families.items():
        count = family.get("count")
        if type(count) is not int or count < 0:
            raise ValueError(f"Family {identity} count must be a non-negative integer")
        task_count += count
        included_families += count > 0
    if task_count == 0:
        raise ValueError("At least one task place is required")
    if set(arms) != {"natural", "short", "long"}:
        raise ValueError("The plan requires exactly natural, short and long arms")

    chosen = list(agents) if selected is None else selected
    if not chosen:
        raise ValueError("At least one agent must be selected")
    if len(chosen) != len(set(chosen)):
        raise ValueError("Duplicate selected agent ID")
    unknown = set(chosen) - set(agents)
    if unknown:
        raise ValueError(f"Unknown agent ID: {', '.join(sorted(unknown))}")

    concurrency = protocol.get("concurrency")
    if not isinstance(concurrency, dict):
        raise ValueError("Protocol concurrency must be an object")
    limit = concurrency.get("active_attempt_limit")
    if type(limit) is not int or limit <= 0:
        raise ValueError("Active attempt limit must be a positive integer")
    at_once = concurrency.get("evaluated_agents_at_once")
    if type(at_once) is not int or at_once != 1:
        raise ValueError("Plan supports one evaluated agent configuration at a time")

    if (calibration.get("status") != "collect_natural_first"
            or calibration.get("average_seconds_by_task") != {}):
        raise ValueError("This foundation does not implement timed calculation; collect natural runs first")
    for arm in arms.values():
        if arm.get("duration_seconds") is not None:
            raise ValueError("Timed durations must remain unset during natural collection")
    natural = arms["natural"]
    if (natural.get("duration_request") is not None
            or natural.get("experiment_time_cap_seconds") is not None):
        raise ValueError("Natural attempts cannot have a duration request or experiment time cap")

    per_agent = {arm: task_count for arm in ("natural", "short", "long")}
    per_agent["total"] = sum(per_agent.values())
    return {
        "experiment": "AgentTime v1.1",
        "scope": "offline_allocation_plan_not_runnable_manifest",
        "task_places": task_count,
        "included_families": included_families,
        "selected_agent_ids": chosen,
        "per_agent": per_agent,
        "selected_total_slots": per_agent["total"] * len(chosen),
        "active_attempt_limit": limit,
        "maximum_planned_overlap": min(limit, task_count),
        "evaluated_agents_at_once": 1,
        "calibration_status": calibration["status"],
        "timed_duration_seconds": {"short": None, "long": None},
        "replacements_in_nominal_counts": False,
        "execution_implemented": False,
        "launch_ready": False,
    }


def format_plan(plan: dict) -> str:
    per_agent = plan["per_agent"]
    return "\n".join([
        plan["experiment"],
        f"Per agent: {per_agent['natural']} natural + {per_agent['short']} short + "
        f"{per_agent['long']} long = {per_agent['total']} planned runs",
        f"Selected agents: {', '.join(plan['selected_agent_ids'])}",
        f"Task allocation: {plan['task_places']} places across {plan['included_families']} families",
        f"Concurrency target: up to {plan['maximum_planned_overlap']} active attempts, one agent configuration at a time",
        "Timing: collect natural runtimes first; short and long durations are unset",
        "Status: offline allocation only; task qualification and execution are still to build",
        "Infrastructure replacements are counted separately",
    ])
