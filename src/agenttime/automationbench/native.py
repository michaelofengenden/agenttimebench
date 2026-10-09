"""Pinned optional native dependencies. No provider client or evaluation loop is invoked."""

from copy import deepcopy
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import sys
import threading

from . import SOURCE_COMMIT
from .prompts import canonical, digest

_GRADE_LOCK = threading.Lock()


def expected_search_index(root):
    """Derive the optional cache exactly as pinned api.search._regenerate_index."""
    schema_dir = Path(root) / "automationbench/tools/api/schemas"
    schemas = {}
    for path in sorted(schema_dir.glob("*.jsonc")):
        if path.is_symlink():
            raise ValueError("Native source contains a symlink")
        schema = json.loads(
            "\n".join(
                line
                for line in path.read_text().splitlines()
                if not line.lstrip().startswith("//")
            )
        )
        schemas[schema["api"]] = schema
    lines = []
    for name, schema in sorted(schemas.items()):
        for endpoint in schema.get("endpoints", []):
            descriptions = [endpoint.get("description", "")]
            for parameter in endpoint.get("parameters", {}).values():
                if isinstance(parameter, dict) and parameter.get("description"):
                    descriptions.append(parameter["description"])
            lines.append(
                "\t".join(
                    [
                        name,
                        endpoint["id"],
                        endpoint["method"],
                        endpoint["path"],
                        " ".join(filter(None, descriptions)),
                    ]
                )
            )
    return ("\n".join(lines) + "\n").encode()


def source_digest(root):
    root = Path(root).resolve()
    from ._pin import PIN

    cache = root / "automationbench/tools/api/schemas/index.txt"
    expected = expected_search_index(root)
    if hashlib.sha256(expected).hexdigest() != PIN["search_index_sha256"]:
        raise ValueError("Native search schema pin mismatch")
    if cache.is_symlink():
        raise ValueError("Native search cache cannot be a symlink")
    if cache.exists() and (not cache.is_file() or cache.read_bytes() != expected):
        raise ValueError("Native search cache differs from pinned schemas")
    files = []
    for path in sorted((root / "automationbench").rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ValueError("Native source contains a symlink")
        if path.is_file() and path != cache:
            files.append(
                (
                    path.relative_to(root).as_posix(),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    for name in ("pyproject.toml", "uv.lock"):
        path = root / name
        if path.is_symlink():
            raise ValueError("Native source contains a symlink")
        files.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
    return digest(files)


class NativeBindings:
    def __init__(self, source_root):
        self.root = Path(source_root).resolve()
        from ._pin import PIN

        pin = PIN
        self.source_sha256 = source_digest(self.root)
        if (
            pin["commit"] != SOURCE_COMMIT
            or self.source_sha256 != pin["runtime_tree_sha256"]
        ):
            raise ValueError("Native source pin mismatch")
        if sys.version_info < (3, 13):
            raise RuntimeError("AutomationBench optional runtime requires Python 3.13")
        for package, expected in pin["dependencies"].items():
            if importlib.metadata.version(package) != expected:
                raise ValueError(f"Native dependency pin mismatch: {package}")
        loaded = sys.modules.get("automationbench")
        expected = self.root / "automationbench/__init__.py"
        if loaded is not None and Path(loaded.__file__).resolve() != expected:
            raise ValueError("A different AutomationBench source is already imported")
        sys.path.insert(0, str(self.root))
        try:
            package = importlib.import_module("automationbench")
            if Path(package.__file__).resolve() != expected:
                raise ValueError("Native source import mismatch")
            from automationbench.runner import (
                strip_none_values,
                compute_allowed_services,
            )
            from automationbench.schema.world import WorldState
            from automationbench.task_contract import task_contract_sha256
            from automationbench.tools.api import API_TOOLS
            from automationbench.tool_wrapper import _create_tool_wrapper
            from verifiers.utils.tool_utils import convert_func_to_tool_def
            from automationbench.rubric import partial_credit, task_completed_correctly
            from automationbench.rubric import registry
            from pydantic import BaseModel
            from jsonschema import validate
        finally:
            sys.path.pop(0)
        self.WorldState = WorldState
        self.BaseModel = BaseModel
        self.normalize = strip_none_values
        self.allowed_services = compute_allowed_services
        self.contract = task_contract_sha256
        self.functions = {f.__name__: f for f in API_TOOLS}
        self.schemas = []
        for function in API_TOOLS:
            tool = convert_func_to_tool_def(_create_tool_wrapper(function, ["world"]))
            self.schemas.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": deepcopy(tool.parameters),
                }
            )
        self._partial = partial_credit
        self._binary = task_completed_correctly
        self._registry = registry
        self.validate = validate

    def initial(self, task):
        info = deepcopy(task["info"])
        normalized = self.normalize(info["initial_state"])
        world = self.WorldState(**normalized)
        world.meta.allowed_services = self.allowed_services(
            normalized, self.normalize(info["assertions"]), info["zapier_tools"]
        )
        return normalized, world

    def invoke(self, name, arguments, world):
        args = {
            k: v for k, v in arguments.items() if not (isinstance(v, dict) and not v)
        }
        if name == "api_fetch":
            args["world"] = world
        return self.functions[name](**args)

    def snapshot(self, world):
        allowed = {
            ("google_sheets",): "_updated_row_keys",
            ("google_ads",): "_offline_jobs",
        }
        bookkeeping = {
            "__pydantic_fields_set__",
            "__pydantic_extra__",
            "__pydantic_private__",
        }

        def walk(obj, path=()):
            if isinstance(obj, self.BaseModel):
                fields = type(obj).model_fields
                extras = set(vars(obj)) - set(fields) - bookkeeping
                accepted = {allowed[path]} if path in allowed else set()
                if extras - accepted:
                    raise ValueError("Undeclared native runtime state")
                extra = getattr(obj, "__pydantic_extra__", None)
                if extra:
                    raise ValueError("Undeclared native model extras")
                private = getattr(obj, "__pydantic_private__", None) or {}
                if set(private) - accepted:
                    raise ValueError("Undeclared native private state")
                for name in fields:
                    walk(getattr(obj, name), path + (name,))
            elif isinstance(obj, dict):
                for key, value in obj.items():
                    walk(value, path + (str(key),))
            elif isinstance(obj, (list, tuple)):
                for i, value in enumerate(obj):
                    walk(value, path + (str(i),))

        walk(world)
        runtime = {}
        for field, key in allowed.items():
            obj = getattr(world, field[0])
            present = hasattr(obj, key)
            value = getattr(obj, key, None)
            if present and key == "_updated_row_keys":
                if type(value) is not set or any(type(x) is not str for x in value):
                    raise ValueError("Invalid Sheets runtime markers")
                value = sorted(value)
            if present and key == "_offline_jobs" and type(value) is not dict:
                raise ValueError("Invalid Ads runtime jobs")
            canonical(value)
            runtime[field[0]] = {"present": present, "value": deepcopy(value)}
        result = {
            "schema": 1,
            "world": world.model_dump(mode="json"),
            "runtime": runtime,
        }
        canonical(result)
        return result

    def restore_world(self, state):
        if set(state) != {"schema", "world", "runtime"} or state["schema"] != 1:
            raise ValueError("Unknown runtime-state schema")
        if set(state["runtime"]) != {"google_sheets", "google_ads"}:
            raise ValueError("Incomplete runtime sidecar")
        world = self.WorldState(**deepcopy(state["world"]))
        for name, key in [
            ("google_sheets", "_updated_row_keys"),
            ("google_ads", "_offline_jobs"),
        ]:
            item = state["runtime"][name]
            if set(item) != {"present", "value"} or type(item["present"]) is not bool:
                raise ValueError("Invalid runtime sidecar")
            if item["present"]:
                value = deepcopy(item["value"])
                if name == "google_sheets":
                    if (
                        type(value) is not list
                        or any(type(x) is not str for x in value)
                        or value != sorted(set(value))
                    ):
                        raise ValueError("Invalid Sheets runtime markers")
                    value = set(value)
                elif type(value) is not dict:
                    raise ValueError("Invalid Ads runtime jobs")
                object.__setattr__(getattr(world, name), key, value)
            elif item["value"] is not None:
                raise ValueError("Unexpected absent runtime value")
        if self.snapshot(world) != state:
            raise ValueError("Runtime state does not round trip exactly")
        return world

    def grade(self, task, sealed, baseline):
        with _GRADE_LOCK:
            old = self._registry.STRICT_MODE
            self._registry.STRICT_MODE = True
            try:
                state = {
                    "world": self.restore_world(sealed),
                    "initial_state": deepcopy(baseline["world"]),
                    "info": {
                        "assertions": self.normalize(
                            deepcopy(task["info"]["assertions"])
                        )
                    },
                }
                score = self._partial(state)
                binary = self._binary(state)
                results = []
                for assertion, result in zip(
                    state["info"]["assertions"],
                    state["_assertion_results"],
                    strict=True,
                ):
                    result = deepcopy(result)
                    result["exclusion_reason"] = (
                        (
                            "authored"
                            if assertion.get("scored") is False
                            or assertion.get("excluded") is True
                            else "initially_satisfied"
                        )
                        if result["excluded"]
                        else None
                    )
                    results.append(result)
                counted = [r for r in results if not r["excluded"]]
                return {
                    "status": "available",
                    "score": score,
                    "task_completed_correctly": binary,
                    "assertions": results,
                    "assertions_total": len(counted),
                    "assertions_passed": sum(r["passed"] for r in counted),
                    "baseline_policy": "constructed_initial_world_v1",
                    "grading_policy": "native_partial_credit_and_all_pass",
                    "sealed_state_sha256": digest(sealed),
                    "baseline_sha256": digest(baseline),
                }
            finally:
                self._registry.STRICT_MODE = old
