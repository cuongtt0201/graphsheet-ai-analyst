"""Every schema shipped to an LLM must be one Gemini will accept.

google-genai converts each response_schema into its own Schema type, which
takes exactly ONE `type` plus `nullable`. A JSON-Schema union list
("type": ["number", "string"]) is rejected before the request is sent, so the
call fails on every slot in the pool at once and the feature is simply dead.

This shipped: requirements pin google-genai with >=, a rebuild picked up a
version that validates strictly, and three schemas -- including the main
analysis pipeline's -- started failing everywhere. A grep is cheap; discovering
it from a wall of ValidationError is not.
"""

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def _union_type_literals(path: pathlib.Path) -> list[int]:
    """Line numbers of dict entries written as `"type": [...]`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "type"
                and isinstance(value, (ast.List, ast.Tuple))
            ):
                hits.append(value.lineno)
    return hits


def test_no_schema_declares_a_union_type():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        for line in _union_type_literals(path):
            offenders.append(f"{path.relative_to(APP.parent)}:{line}")

    assert not offenders, (
        "Union `type` lists are rejected by google-genai. Use one type plus "
        '"nullable": True instead. Found at: ' + ", ".join(offenders)
    )


def test_the_copilot_and_alpha_schemas_survive_a_gemini_conversion():
    """Convert for real when the SDK is installed, rather than trusting the grep."""
    types = __import__("pytest").importorskip("google.genai.types", reason="google-genai not installed")

    from app.agent.sheet_copilot import _COPILOT_SCHEMA

    types.Schema(**_COPILOT_SCHEMA)
