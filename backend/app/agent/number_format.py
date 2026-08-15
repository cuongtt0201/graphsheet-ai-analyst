"""Vietnamese number presentation for AI-written prose.

Why the backend formats instead of the prompt asking the model to: converting
866838347 -> "866,8 triệu" is arithmetic, and a model that slips one power of
ten writes a number that is wrong by 10x while still reading fluently. The
whole product is built on "backend computes, AI only writes", so the compact
form is computed here and handed to the model to copy verbatim.

Vietnamese convention: "." groups thousands, "," is the decimal separator.
"""


def fmt_vi(value) -> str:
    """Full number, Vietnamese separators: 29717277717.5 -> "29.717.277.717,5"."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if v.is_integer():
        return f"{int(v):,}".replace(",", ".")
    # Format with 2 decimals in en style first, then swap both separators via a
    # placeholder so the swap can't clobber its own output.
    return f"{v:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def fmt_vi_compact(value) -> str:
    """Short readable magnitude: 866838347 -> "866,8 triệu", 2.97e10 -> "29,7 tỷ".

    Returns "" when the number is small enough that the full form is already
    readable - the caller then has nothing extra to offer the model, and an
    unnecessary "38,1 nghìn" for a count of 38142 reads worse than the plain
    number.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.1f}".replace(".", ",") + " tỷ"
    if a >= 1e6:
        return f"{v / 1e6:.1f}".replace(".", ",") + " triệu"
    return ""


def describe(value) -> str:
    """"866.838.347 (≈ 866,8 triệu)" - the exact value plus, when useful, the
    magnitude a human actually reads. Both forms are grounded, so the
    number-verification gate accepts whichever one the model picks."""
    full = fmt_vi(value)
    compact = fmt_vi_compact(value)
    return f"{full} (≈ {compact})" if compact else full


NUMBER_STYLE_RULES = """QUY TẮC VIẾT SỐ (rất quan trọng cho người đọc):
- Khi một số đã kèm dạng rút gọn trong ngoặc "(≈ ...)", HÃY DÙNG DẠNG RÚT GỌN đó trong câu văn
  (ví dụ viết "866,8 triệu" thay vì "866.838.347"). TUYỆT ĐỐI không tự tính lại dạng rút gọn cho
  số nào chưa có sẵn — cứ giữ nguyên số đầy đủ.
- LUÔN nêu ĐƠN VỊ ngay sau con số, suy ra từ tên chỉ số: giá trị/doanh thu/tồn kho → "VNĐ";
  số lượng/đơn vị sản phẩm → "sản phẩm"; số đơn/hóa đơn → "đơn"; tỷ lệ vòng quay → "lần";
  phần trăm → "%". Người đọc không được phép đoán đơn vị.
- Số nguyên nhỏ (số lượng, số đơn) viết đầy đủ với dấu chấm phân cách nghìn kiểu Việt Nam
  (38.142 sản phẩm), không rút gọn thành "38,1 nghìn"."""
