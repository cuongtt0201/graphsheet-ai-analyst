"""Safe NDJSON serialization for streaming endpoints.

`json.dumps` defaults to allow_nan=True, which emits bare `NaN`/`Infinity`
tokens — valid for Python's json but REJECTED by the browser's JSON.parse
("Unexpected token 'N'"), which silently kills a streamed upload/agent run.
Any float that came from pandas (a blank numeric cell, an all-null column's
mean, etc.) can be non-finite, so every manual stream emit must go through
here. `_sanitize` also coerces numpy scalars to native types."""

import json
import math


def _sanitize(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    # numpy scalars expose .item(); non-finite numpy floats become None too.
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            return _sanitize(item())
        except Exception:
            return obj
    return obj


def ndjson_line(event: dict) -> str:
    """One valid-JSON NDJSON line (NaN/Inf → null), newline-terminated."""
    return json.dumps(_sanitize(event), ensure_ascii=False) + "\n"


def _import_json_response():
    from starlette.responses import JSONResponse
    return JSONResponse


class SafeJSONResponse(_import_json_response()):
    """Default response class for the app: sanitizes non-finite floats to null
    BEFORE serializing. Starlette's stock JSONResponse renders with
    allow_nan=False and raises ValueError("Out of range float values are not
    JSON compliant") the moment any profile stat / computed reply contains a
    NaN/Inf (common with messy sheets: an all-null numeric column's mean, a
    divide-by-zero KPI). Sanitizing once here fixes every non-streaming
    endpoint at the source."""

    def render(self, content) -> bytes:
        return json.dumps(
            _sanitize(content),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
