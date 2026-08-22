"""Column display formats must come from meaning, never from the digits.

Adding thousands separators to every integer would render a year 2026 as
"2.026" and an order code 10023 as "10.023" -- the grid's own comment has warned
about this all along, which is why it formatted nothing but decimals.
"""

from app.data.display_format import column_formats


def _profile(*cols):
    return {"column_profiles": list(cols)}


def test_currency_comes_from_the_recorded_unit():
    prof = _profile({"name": "Doanh thu", "role": "measure"})
    sem = {"target_measures": [{"column": "Doanh thu", "unit": "VNĐ"}]}
    assert column_formats(prof, sem)["Doanh thu"] == "currency"


def test_currency_falls_back_to_the_column_name():
    """Semantics do not always run, and "Chi phí" is money either way."""
    prof = _profile({"name": "Chi phí", "role": "measure"}, {"name": "Lợi nhuận", "role": "measure"})
    fmt = column_formats(prof, None)
    assert fmt["Chi phí"] == "currency"
    assert fmt["Lợi nhuận"] == "currency"


def test_a_year_is_left_alone():
    """The exact case separators would damage."""
    prof = _profile({"name": "Năm", "role": "measure"}, {"name": "Quý", "role": "measure"})
    fmt = column_formats(prof, None)
    assert fmt["Năm"] == "plain"
    assert fmt["Quý"] == "plain"


def test_identifiers_are_never_given_a_number_format():
    prof = _profile({"name": "Mã CT", "role": "id"}, {"name": "SĐT", "role": "id"})
    fmt = column_formats(prof, None)
    assert fmt["Mã CT"] == "id"
    assert fmt["SĐT"] == "id"


def test_percent_applies_only_to_the_fraction_form():
    """A percent pattern multiplies by 100, so 85 would render as "8500%"."""
    sem = {"target_measures": [
        {"column": "Tỷ lệ", "unit": "%"},
        {"column": "Hoàn thành", "unit": "%"},
    ]}
    prof = _profile(
        {"name": "Tỷ lệ", "role": "measure", "max": 0.98},
        {"name": "Hoàn thành", "role": "measure", "max": 98.0},
    )
    fmt = column_formats(prof, sem)
    assert fmt["Tỷ lệ"] == "percent"
    assert fmt["Hoàn thành"] == "plain"


def test_non_numeric_roles_stay_plain():
    prof = _profile(
        {"name": "Miền", "role": "category"},
        {"name": "Ngày", "role": "date"},
        {"name": "Ghi chú", "role": "text"},
    )
    assert set(column_formats(prof, None).values()) == {"plain"}


def test_money_hints_match_whole_words_only():
    """"Giá" is money; a word merely containing those letters is not."""
    prof = _profile(
        {"name": "Giá bán", "role": "measure"},
        {"name": "Giai đoạn", "role": "measure"},
    )
    fmt = column_formats(prof, None)
    assert fmt["Giá bán"] == "currency"
    assert fmt["Giai đoạn"] == "plain"
