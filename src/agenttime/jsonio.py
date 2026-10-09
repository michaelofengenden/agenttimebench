"""Reject ambiguous JSON before it reaches a manifest hash or execution decision."""
import json
from .evidence import canonical_json


def loads_strict(data: str | bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result

    try:
        value = json.loads(data, object_pairs_hook=pairs)
        canonical_json(value)  # Also rejects overflowed numbers such as 1e999.
    except RecursionError as error:
        raise ValueError('JSON nesting exceeds the supported depth') from error
    return value
