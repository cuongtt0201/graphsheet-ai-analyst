"""Run LLM-generated Python code against the uploaded dataframes - safely.

This module is the ONLY place AI-generated code is allowed to execute. Every
entry point (chat snippets, the Code Interpreter's layout scripts, and the
mock-data synthesizer) goes through the same two-tier pipeline:

  Tier 1 - Docker sibling container (preferred):
      network disabled, 512MB RAM, 1 CPU, hard timeout. Data crosses the
      boundary ONLY as parquet + JSON — never pickle, so code inside the
      container cannot craft a payload that executes on the host when read.
      Requires the SANDBOX_IMAGE image (see Dockerfile.sandbox); if Docker or
      the image is missing we fall back to tier 2 instead of failing.

  Tier 2 - in-process restricted execution (fallback):
      1. Static AST scan  - reject dunder access and dangerous names before
                            anything executes. Imports are rejected for chat
                            snippets; layout/datagen scripts may import only a
                            small whitelist (pandas/numpy/math/datetime/...).
      2. Restricted namespace - no real __builtins__; only df/dfs/pd/np plus a
                            small whitelist of harmless builtins.
      3. Timeout          - the code runs in a daemon thread; on overrun we
                            abandon it (a thread can't be force-killed, but the
                            restricted namespace keeps a runaway harmless).

The in-process tier is "good enough for a single-process friend-group tool";
the container tier is what you want the moment strangers can reach this API.
"""

import ast
import contextvars
import json
import logging
import os
import shutil
import socket
import threading
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import docker
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False

# Image with pandas/numpy/pyarrow preinstalled (see backend/Dockerfile.sandbox).
# python:3.11-slim does NOT contain pandas, and the container has no network,
# so running on a bare image can never work - we require this one explicitly.
SANDBOX_IMAGE = os.environ.get("SANDBOX_IMAGE", "ai-dashboard-sandbox:latest")

# Measurements of every merge the last snippet performed, published by whichever
# tier ran it. A ContextVar rather than a module list because three code paths
# fan out four threads wide — a shared list would hand one request another
# request's joins.
join_measurements: contextvars.ContextVar = contextvars.ContextVar(
    "join_measurements", default=None
)

CHAT_TIMEOUT_S = 8.0     # short pandas snippets from the chat agent
SCRIPT_TIMEOUT_S = 30.0  # full layout scripts / data generation
MAX_RESULT_ROWS = 200    # cap what we ship back to the model / UI

# Names the snippet is never allowed to reference.
_FORBIDDEN_NAMES = {
    "__import__", "eval", "exec", "compile", "open", "input",
    "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "os", "sys", "subprocess", "shutil", "socket", "requests",
    "importlib", "builtins", "breakpoint", "help", "exit", "quit",
}

# Modules a layout/datagen script (or a saved skill) may import.
ALLOWED_IMPORTS = {"pandas", "numpy", "math", "datetime", "random", "re", "json"}

# The only builtins in-process snippets may use.
_SAFE_BUILTINS = {
    "abs": abs, "min": min, "max": max, "sum": sum, "len": len,
    "round": round, "sorted": sorted, "range": range, "enumerate": enumerate,
    "zip": zip, "map": map, "filter": filter, "list": list, "dict": dict,
    "set": set, "tuple": tuple, "str": str, "int": int, "float": float,
    "bool": bool, "any": any, "all": all, "print": print,
    "isinstance": isinstance, "repr": repr, "divmod": divmod, "pow": pow,
}


class SmartDataframeDict(dict):
    """Resilient multi-key dictionary for DataFrames in sandbox.
    Supports lookups by:
    - Full source_id: dfs['DoanhThu.xlsx::Sheet1']
    - Sheet name: dfs['Sheet1']
    - Clean identifier: dfs['sheet1'], dfs['doanh_thu']
    - Single sheet fallback
    """
    def __init__(self, raw_dict: dict[str, pd.DataFrame] | None = None):
        super().__init__()
        self._aliases: dict[str, str] = {}
        if raw_dict:
            for k, v in raw_dict.items():
                self[k] = v

    def __setitem__(self, key: str, value: pd.DataFrame):
        super().__setitem__(key, value)
        if isinstance(key, str):
            clean_k = key.lower().strip()
            self._aliases[clean_k] = key
            if "::" in key:
                sheet_part = key.split("::")[-1]
                self._aliases[sheet_part.lower().strip()] = key
                var_name = "".join(c if c.isalnum() else "_" for c in sheet_part).lower()
                if var_name:
                    self._aliases[var_name] = key

    def __getitem__(self, key: str) -> pd.DataFrame:
        if key in self:
            return super().__getitem__(key)
        if isinstance(key, str):
            clean_k = key.lower().strip()
            if clean_k in self._aliases:
                return super().__getitem__(self._aliases[clean_k])
            # If only 1 sheet exists, fallback gracefully
            if len(self) == 1:
                return next(iter(self.values()))
        available = list(self.keys())
        raise KeyError(f"Sheet '{key}' không tồn tại trong bộ dữ liệu. Các bảng có sẵn: {available}")


class UnsafeCodeError(Exception):
    """The static scan rejected the snippet before running it."""


def scan_code(code: str, allow_imports: bool = False) -> None:
    """Raise UnsafeCodeError if the code uses dunder attributes, forbidden
    names, or (unless whitelisted) imports. Runs on the parsed AST so there's
    nothing to smuggle past a regex."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise UnsafeCodeError(f"Cú pháp Python không hợp lệ: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if not allow_imports:
                raise UnsafeCodeError("Không được dùng import.")
            if isinstance(node, ast.ImportFrom):
                roots = [(node.module or "").split(".")[0]]
            else:
                roots = [alias.name.split(".")[0] for alias in node.names]
            for root in roots:
                if root not in ALLOWED_IMPORTS:
                    raise UnsafeCodeError(f"Không được import module '{root}'.")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise UnsafeCodeError(f"Không được truy cập thuộc tính '{node.attr}'.")
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise UnsafeCodeError(f"Không được dùng '{node.id}'.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tier 1: Docker sibling container
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CONTAINER_STORAGE = "/srv/backend/app/storage"

# Derived from this file's own location, never from the working directory.
# The old fallback was os.path.abspath("./backend/app/storage"), which is only
# correct when the process happens to be started from the repository root: from
# inside the container (cwd /srv/backend) it resolved to
# /srv/backend/backend/app/storage, and on a host shell started in backend/ it
# doubled the same way. A sibling container then mounted a path that does not
# exist, so the run saw an empty directory instead of its inputs.
#   sandbox.py -> agent/ -> app/ -> storage
LOCAL_STORAGE = str(Path(__file__).resolve().parents[1] / "storage")


def get_host_storage_path() -> str:
    """Host-side path of the storage mount, for handing to a sibling container.

    A sibling container is created by the SAME daemon as this one, so its bind
    mounts are resolved against the host filesystem — a path that is valid in
    here is meaningless out there. Asking the daemon what this container's own
    mount points at is the only way to translate between the two.
    """
    try:
        client = docker.from_env()
        container = client.containers.get(socket.gethostname())
        for m in container.attrs.get("Mounts", []):
            if m.get("Destination") == CONTAINER_STORAGE:
                return m.get("Source")
    except Exception:
        pass
    # Not containerised (or the mount is absent): this process IS on the host,
    # so its own storage directory is already a host path.
    return LOCAL_STORAGE


def _docker_client():
    """Docker client with the sandbox image present, or None to fall back."""
    if not DOCKER_AVAILABLE:
        return None
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:
        logger.warning(f"Docker is not available: {exc}. Falling back to local execution.")
        return None
    try:
        client.images.get(SANDBOX_IMAGE)
    except Exception:
        # The path in this hint used to be `-f Dockerfile.sandbox ... .`, which
        # fails from the repository root because the file lives under backend/.
        # A build command that does not run is why this image stayed unbuilt.
        logger.warning(
            f"Sandbox image '{SANDBOX_IMAGE}' not found - build it with "
            "`docker build -f backend/Dockerfile.sandbox -t ai-dashboard-sandbox backend/` "
            "Falling back to LOCAL execution: generated code will run inside this "
            "process, guarded only by the AST scan."
        )
        return None
    return client


# Runs inside the container. Results cross back as JSON (+parquet for frames),
def _probe_source() -> str:
    """The join probe, as source, prepended to the wrapper the container runs.

    The sandbox image carries pandas but none of this application's code, so the
    measurement has to travel with the job. Shipping the actual source of the
    real functions — rather than a hand-written copy — is what keeps the two
    execution tiers measuring the same thing. Both depend on nothing beyond
    pandas, which the image already has.
    """
    import inspect

    from app.data import join_guard, join_probe

    return (
        # Must come first: these functions annotate their parameters with
        # pd.DataFrame, and an annotation is evaluated when the `def` runs — not
        # when the function is called. join_guard gets away without this because
        # it has `from __future__ import annotations`; the injected copy does not.
        "import pandas as pd\n\n"
        + inspect.getsource(join_guard._numeric_columns) + "\n\n"
        + inspect.getsource(join_guard.measure_join) + "\n\n"
        + inspect.getsource(join_probe.install) + "\n\n"
    )


# NEVER pickle: unpickling attacker-written bytes on the host would be RCE.
_WRAPPER_SOURCE = r'''
import json
import os

import numpy as np
import pandas as pd

MAX_RESULT_ROWS = 200


def _to_native(v):
    if isinstance(v, dict):
        return {str(k): _to_native(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_native(x) for x in v]
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        f = float(v)
        return None if pd.isna(f) else f
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, pd.Timestamp):
        return str(v)
    if isinstance(v, float) and pd.isna(v):
        return None
    return v


def _serialize(value, out_dir):
    if isinstance(value, pd.DataFrame):
        truncated = len(value) > MAX_RESULT_ROWS
        head = value.head(MAX_RESULT_ROWS).copy()
        for col in head.columns:
            if pd.api.types.is_datetime64_any_dtype(head[col]):
                head[col] = head[col].astype(str)
        rows = head.astype(object).where(pd.notna(head), "").values.tolist()
        return {"kind": "table", "result": {
            "columns": [str(c) for c in head.columns],
            "rows": _to_native(rows),
            "total_rows": int(len(value)),
            "truncated": truncated,
        }}
    if isinstance(value, pd.Series):
        return _serialize(value.reset_index(), out_dir)
    if isinstance(value, dict) and value and all(isinstance(x, pd.DataFrame) for x in value.values()):
        files = {}
        for i, (name, frame) in enumerate(value.items()):
            fn = "out_%d.parquet" % i
            frame.to_parquet(os.path.join(out_dir, fn))
            files[str(name)] = fn
        return {"kind": "dataframes", "result": files}
    if isinstance(value, (np.integer, np.floating)):
        return {"kind": "scalar", "result": _to_native(value)}
    if isinstance(value, (int, float, str, bool)):
        return {"kind": "scalar", "result": value}
    if isinstance(value, (dict, list)):
        return {"kind": "json", "result": _to_native(value)}
    return {"kind": "text", "result": str(value)[:2000]}


try:
    with open("/workspace/config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    result_var = config.get("result_var", "result")

    with open("/workspace/mappings.json", "r", encoding="utf-8") as f:
        mappings = json.load(f)

    dfs = {}
    for sid, filename in mappings.items():
        dfs[sid] = pd.read_parquet(os.path.join("/workspace", filename))
    df = next(iter(dfs.values())) if dfs else None

    namespace = {"df": df, "dfs": dfs, "pd": pd, "np": np, result_var: None}

    # Enrich namespace and dfs keys with clean sheet names to prevent NameErrors/KeyErrors
    import re
    enriched_dfs = dict(dfs)
    for sid, frame in list(dfs.items()):
        parts = sid.split("::")
        sheet_name = parts[-1] if parts else sid
        var_name = re.sub(r"\W+", "_", sheet_name)
        if var_name.isidentifier():
            namespace[var_name] = frame
            enriched_dfs[var_name] = frame
    namespace["dfs"] = enriched_dfs

    if os.path.exists("/workspace/helper.py"):
        with open("/workspace/helper.py", "r", encoding="utf-8") as f:
            exec(compile(f.read(), "helper.py", "exec"), namespace)

    with open("/workspace/code.txt", "r", encoding="utf-8") as f:
        code = f.read()
    # Join probe: measure every merge the snippet performs. The two functions
    # below are appended to this file by the host from join_guard/join_probe, so
    # there is no second copy of the logic to drift.
    _joins = []
    try:
        _names = {id(v): k.split("::")[-1] for k, v in enriched_dfs.items()
                  if isinstance(v, pd.DataFrame)}
        _uninstall = install(pd, measure_join, _joins, _names)
    except Exception:
        _uninstall = None

    try:
        exec(compile(code, "<snippet>", "exec"), namespace)
    finally:
        if _uninstall is not None:
            try:
                _uninstall()
            except Exception:
                pass

    value = namespace.get(result_var)
    if value is None and result_var == "dataframes" and isinstance(namespace.get("df"), pd.DataFrame):
        value = {"Data": namespace["df"]}

    if value is None:
        payload = {"status": "error",
                   "error": "Code khong gan ket qua vao bien `%s`." % result_var}
    else:
        payload = {"status": "success", "joins": _joins}
        payload.update(_serialize(value, "/workspace"))
    with open("/workspace/result.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=str)
except Exception as e:
    with open("/workspace/result.json", "w", encoding="utf-8") as f:
        json.dump({"status": "error", "error": "%s: %s" % (type(e).__name__, e)},
                  f, ensure_ascii=False)
'''


def _run_in_container(
    code: str,
    dataframes: dict[str, pd.DataFrame],
    result_var: str = "result",
    helper_source: str = "",
    timeout: float = CHAT_TIMEOUT_S,
) -> dict | None:
    """Run code in an isolated sibling container. Returns None if Docker/the
    sandbox image is unavailable (caller falls back to local execution)."""
    client = _docker_client()
    if client is None:
        return None

    local_run_dir = None
    try:
        local_storage = CONTAINER_STORAGE if os.path.isdir(CONTAINER_STORAGE) else LOCAL_STORAGE
        os.makedirs(local_storage, exist_ok=True)

        host_storage = get_host_storage_path()

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        local_run_dir = os.path.join(local_storage, run_id)
        os.makedirs(local_run_dir, exist_ok=True)
        host_run_dir = os.path.join(host_storage, run_id)

        # Inputs cross as parquet + JSON only.
        df_mappings = {}
        for idx, (sid, df) in enumerate(dataframes.items()):
            filename = f"df_{idx}.parquet"
            df.to_parquet(os.path.join(local_run_dir, filename))
            df_mappings[sid] = filename

        with open(os.path.join(local_run_dir, "mappings.json"), "w", encoding="utf-8") as f:
            json.dump(df_mappings, f, ensure_ascii=False)
        with open(os.path.join(local_run_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"result_var": result_var}, f)
        with open(os.path.join(local_run_dir, "code.txt"), "w", encoding="utf-8") as f:
            f.write(code)
        if helper_source:
            with open(os.path.join(local_run_dir, "helper.py"), "w", encoding="utf-8") as f:
                f.write(helper_source)
        with open(os.path.join(local_run_dir, "wrapper.py"), "w", encoding="utf-8") as f:
            f.write(_probe_source() + _WRAPPER_SOURCE)

        container = client.containers.run(
            image=SANDBOX_IMAGE,
            command="python /workspace/wrapper.py",
            volumes={host_run_dir: {"bind": "/workspace", "mode": "rw"}},
            mem_limit="512m",
            network_mode="none",
            nano_cpus=1_000_000_000,  # 1 CPU core limit
            detach=True,
        )

        try:
            container.wait(timeout=timeout)
        except Exception:
            try:
                container.kill()
            except Exception:
                pass
            container.remove()
            return {"ok": False, "error": f"TimeoutError: Code chạy quá {timeout:.0f}s trong sandbox."}
        container.remove()

        result_path = os.path.join(local_run_dir, "result.json")
        if not os.path.exists(result_path):
            return {"ok": False, "error": "Sandbox không trả về kết quả. Hãy chắc chắn code gán kết quả vào biến yêu cầu."}

        with open(result_path, "r", encoding="utf-8") as f:
            res_data = json.load(f)

        if res_data.get("status") != "success":
            return {"ok": False, "error": res_data.get("error", "Unknown sandbox error")}

        # Merges measured inside the container, published the same way the local
        # tier publishes its own — so callers never need to know which ran.
        join_measurements.set(res_data.get("joins") or [])

        kind = res_data.get("kind")
        if kind == "dataframes":
            # Frames come back as parquet files written by the container.
            frames = {}
            for name, fn in res_data["result"].items():
                frames[name] = pd.read_parquet(os.path.join(local_run_dir, fn))
            return {"ok": True, "kind": "dataframes", "result": frames}

        return {"ok": True, "kind": kind, "result": res_data.get("result")}

    except Exception as e:
        logger.exception("Error in containerized execution")
        return {"ok": False, "error": f"Sandbox error: {e}"}
    finally:
        if local_run_dir:
            shutil.rmtree(local_run_dir, ignore_errors=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tier 2: in-process restricted execution
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _safe_import(name, *args, **kwargs):
    root = name.split(".")[0]
    if root not in ALLOWED_IMPORTS:
        raise ImportError(f"Import module '{root}' không được phép trong sandbox.")
    return __import__(name, *args, **kwargs)


def _run_with_timeout(fn, timeout: float):
    """Run fn() in a daemon thread; return (value, error). On timeout the thread
    is abandoned (can't be force-killed) but its restricted namespace keeps it
    harmless."""
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001 - surface to the agent as a result
            box["error"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, TimeoutError(f"Code chạy quá {timeout:.0f}s.")
    return box.get("value"), box.get("error")


def _run_local(
    code: str,
    extra_namespace: dict,
    result_var: str,
    timeout: float,
    allow_imports: bool = False,
):
    """Scan then execute in a restricted single-dict namespace.
    Returns (value, error_message)."""
    try:
        scan_code(code, allow_imports=allow_imports)
    except UnsafeCodeError as exc:
        return None, str(exc)

    builtins = dict(_SAFE_BUILTINS)
    if allow_imports:
        builtins["__import__"] = _safe_import

    # One dict as BOTH globals and locals — see the wrapper comment: with a
    # split dict, functions defined by the snippet can't see pd/np/df.
    namespace = {"__builtins__": builtins, "pd": pd, "np": np, result_var: None}
    namespace.update(extra_namespace)

    # Enrich namespace and dfs keys with clean sheet names to prevent NameErrors/KeyErrors
    if "dfs" in namespace:
        import re
        dfs = namespace["dfs"]
        enriched_dfs = dict(dfs)
        for sid, frame in list(dfs.items()):
            parts = sid.split("::")
            sheet_name = parts[-1] if parts else sid
            var_name = re.sub(r"\W+", "_", sheet_name)
            if var_name.isidentifier():
                namespace[var_name] = frame
                enriched_dfs[var_name] = frame
        namespace["dfs"] = enriched_dfs

    # Watch every merge the snippet performs. Installed around this one call and
    # removed in `finally`, because pd.merge is process-global and leaving it
    # patched would follow every other request in the backend.
    from app.data.join_guard import measure_join
    from app.data.join_probe import install as install_join_probe

    joins: list[dict] = []
    names = {id(v): k.split("::")[-1] for k, v in (extra_namespace.get("dfs") or {}).items()
             if isinstance(v, pd.DataFrame)}
    uninstall = install_join_probe(pd, measure_join, joins, names)

    def _exec():
        exec(compile(code, "<snippet>", "exec"), namespace)  # noqa: S102
        return namespace.get(result_var)

    try:
        value, error = _run_with_timeout(_exec, timeout)
    finally:
        uninstall()
    join_measurements.set(joins)

    if error is not None:
        return None, f"{type(error).__name__}: {error}"
    if value is None and result_var == "dataframes" and isinstance(namespace.get("df"), pd.DataFrame):
        value = {"Data": namespace["df"]}
    if value is None:
        return None, f"Code không gán kết quả vào biến `{result_var}`."
    return value, None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Public entry points
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_pandas(code: str, dataframes: dict[str, pd.DataFrame],
               skills_env: dict | None = None, skills_source: str = "") -> dict:
    """Chat-agent snippets: `code` must assign to `result`.

    In scope: df (first table), dfs (all tables), pd, np, plus any curated /
    learned skill functions (provided as container helper source and as loaded
    callables locally). The snippet itself still may not import — helpers are
    pre-defined, so the AI just calls them.

    Returns a JSON-serializable dict:
      {"ok": True,  "result": <serialized>, "kind": "table"|"scalar"|"text"|"json",
       "join_warnings": [str], "non_additive": [str]}
      {"ok": False, "error": "..."}

    `join_warnings` is the guard the dashboard path has always had and this one
    never did: a merge written by the model can silently multiply the very
    totals the user trusts most, and nothing here used to notice.
    """
    if not dataframes:
        return {"ok": False, "error": "Chưa có dữ liệu nào được upload."}

    # Scan even before the container run: cheap, and rejects garbage early.
    try:
        scan_code(code, allow_imports=False)
    except UnsafeCodeError as exc:
        return {"ok": False, "error": str(exc)}

    container_res = _run_in_container(code, dataframes, helper_source=skills_source, timeout=CHAT_TIMEOUT_S)
    if container_res is not None:
        if container_res["ok"]:
            return {"ok": True, "kind": container_res["kind"], "result": container_res["result"],
                    **_join_verdict()}
        return {"ok": False, "error": container_res["error"]}

    smart_dfs = SmartDataframeDict(dataframes)
    first_df = next(iter(dataframes.values())) if dataframes else None
    local_env = dict(skills_env or {})
    local_env["df"] = first_df
    local_env["dfs"] = smart_dfs
    for sid, frame in (dataframes or {}).items():
        sheet_part = sid.split("::")[-1] if "::" in sid else sid
        var_name = "".join(c if c.isalnum() else "_" for c in sheet_part)
        if var_name.isidentifier() and var_name not in local_env:
            local_env[var_name] = frame

    value, error = _run_local(
        code, local_env,
        result_var="result", timeout=CHAT_TIMEOUT_S,
    )
    if error is not None:
        return {"ok": False, "error": error}
    return {"ok": True, **_serialize(value), **_join_verdict()}


def _join_verdict() -> dict:
    """Judge whatever the last run measured, then clear it so a later snippet
    that performs no merge cannot inherit the previous one's warnings."""
    from app.data.join_probe import judge_all

    measured = join_measurements.get() or []
    join_measurements.set(None)
    if not measured:
        return {}
    verdict = judge_all(measured)
    out = {}
    if verdict["warnings"]:
        out["join_warnings"] = verdict["warnings"]
    if verdict["non_additive"]:
        out["non_additive"] = verdict["non_additive"]
    return out


def run_layout_script(
    code: str,
    df: pd.DataFrame,
    skills_env: dict | None = None,
    skills_source: str = "",
) -> dict:
    """Code-Interpreter scripts: `code` must assign a dict to `layout`.

    Skills are provided as source (container helper) and as loaded callables
    (local fallback). Whitelisted imports are allowed.

    Returns {"ok": True, "layout": dict} or {"ok": False, "error": "..."}.
    """
    try:
        scan_code(code, allow_imports=True)
    except UnsafeCodeError as exc:
        return {"ok": False, "error": str(exc)}

    container_res = _run_in_container(
        code, {"data::main": df},
        result_var="layout", helper_source=skills_source, timeout=SCRIPT_TIMEOUT_S,
    )
    if container_res is not None:
        if not container_res["ok"]:
            return {"ok": False, "error": container_res["error"]}
        layout = container_res["result"]
        if container_res["kind"] != "json" or not isinstance(layout, dict):
            return {"ok": False, "error": "Biến `layout` không phải là dict hợp lệ."}
        return {"ok": True, "layout": layout}

    extra = dict(skills_env or {})
    extra["df"] = df
    extra["dfs"] = {"data::main": df}
    value, error = _run_local(
        code, extra, result_var="layout", timeout=SCRIPT_TIMEOUT_S, allow_imports=True,
    )
    if error is not None:
        return {"ok": False, "error": error}
    if not isinstance(value, dict):
        return {"ok": False, "error": "Biến `layout` không phải là dict hợp lệ."}
    return {"ok": True, "layout": value}


def run_datagen(code: str) -> dict:
    """Mock-data synthesizer scripts: `code` must assign a dict of DataFrames
    to `dataframes` (or a single DataFrame to `df`). Whitelisted imports allowed.

    Returns {"ok": True, "dataframes": {name: DataFrame}} or {"ok": False, "error"}.
    """
    try:
        scan_code(code, allow_imports=True)
    except UnsafeCodeError as exc:
        return {"ok": False, "error": str(exc)}

    container_res = _run_in_container(
        code, {}, result_var="dataframes", timeout=SCRIPT_TIMEOUT_S,
    )
    if container_res is not None:
        if not container_res["ok"]:
            return {"ok": False, "error": container_res["error"]}
        if container_res["kind"] != "dataframes":
            return {"ok": False, "error": "Không tìm thấy biến `dataframes` kiểu dict[str, DataFrame] hoặc `df` kiểu DataFrame."}
        return {"ok": True, "dataframes": container_res["result"]}

    value, error = _run_local(
        code, {}, result_var="dataframes", timeout=SCRIPT_TIMEOUT_S, allow_imports=True,
    )
    if error is not None:
        return {"ok": False, "error": error}
    if not isinstance(value, dict) or not value or not all(
        isinstance(v, pd.DataFrame) for v in value.values()
    ):
        return {"ok": False, "error": "Không tìm thấy biến `dataframes` kiểu dict[str, DataFrame] hoặc `df` kiểu DataFrame."}
    return {"ok": True, "dataframes": value}


def _serialize(value) -> dict:
    """Turn a pandas/numpy/python result into JSON the model + UI can read,
    capped at MAX_RESULT_ROWS."""
    if isinstance(value, pd.DataFrame):
        truncated = len(value) > MAX_RESULT_ROWS
        head = value.head(MAX_RESULT_ROWS).copy()
        for col in head.columns:
            if pd.api.types.is_datetime64_any_dtype(head[col]):
                head[col] = head[col].astype(str)
        return {
            "kind": "table",
            "result": {
                "columns": [str(c) for c in head.columns],
                "rows": head.fillna("").astype(object).where(pd.notna(head), "").values.tolist(),
                "total_rows": int(len(value)),
                "truncated": truncated,
            },
        }
    if isinstance(value, pd.Series):
        return _serialize(value.reset_index())
    if isinstance(value, (np.integer, np.floating)):
        return {"kind": "scalar", "result": value.item()}
    if isinstance(value, (int, float, str, bool)):
        return {"kind": "scalar", "result": value}
    # Fallback: stringify anything else (dict, list, etc.)
    return {"kind": "text", "result": str(value)[:2000]}
