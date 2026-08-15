"""Discover which models are actually available to this account for each provider,
via each provider's own list-models API - not a hand-picked/hardcoded list.

Both providers return catalogs full of non-chat models (image/video/tts/embedding/
translation/rerank), so a light keyword filter narrows the list down to models that
are plausibly chat/instruct-capable before we spend API calls testing them.
"""

import os

from providers import NVIDIA_BASE_URL

GEMINI_EXCLUDE_KEYWORDS = [
    "tts", "image", "clip", "lyria", "robotics", "computer-use",
    "antigravity", "deep-research", "omni", "banana", "customtools",
]

NVIDIA_EXCLUDE_KEYWORDS = [
    "embed", "rerank", "guard", "vision", "translate", "tts", "asr", "ocr",
    "image", "video", "diffusion", "kosmos", "fuyu", "deplot", "codegemma",
    "starcoder", "codellama",
]


def discover_gemini_models() -> list[str]:
    from google import genai

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    names = []
    for m in client.models.list():
        if "generateContent" not in (m.supported_actions or []):
            continue
        short_name = m.name.split("/")[-1]
        if any(k in short_name.lower() for k in GEMINI_EXCLUDE_KEYWORDS):
            continue
        names.append(short_name)
    return names


def discover_nvidia_models() -> list[str]:
    from openai import OpenAI

    client = OpenAI(
        base_url=NVIDIA_BASE_URL,
        api_key=os.environ.get("NVIDIA_API_KEY"),
        max_retries=0,
    )
    ids = [m.id for m in client.models.list().data]
    return [i for i in ids if not any(k in i.lower() for k in NVIDIA_EXCLUDE_KEYWORDS)]


def discover_candidates() -> list[dict]:
    candidates = []
    try:
        for name in discover_gemini_models():
            candidates.append({"provider": "gemini", "model": name})
    except Exception as exc:  # noqa: BLE001
        print(f"[discover] Gemini model list failed: {exc}")

    try:
        for name in discover_nvidia_models():
            candidates.append({"provider": "nvidia", "model": name})
    except Exception as exc:  # noqa: BLE001
        print(f"[discover] NVIDIA model list failed: {exc}")

    return candidates
