"""Fetch each model's context window (max input tokens) so the runtime pool can
route data-heavy prompts to models that can actually hold the file content.

Gemini exposes real limits via its list-models API (`input_token_limit`).
NVIDIA's OpenAI-compatible /v1/models does NOT return context length, so we use
a small known-values map for the tiny models plus a conservative default.

Output: model_eval/model_contexts.json = {"<provider>:<model>": <max_input_tokens>}
Regenerate alongside models.json; the pool reads it read-only at runtime.
"""

import json
import os

# NVIDIA NIM has no context field in its API. Default assumes a modern chat NIM
# (>=32k). Only the genuinely small models need an explicit entry so we never
# send them a big data prompt.
NVIDIA_DEFAULT = 32768
NVIDIA_KNOWN: dict[str, int] = {
    "google/gemma-2-2b-it": 8192,
    "google/gemma-2-9b-it": 8192,
    "nvidia/nemotron-mini-4b-instruct": 4096,
    "nvidia/nemotron-content-safety-reasoning-4b": 8192,
    "meta/llama-3.1-8b-instruct": 131072,
    "mistralai/mistral-large-3-675b-instruct-2512": 131072,
    "mistralai/mistral-small-4-119b-2603": 131072,
    "nvidia/nemotron-3-super-120b-a12b": 131072,
    "openai/gpt-oss-120b": 131072,
    "openai/gpt-oss-20b": 131072,
    "mistralai/mistral-nemotron": 131072,
}

GEMINI_DEFAULT = 1_048_576  # every current Gemini chat model is ~1M input tokens


def gemini_contexts() -> dict[str, int]:
    from google import genai

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    out: dict[str, int] = {}
    for m in client.models.list():
        if "generateContent" not in (m.supported_actions or []):
            continue
        short = m.name.split("/")[-1]
        out[short] = getattr(m, "input_token_limit", None) or GEMINI_DEFAULT
    return out


def build() -> dict[str, int]:
    here = os.path.dirname(__file__)
    contexts: dict[str, int] = {}

    # Gemini: real limits from the API.
    try:
        for name, limit in gemini_contexts().items():
            contexts[f"gemini:{name}"] = limit
    except Exception as exc:  # noqa: BLE001
        print(f"[probe] Gemini context probe failed: {exc}")

    # NVIDIA: API can't tell us - use the known map + default for whatever is in
    # the active pool (models.json).
    models_path = os.path.join(here, "models.json")
    if os.path.exists(models_path):
        for m in json.load(open(models_path, encoding="utf-8")):
            if m["provider"] == "nvidia":
                contexts[f"nvidia:{m['model']}"] = NVIDIA_KNOWN.get(m["model"], NVIDIA_DEFAULT)

    out_path = os.path.join(here, "model_contexts.json")
    json.dump(contexts, open(out_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"[probe] wrote {len(contexts)} context limits -> {out_path}")
    return contexts


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))
    build()
