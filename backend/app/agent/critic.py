"""Data Critic & Statistical Guard Agent (Giác quan phản biện & Giám định số liệu).

Runs 100% deterministically in Python (0ms AI latency) to analyze the computed
results from the Sandbox, detecting:
1. Outliers and extreme distributions (Z-score, IQR).
2. Business paradoxes (negative profit/margins, zero revenues, extreme concentration).
3. Data quality warnings (null proportions, cardinality issues).
4. Perceptual signals to trigger Alpha Agent's Mind Shifts (Bẻ lái nhận thức).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import numpy as np
import pandas as pd


@dataclass
class CriticVerdict:
    is_valid: bool = True
    has_anomalies: bool = False
    anomaly_signals: list[str] = field(default_factory=list)
    statistical_insights: list[str] = field(default_factory=list)
    risk_level: str = "low"  # "low", "medium", "high"
    suggested_drill_down: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "has_anomalies": self.has_anomalies,
            "anomaly_signals": self.anomaly_signals,
            "statistical_insights": self.statistical_insights,
            "risk_level": self.risk_level,
            "suggested_drill_down": self.suggested_drill_down,
        }

    def format_for_monologue(self) -> str:
        """Format perceptual feedback for the Alpha Orchestrator's inner monologue."""
        if not self.has_anomalies and not self.statistical_insights:
            return "✅ Critic: Số liệu hợp lệ, không phát hiện bất thường logic."
        lines = []
        if self.anomaly_signals:
            lines.append("🚨 CẢNH BÁO TỪ CRITIC (BẤT THƯỜNG DỮ LIỆU):")
            for sig in self.anomaly_signals:
                lines.append(f"  - ⚠️ {sig}")
        if self.statistical_insights:
            lines.append("📊 TÍN HIỆU THỐNG KÊ:")
            for ins in self.statistical_insights:
                lines.append(f"  - 🔍 {ins}")
        if self.suggested_drill_down:
            lines.append("💡 GỢI Ý ĐÀO SÂU (DRILL-DOWN):")
            for d in self.suggested_drill_down:
                lines.append(f"  - 🎯 {d}")
        return "\n".join(lines)


def critique_dataframe(df: pd.DataFrame, max_cols: int = 15) -> CriticVerdict:
    """Perform fast, non-blocking statistical checks over a result DataFrame."""
    verdict = CriticVerdict()
    if df is None or len(df) == 0:
        verdict.is_valid = False
        verdict.has_anomalies = True
        verdict.risk_level = "high"
        verdict.anomaly_signals.append("Bảng kết quả rỗng (0 dòng). Cần kiểm tra lại điều kiện lọc hoặc tên cột.")
        return verdict

    n_rows = len(df)
    cols = list(df.columns[:max_cols])

    # 1. Numeric column analysis
    num_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    for col in num_cols:
        series = df[col].dropna()
        if len(series) < 3:
            continue

        c_name = str(col).lower()
        s_min, s_max, s_mean = float(series.min()), float(series.max()), float(series.mean())
        s_sum = float(series.sum())

        # Check negative amounts on metrics that are usually positive
        is_revenue_like = any(term in c_name for term in ["doanh thu", "revenue", "tiền", "amount", "sales", "giá"])
        if is_revenue_like and s_min < 0:
            verdict.has_anomalies = True
            verdict.anomaly_signals.append(
                f"Cột '{col}' có giá trị âm (min={s_min:,.2f}). Có thể là đơn trả hàng, giảm trừ hoặc lỗi nhập liệu."
            )
            verdict.suggested_drill_down.append(f"Lọc các dòng có `{col} < 0` để phân tích nguyên nhân giảm trừ")

        # Check extreme concentration (Monopoly vs Pareto)
        if s_sum > 0 and len(series) >= 5:
            top_val = float(series.max())
            ratio = top_val / s_sum
            if ratio > 0.85:
                # Extreme concentration / Monopoly -> Anomaly requiring drill-down
                verdict.has_anomalies = True
                verdict.anomaly_signals.append(
                    f"Độ tập trung cao bất thường ở cột '{col}': giá trị lớn nhất ({top_val:,.2f}) chiếm tới {ratio*100:.1f}% tổng số."
                )
                verdict.suggested_drill_down.append(f"Kiểm tra thực thể có `{col}` lớn nhất xem có phải ngoại lai (outlier)")
            elif ratio >= 0.65:
                # Normal/moderate Pareto distribution -> Insight for Storyteller without forcing expensive Mind Shift
                verdict.statistical_insights.append(
                    f"Phân phối Pareto: Giá trị lớn nhất ({top_val:,.2f}) chiếm {ratio*100:.1f}% tổng số cột '{col}'."
                )

        # Outlier detection: robust IQR, falling back to a z-score.
        #
        # The n >= 8 gate is the whole reason this block does not double-flag an
        # ordinary Pareto breakdown. Quartiles estimated from five points are
        # noise, not statistics: a ranked 5-row result like [75, 10, 5, 5, 5]
        # yields q75=10 and IQR=5, so the leading category clears any 3xIQR
        # fence purely because it leads. Concentration ratio cannot separate the
        # two cases either — the genuine outlier below (a 500ms latency among
        # 10-14ms) sits at 82.6% of the sum while that harmless Pareto sits at
        # 75%. Sample size separates them cleanly, and it is the honest reason:
        # with fewer than ~8 points there is no "rest of the distribution" to be
        # detached from. The z-score branch already required this; the IQR
        # branch admitting n=5 is what let concentration masquerade as anomaly.
        if len(series) >= 8:
            q25, q75 = float(series.quantile(0.25)), float(series.quantile(0.75))
            iqr = q75 - q25
            if iqr > 0 and (s_max - q75) > 3.0 * iqr:
                verdict.has_anomalies = True
                verdict.statistical_insights.append(
                    f"Cột '{col}' xuất hiện giá trị ngoại lai cực trị theo IQR ({s_max:,.2f} so với Q3={q75:,.2f}, IQR={iqr:,.2f})."
                )
                verdict.suggested_drill_down.append(f"Phân tích các dòng có `{col} > {q75 + 3.0*iqr:,.2f}`")
            else:
                std = float(series.std()) if len(series) > 1 else 0
                if std > 0 and (s_max - s_mean) > 3.0 * std:
                    verdict.has_anomalies = True
                    verdict.statistical_insights.append(
                        f"Cột '{col}' xuất hiện giá trị cực trị vượt 3 độ lệch chuẩn ({s_max:,.2f} vs TB {s_mean:,.2f})."
                    )

    # 2. Text / Categorical columns cardinality check
    cat_cols = [c for c in cols if pd.api.types.is_string_dtype(df[c]) or pd.api.types.is_object_dtype(df[c])]
    for col in cat_cols:
        series = df[col].dropna()
        if len(series) == 0:
            continue
        n_unique = series.nunique()
        if n_unique == 1 and n_rows > 5:
            verdict.statistical_insights.append(
                f"Cột '{col}' chỉ có duy nhất 1 giá trị ('{series.iloc[0]}') trên toàn bộ {n_rows} dòng."
            )

    if verdict.anomaly_signals:
        verdict.risk_level = "medium" if len(verdict.anomaly_signals) == 1 else "high"

    return verdict


def critique_scalar(value: Any, label: str = "") -> CriticVerdict:
    """Critique a single scalar metric."""
    verdict = CriticVerdict()
    if value is None:
        verdict.is_valid = False
        verdict.has_anomalies = True
        verdict.anomaly_signals.append("Giá trị tính toán trả về None/Null.")
        return verdict

    if isinstance(value, (int, float)):
        f_val = float(value)
        lbl_lower = label.lower()
        if np.isnan(f_val) or np.isinf(f_val):
            verdict.is_valid = False
            verdict.has_anomalies = True
            verdict.anomaly_signals.append("Kết quả tính toán bị lỗi NaN hoặc Vô cực (Inf) do chia cho 0.")
        elif any(k in lbl_lower for k in ["tỷ lệ", "tỉ lệ", "rate", "percent", "%"]) and (f_val > 100 or f_val < -100):
            verdict.has_anomalies = True
            verdict.statistical_insights.append(
                f"Chỉ số tỷ lệ '{label}' có giá trị {f_val} nằm ngoài dải thông thường [-100%, 100%]."
            )
        elif any(k in lbl_lower for k in ["doanh thu", "sales", "revenue", "tiền"]) and f_val < 0:
            verdict.has_anomalies = True
            verdict.anomaly_signals.append(f"Chỉ số doanh thu '{label}' có giá trị âm ({f_val:,.2f}).")

    return verdict


def critique_execution(kind: str, result: Any, label: str = "") -> CriticVerdict:
    """Universal dispatcher for reviewing Sandbox execution results."""
    if kind == "table":
        if isinstance(result, dict) and "columns" in result and "rows" in result:
            df = pd.DataFrame(result["rows"], columns=result["columns"])
            return critique_dataframe(df)
        if isinstance(result, pd.DataFrame):
            return critique_dataframe(result)
    elif kind == "scalar":
        return critique_scalar(result, label=label)
    return CriticVerdict()
