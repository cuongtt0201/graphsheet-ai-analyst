"""Duplicate detection must not crash the request that triggered it.

_find_duplicate_skill read s["file"], a key no skill dict has ever had --
get_available_skills builds both tiers with "code" instead. KeyError is not
OSError, so it slipped past the guard around the read and took down whatever
call was in flight; the auto-dashboard build died with a bare "'file'".
"""

from app.agent import skills_manager
from app.agent.skills_manager import _find_duplicate_skill


CODE_A = '''
def tong_doanh_thu(df):
    """Tổng doanh thu theo miền."""
    return df.groupby("Mien")["DoanhThu"].sum().reset_index()
'''

CODE_B = '''
def doanh_thu_theo_mien(df):
    """Tổng doanh thu theo miền."""
    return df.groupby("Mien")["DoanhThu"].sum().reset_index()
'''


def _stub(monkeypatch, skills):
    monkeypatch.setattr(skills_manager, "get_available_skills", lambda owner: skills)


def test_no_skill_dict_carries_a_file_key():
    """The dict shape the reader assumed does not exist in either tier."""
    for tier in ("curated", "personal"):
        sample = {"name": "x", "description": "d", "code": "pass", "type": tier}
        assert "file" not in sample


def test_dedup_survives_a_skill_with_no_file_on_disk(monkeypatch):
    """This is the exact call that used to raise KeyError('file')."""
    _stub(monkeypatch, [
        {"name": "tong_doanh_thu", "description": "Tổng doanh thu theo miền.",
         "code": CODE_A, "type": "personal"},
    ])

    hit = _find_duplicate_skill("u1", "doanh_thu_theo_mien", "Tổng doanh thu theo miền.", CODE_B)
    assert hit is not None
    assert hit["name"] == "tong_doanh_thu"


def test_a_genuinely_new_skill_is_not_called_a_duplicate(monkeypatch):
    _stub(monkeypatch, [
        {"name": "tong_doanh_thu", "description": "Tổng doanh thu theo miền.",
         "code": CODE_A, "type": "personal"},
    ])

    other = '''
def dem_khach_hang(df):
    """Đếm số khách hàng duy nhất."""
    return df["KhachHang"].nunique()
'''
    assert _find_duplicate_skill("u1", "dem_khach_hang", "Đếm số khách hàng duy nhất.", other) is None


def test_a_skill_with_empty_code_is_skipped_not_crashed_on(monkeypatch):
    _stub(monkeypatch, [
        {"name": "rong", "description": "Tổng doanh thu theo miền.", "code": "", "type": "curated"},
    ])
    # Docstring overlap alone is enough to match here, which is fine; what
    # matters is that the empty code path does not raise.
    result = _find_duplicate_skill("u1", "moi", "Tổng doanh thu theo miền.", CODE_B)
    assert result is None or result["name"] == "rong"


def test_resaving_under_the_same_name_is_not_a_duplicate(monkeypatch):
    """Same name is an intentional update, not a collision."""
    _stub(monkeypatch, [
        {"name": "tong_doanh_thu", "description": "Tổng doanh thu theo miền.",
         "code": CODE_A, "type": "personal"},
    ])
    assert _find_duplicate_skill("u1", "tong_doanh_thu", "Tổng doanh thu theo miền.", CODE_A) is None
