import os
import shutil
import uuid
import json
from typing import Any
import pandas as pd
from fastapi import Request

STORAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage")
os.makedirs(STORAGE_DIR, exist_ok=True)


def _safe_filename(name: str) -> str:
    """Client-supplied upload filenames are untrusted: `os.path.join(dir, name)`
    resolves any ".." / absolute path / drive letter and would write OUTSIDE the
    session dir (e.g. dropping a .py into app/skills/, which then gets executed).
    Collapse to a bare basename with separators stripped."""
    base = os.path.basename(name.replace("\\", "/").rstrip("/"))
    base = base.replace("\\", "_").replace("/", "_").lstrip(".") or "file"
    return base

class LazySessionState(dict):
    def __init__(self, session_id: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_id = session_id
        self._parquet_paths: dict[str, str] = {}
        self._raw_paths: dict[str, str] = {}
        self._cleaned_path: str | None = None
        self.sdir = os.path.join(STORAGE_DIR, self.session_id)
        self.state_json_path = os.path.join(self.sdir, "state.json")
        self.load_from_disk()

    def load_from_disk(self):
        if os.path.exists(self.state_json_path):
            try:
                with open(self.state_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.items():
                    if k not in ("dataframes", "cleaned_df", "raw_files", "_parquet_paths", "_raw_paths", "_cleaned_path"):
                        super().__setitem__(k, v)
                self._parquet_paths = data.get("_parquet_paths", {})
                self._raw_paths = data.get("_raw_paths", {})
                self._cleaned_path = data.get("_cleaned_path")
            except Exception as e:
                print(f"Error loading state from disk: {e}")

    def save_metadata_to_disk(self):
        os.makedirs(self.sdir, exist_ok=True)
        try:
            serializable = {
                "_parquet_paths": self._parquet_paths,
                "_raw_paths": self._raw_paths,
                "_cleaned_path": self._cleaned_path
            }
            for k, v in self.items():
                if k not in ("dataframes", "cleaned_df", "raw_files"):
                    try:
                        json.dumps(v)
                        serializable[k] = v
                    except (TypeError, OverflowError):
                        pass
            with open(self.state_json_path, "w", encoding="utf-8") as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving state to disk: {e}")

    def __setitem__(self, key: str, value: Any) -> None:
        if key == "dataframes":
            if isinstance(value, dict) and value:
                sdir = os.path.join(self.sdir, "dataframes")
                if os.path.exists(sdir):
                    try:
                        shutil.rmtree(sdir)
                    except OSError:
                        pass
                os.makedirs(sdir, exist_ok=True)
                
                paths = {}
                for name, df in value.items():
                    if isinstance(df, pd.DataFrame):
                        safe_name = name.replace("::", "__").replace("/", "_").replace("\\", "_")
                        path = os.path.join(sdir, f"{safe_name}.parquet")
                        try:
                            df.to_parquet(path, index=False)
                        except Exception:
                            # Last-resort safety net: a column pyarrow still can't
                            # serialize must not sink the whole upload. Stringify
                            # every object/mixed column and retry so the file loads.
                            safe_df = df.copy()
                            for col in safe_df.columns:
                                if safe_df[col].dtype == object or str(safe_df[col].dtype) == "string":
                                    safe_df[col] = safe_df[col].apply(
                                        lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
                                    ).astype("string")
                            safe_df.to_parquet(path, index=False)
                        paths[name] = os.path.join("dataframes", f"{safe_name}.parquet").replace("\\", "/")
                    else:
                        paths[name] = df
                self._parquet_paths = paths
                super().__setitem__("dataframes", None)
            else:
                self._parquet_paths.clear()
                super().__setitem__("dataframes", value)
            self.save_metadata_to_disk()
        elif key == "raw_files":
            if isinstance(value, dict) and value:
                sdir = os.path.join(self.sdir, "raw")
                if os.path.exists(sdir):
                    try:
                        shutil.rmtree(sdir)
                    except OSError:
                        pass
                os.makedirs(sdir, exist_ok=True)
                
                paths = {}
                for idx, (name, content) in enumerate(value.items()):
                    if isinstance(content, bytes):
                        # Keep the original name as the dict key (lookups use it),
                        # but write under a sanitized, index-prefixed basename so a
                        # crafted filename can't escape `sdir` or collide.
                        safe = f"{idx}_{_safe_filename(name)}"
                        path = os.path.join(sdir, safe)
                        with open(path, "wb") as f:
                            f.write(content)
                        paths[name] = os.path.join("raw", safe).replace("\\", "/")
                    else:
                        paths[name] = content
                self._raw_paths = paths
                super().__setitem__("raw_files", None)
            else:
                self._raw_paths.clear()
                super().__setitem__("raw_files", value)
            self.save_metadata_to_disk()
        elif key == "cleaned_df":
            if isinstance(value, pd.DataFrame):
                os.makedirs(self.sdir, exist_ok=True)
                path = os.path.join(self.sdir, "cleaned_df.parquet")
                value.to_parquet(path, index=False)
                self._cleaned_path = "cleaned_df.parquet"
                super().__setitem__("cleaned_df", None)
            else:
                if self._cleaned_path:
                    abs_path = os.path.join(self.sdir, self._cleaned_path)
                    if os.path.exists(abs_path):
                        try:
                            os.remove(abs_path)
                        except OSError:
                            pass
                self._cleaned_path = None
                super().__setitem__("cleaned_df", value)
            self.save_metadata_to_disk()
        else:
            super().__setitem__(key, value)
            self.save_metadata_to_disk()

    def __getitem__(self, key: str) -> Any:
        if key == "dataframes":
            if not self._parquet_paths:
                return super().__getitem__("dataframes") if super().__contains__("dataframes") else {}
            dfs = {}
            for name, rel_path in self._parquet_paths.items():
                if isinstance(rel_path, str):
                    abs_path = os.path.join(self.sdir, rel_path)
                    if os.path.exists(abs_path):
                        dfs[name] = pd.read_parquet(abs_path)
                    else:
                        dfs[name] = rel_path
                else:
                    dfs[name] = rel_path
            return dfs
        elif key == "raw_files":
            if not self._raw_paths:
                return super().__getitem__("raw_files") if super().__contains__("raw_files") else {}
            raws = {}
            for name, rel_path in self._raw_paths.items():
                if isinstance(rel_path, str):
                    abs_path = os.path.join(self.sdir, rel_path)
                    if os.path.exists(abs_path):
                        with open(abs_path, "rb") as f:
                            raws[name] = f.read()
                    else:
                        raws[name] = rel_path
                else:
                    raws[name] = rel_path
            return raws
        elif key == "cleaned_df":
            if self._cleaned_path:
                abs_path = os.path.join(self.sdir, self._cleaned_path)
                if os.path.exists(abs_path):
                    return pd.read_parquet(abs_path)
            return super().__getitem__("cleaned_df") if super().__contains__("cleaned_df") else None
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        if key == "dataframes":
            return len(self._parquet_paths) > 0 or super().__contains__("dataframes")
        elif key == "raw_files":
            return len(self._raw_paths) > 0 or super().__contains__("raw_files")
        elif key == "cleaned_df":
            return (self._cleaned_path is not None and os.path.exists(os.path.join(self.sdir, self._cleaned_path))) or super().__contains__("cleaned_df")
        return super().__contains__(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def setdefault(self, key: str, default: Any = None) -> Any:
        if key not in self:
            self[key] = default
        return self[key]

    def pop(self, key: str, default: Any = None) -> Any:
        if key == "dataframes":
            sdir = os.path.join(self.sdir, "dataframes")
            if os.path.exists(sdir):
                try:
                    shutil.rmtree(sdir)
                except OSError:
                    pass
            self._parquet_paths.clear()
            res = super().pop(key, default)
            self.save_metadata_to_disk()
            return res
        elif key == "raw_files":
            sdir = os.path.join(self.sdir, "raw")
            if os.path.exists(sdir):
                try:
                    shutil.rmtree(sdir)
                except OSError:
                    pass
            self._raw_paths.clear()
            res = super().pop(key, default)
            self.save_metadata_to_disk()
            return res
        elif key == "cleaned_df":
            if self._cleaned_path:
                abs_path = os.path.join(self.sdir, self._cleaned_path)
                if os.path.exists(abs_path):
                    try:
                        os.remove(abs_path)
                    except OSError:
                        pass
            self._cleaned_path = None
            res = super().pop(key, default)
            self.save_metadata_to_disk()
            return res
        res = super().pop(key, default)
        self.save_metadata_to_disk()
        return res


_STORE: dict[str, LazySessionState] = {}


def get_session_id(request: Request) -> str:
    sid = request.session.get("sid")
    if not sid:
        sid = str(uuid.uuid4())
        request.session["sid"] = sid
    return sid


def get_state(request: Request) -> dict:
    sid = get_session_id(request)
    if sid not in _STORE:
        _STORE[sid] = LazySessionState(sid)
    return _STORE[sid]


def get_user_id(request: Request) -> str:
    """Stable identity key for graph memory. Chat mode works without Google
    login, so memory must too: use the logged-in email when present, else an
    anonymous key derived from the session id (still lets a returning browser
    session build up habits/recipes, just not portable across browsers)."""
    email = request.session.get("email")
    if email:
        return email
    return f"anon:{get_session_id(request)}"

