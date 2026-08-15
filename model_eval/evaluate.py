"""Local, one-shot (re-runnable) evaluation of every model available to this account.

Run: python evaluate.py

Step 1 (discover): ask each provider's own API which models this account can actually
call (discover.py) - no hand-picked model list.
Step 2 (evaluate): test every discovered model against the fixed test cases in
test_cases.py, validating that the response is valid JSON matching the expected
schema.
Step 3 (filter): write models.json with only the models that passed every test case,
ordered by reliability then speed. The backend later reads only models.json - it
never talks to provider SDKs or does model discovery/selection itself.
"""

import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from jsonschema import ValidationError, validate

from discover import discover_candidates
from providers import call_model
from test_cases import TEST_CASES

HERE = Path(__file__).parent
MAX_WORKERS = 8


def strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def run_test_case(provider: str, model: str, case: dict) -> dict:
    raw_text, latency, error, _meta = call_model(provider, model, case["prompt"])
    result = {
        "test_case": case["name"],
        "latency_s": round(latency, 2),
        "passed": False,
        "error": error,
    }
    if error is not None:
        return result

    try:
        parsed = json.loads(strip_code_fence(raw_text))
        validate(instance=parsed, schema=case["schema"])
        result["passed"] = True
    except (json.JSONDecodeError, ValidationError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def main():
    load_dotenv(HERE / ".env")

    print("Discovering available models from each provider's API...")
    candidates = discover_candidates()
    print(f"Found {len(candidates)} candidate model(s) to evaluate.\n")

    jobs = []
    for candidate in candidates:
        for case in TEST_CASES:
            jobs.append((candidate["provider"], candidate["model"], case))

    results_by_model = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_job = {
            pool.submit(run_test_case, provider, model, case): (provider, model, case["name"])
            for provider, model, case in jobs
        }
        for future in as_completed(future_to_job):
            provider, model, case_name = future_to_job[future]
            res = future.result()
            key = (provider, model)
            results_by_model.setdefault(key, []).append(res)
            status = "PASS" if res["passed"] else f"FAIL ({res['error']})"
            print(f"[{provider}/{model}] {case_name}: {status} ({res['latency_s']}s)")

    qualifying = []
    for (provider, model), case_results in results_by_model.items():
        if len(case_results) < len(TEST_CASES):
            continue  # a job crashed/never completed
        if not all(r["passed"] for r in case_results):
            continue
        avg_latency = sum(r["latency_s"] for r in case_results) / len(case_results)
        qualifying.append({"provider": provider, "model": model, "avg_latency_s": round(avg_latency, 2)})

    qualifying.sort(key=lambda m: m["avg_latency_s"])
    for i, m in enumerate(qualifying, start=1):
        m["priority"] = i
        del m["avg_latency_s"]

    out_path = HERE / "models.json"
    out_path.write_text(json.dumps(qualifying, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n{len(qualifying)}/{len(candidates)} model(s) qualified (passed all test cases).")
    print(f"Written to {out_path}")

    if not qualifying:
        sys.exit(1)


if __name__ == "__main__":
    main()
