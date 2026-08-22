"""Dependencies must be pinned, and the sandbox must match the backend.

An unbounded ">=" is how google-genai reached 2.19 on a rebuild and killed every
LLM call in the app without a line of our code changing. These tests make that
failure mode visible at test time instead of at deploy time.
"""

import re
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parent.parent
REQUIREMENTS = BACKEND / "requirements.txt"
LOCK = BACKEND / "requirements.lock.txt"
SANDBOX_DOCKERFILE = BACKEND / "Dockerfile.sandbox"
DOCKERFILE = BACKEND / "Dockerfile"


def _pins(path: pathlib.Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]+\])?==(.+)$", line)
        if m:
            out[m.group(1).lower().replace("_", "-")] = m.group(2).strip()
    return out


def test_every_direct_dependency_is_pinned_exactly():
    unpinned = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            unpinned.append(line)
    assert not unpinned, f"Unpinned requirements will drift on rebuild: {unpinned}"


def test_lockfile_covers_every_direct_dependency():
    lock = _pins(LOCK)
    missing = [name for name in _pins(REQUIREMENTS) if name not in lock]
    assert not missing, f"Missing from requirements.lock.txt: {missing}"


def test_direct_pins_agree_with_the_lock():
    req, lock = _pins(REQUIREMENTS), _pins(LOCK)
    mismatched = {n: (v, lock[n]) for n, v in req.items() if n in lock and lock[n] != v}
    assert not mismatched, f"requirements.txt disagrees with the lock: {mismatched}"


def test_the_image_installs_from_the_lock_not_the_loose_list():
    """Installing requirements.txt would re-resolve transitive packages freely."""
    body = DOCKERFILE.read_text(encoding="utf-8")
    assert "pip install --no-cache-dir -r requirements.lock.txt" in body


def test_sandbox_data_stack_matches_the_backend():
    """Generated code runs in the sandbox and its result is shown as fact.

    If the two pandas differ, a dtype or a groupby default can differ with them,
    and the user reads numbers computed under rules the backend never applied.
    """
    lock = _pins(LOCK)
    dockerfile = SANDBOX_DOCKERFILE.read_text(encoding="utf-8")
    sandbox = dict(re.findall(r'"([A-Za-z0-9_.\-]+)==([^"]+)"', dockerfile))

    assert sandbox, "Dockerfile.sandbox has no exact pins"
    for name, version in sandbox.items():
        key = name.lower().replace("_", "-")
        assert key in lock, f"{name} pinned in the sandbox but absent from the lock"
        assert lock[key] == version, (
            f"{name}: sandbox has {version}, backend has {lock[key]} - "
            "the two run the same generated code and must agree"
        )
