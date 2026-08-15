"""Thin TRANSPORT wrappers around the Gemini, NVIDIA NIM, and OpenRouter APIs.

Every call_* returns (raw_text, latency_seconds, error, meta) and never raises —
rate limits, auth failures, and timeouts are caught and reported instead, so a
caller loop can keep going through the rest of the candidate list.

These functions hold NO policy. How a schema gets enforced, what an error MEANS,
and when to retry belong to the harness gate (backend/app/ai/harness.py); the job
here is only to speak each vendor's dialect and hand back what came out.

`meta` is what makes that split possible:
    exc         the original exception object, UNFLATTENED, so the gate can
                classify on type and status instead of regexing a formatted
                string — the previous design lost this and had to guess
    status      HTTP status when the SDK exposes one, else None
    tokens_in   prompt tokens as reported by the provider (None if not reported)
    tokens_out  completion tokens likewise
    model_used  the model that actually answered; differs from the one requested
                for routers like openrouter/free, which pick per request
"""

import json
import os
import time


def _meta(exc=None, tokens_in=None, tokens_out=None, model_used=None) -> dict:
    status = None
    for attr in ("status_code", "code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            status = v
            break
    return {"exc": exc, "status": status, "tokens_in": tokens_in,
            "tokens_out": tokens_out, "model_used": model_used}


def _openai_usage(obj):
    u = getattr(obj, "usage", None)
    if u is None:
        return None, None
    return getattr(u, "prompt_tokens", None), getattr(u, "completion_tokens", None)


def _gemini_usage(obj):
    u = getattr(obj, "usage_metadata", None)
    if u is None:
        return None, None
    return getattr(u, "prompt_token_count", None), getattr(u, "candidates_token_count", None)

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


# temperature 0 = deterministic, least "creative" output → far fewer hallucinated
# column names / off-schema plans. Gemini previously ran at its default (~1.0),
# which was the main source of the invalid-enum churn.
TEMPERATURE = 0.0


def call_gemini(model: str, prompt: str, api_key: str | None = None, timeout_s: float = 30.0,
                response_schema: dict | None = None,
                thinking_budget: int = 0, on_thinking=None, system_instruction: str | None = None):
    from google import genai
    from google.genai import types

    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None, 0.0, "GEMINI_API_KEY not set", _meta()

    start = time.monotonic()
    try:
        client = genai.Client(api_key=api_key)
        cfg = dict(
            temperature=TEMPERATURE,
            response_mime_type="application/json",
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
        )
        # response_schema forces the model to obey the exact structure (enums,
        # required fields) - the strongest defense against off-schema output.
        if response_schema is not None:
            cfg["response_schema"] = response_schema
            
        if system_instruction is not None:
            cfg["system_instruction"] = system_instruction
        # thinking_budget: 0 = off, -1 = dynamic (model picks its own depth
        # per prompt), >0 = fixed cap. `!= 0` so -1 actually reaches the API -
        # a `> 0` check would silently drop dynamic mode.
        if thinking_budget != 0:
            # include_thoughts=True is required for thought summaries to come
            # back in the stream at all - without it the model thinks silently
            # and the on_thinking callback (the 💭 live status) never fires.
            cfg["thinking_config"] = types.ThinkingConfig(
                thinking_budget=thinking_budget,
                include_thoughts=True,
            )

        config = types.GenerateContentConfig(**cfg)

        # Stream when thinking is on so we can emit thought chunks live.
        if thinking_budget != 0:
            response_parts: list[str] = []
            tin = tout = None
            for chunk in client.models.generate_content_stream(
                model=model, contents=prompt, config=config,
            ):
                # Usage arrives on the trailing chunks; keep the last non-empty
                # reading rather than the first, which is usually still zero.
                c_in, c_out = _gemini_usage(chunk)
                if c_in is not None:
                    tin, tout = c_in, c_out
                if not chunk.candidates:
                    continue
                for part in chunk.candidates[0].content.parts:
                    if getattr(part, "thought", False):
                        if on_thinking and part.text:
                            on_thinking(part.text)
                    elif part.text:
                        response_parts.append(part.text)
            latency = time.monotonic() - start
            return "".join(response_parts), latency, None, _meta(
                tokens_in=tin, tokens_out=tout, model_used=model)
        else:
            response = client.models.generate_content(
                model=model, contents=prompt, config=types.GenerateContentConfig(**cfg),
            )
            latency = time.monotonic() - start
            tin, tout = _gemini_usage(response)
            return response.text, latency, None, _meta(
                tokens_in=tin, tokens_out=tout, model_used=model)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, classified by the gate
        latency = time.monotonic() - start
        return None, latency, f"{type(exc).__name__}: {exc}", _meta(exc=exc)


def call_nvidia(model: str, prompt: str, api_key: str | None = None, timeout_s: float = 25.0,
                response_schema: dict | None = None, system_instruction: str | None = None):
    from openai import OpenAI

    api_key = api_key or os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        return None, 0.0, "NVIDIA_API_KEY not set", _meta()

    start = time.monotonic()
    try:
        client = OpenAI(base_url=NVIDIA_BASE_URL, api_key=api_key, timeout=timeout_s, max_retries=0)
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})
        
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=TEMPERATURE,
            response_format={"type": "json_object"},
            stream=True,
            stream_options={"include_usage": True},
        )
        chunks, tin, tout, used = [], None, None, None
        for chunk in stream:
            c_in, c_out = _openai_usage(chunk)
            if c_in is not None:
                tin, tout = c_in, c_out
            used = getattr(chunk, "model", None) or used
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                chunks.append(delta.content)
        latency = time.monotonic() - start
        return "".join(chunks), latency, None, _meta(
            tokens_in=tin, tokens_out=tout, model_used=used or model)
    except Exception as exc:  # noqa: BLE001
        latency = time.monotonic() - start
        return None, latency, f"{type(exc).__name__}: {exc}", _meta(exc=exc)


def call_openrouter(model: str, prompt: str, api_key: str | None = None, timeout_s: float = 60.0,
                    response_schema: dict | None = None, system_instruction: str | None = None):
    """OpenRouter — an aggregator: one OpenAI-compatible endpoint in front of many
    upstream providers. Same shape as call_nvidia, with three deliberate additions:

    - data_collection="deny": route ONLY to upstreams that do not collect/train on
      the prompt. Non-negotiable here — these prompts carry customers' spreadsheet
      contents, and OpenRouter's free endpoints otherwise MAY publish prompts to
      public datasets.
    - require_parameters=True: only pick upstreams that actually support the
      params we send (notably response_format). The same model served by two
      upstreams can differ, and one that silently drops response_format returns
      prose that fails our schema validation downstream.
    - response_format=json_schema WITHOUT strict. Strict mode demands
      additionalProperties:false and every property listed in `required`; our
      schemas intentionally carry optional fields (see _drop_nulls in pool.py),
      so strict would reject them outright. Non-strict still steers the model,
      and pool.py's jsonschema validate stays the real gate.
    """
    from openai import OpenAI

    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None, 0.0, "OPENROUTER_API_KEY not set", _meta()

    start = time.monotonic()
    try:
        client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key,
                        timeout=timeout_s, max_retries=0)
        if response_schema is not None:
            fmt = {"type": "json_schema",
                   "json_schema": {"name": "response", "strict": False,
                                   "schema": response_schema}}
            # Restate the schema in the prompt as well. Non-strict json_schema is
            # only ADVISORY, and these free endpoints do ignore it — openrouter/free
            # answered a {thu_do, quoc_gia} request with {country, gdp}. Gemini needs
            # no such reminder because its response_schema is enforced natively.
            system_instruction = (system_instruction or "") + (
                "\n\n[OUTPUT CONTRACT]\nReturn ONE JSON object and nothing else. It "
                "MUST validate against this JSON Schema, using these EXACT property "
                "names — do not rename, translate, or add fields:\n"
                + json.dumps(response_schema, ensure_ascii=False)
            )
        else:
            fmt = {"type": "json_object"}

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=TEMPERATURE,
            response_format=fmt,
            stream=True,
            stream_options={"include_usage": True},
            extra_body={"provider": {"data_collection": "deny",
                                     "require_parameters": True}},
        )
        chunks, tin, tout, used = [], None, None, None
        for chunk in stream:
            c_in, c_out = _openai_usage(chunk)
            if c_in is not None:
                tin, tout = c_in, c_out
            # openrouter/free picks a different upstream per request, so the
            # model that answered is only knowable from the response.
            used = getattr(chunk, "model", None) or used
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                chunks.append(delta.content)
        latency = time.monotonic() - start
        meta = _meta(tokens_in=tin, tokens_out=tout, model_used=used or model)
        text = "".join(chunks)
        if not text.strip():
            # An empty 200 means every upstream was filtered out by the provider
            # preferences above. Report it as an error so the pool moves on
            # instead of handing "" to json.loads.
            return None, latency, "empty response (no upstream matched data_collection=deny)", meta
        return text, latency, None, meta
    except Exception as exc:  # noqa: BLE001
        latency = time.monotonic() - start
        return None, latency, f"{type(exc).__name__}: {exc}", _meta(exc=exc)


CALLERS = {
    "gemini": call_gemini,
    "nvidia": call_nvidia,
    "openrouter": call_openrouter,
}


def call_model(provider: str, model: str, prompt: str, api_key: str | None = None,
               response_schema: dict | None = None,
               thinking_budget: int = 0, on_thinking=None, system_instruction: str | None = None):
    caller = CALLERS.get(provider)
    if caller is None:
        return None, 0.0, f"Unknown provider: {provider}", _meta()
    kwargs: dict = dict(api_key=api_key, response_schema=response_schema, system_instruction=system_instruction)
    if provider == "gemini":
        kwargs["thinking_budget"] = thinking_budget
        kwargs["on_thinking"] = on_thinking
    return caller(model, prompt, **kwargs)

