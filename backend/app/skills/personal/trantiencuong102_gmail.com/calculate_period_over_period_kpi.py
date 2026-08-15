import pandas as pd
import numpy as np

def calculate_period_over_period_kpi(df, date_col, value_col, id_col, current_period, previous_period, period_format='%Y-%m'):
    """
    Calculates KPI metrics (Sum, Count, Average) for two periods and compares them.
    
    Args:
        df (pd.DataFrame): Input dataframe.
        date_col (str): Column name containing date information.
        value_col (str): Column name for numerical values (e.g., Sales).
        id_col (str): Column name for unique identifiers (e.g., Bill ID).
        current_period (str): The current period string (e.g., '2026-01').
        previous_period (str): The previous period string (e.g., '2025-12').
        period_format (str): Format to parse the date column.
        
    Returns:
        dict: A dictionary containing KPIs with current values and comparison values.
    """
    df = df.copy()
    df['period'] = pd.to_datetime(df[date_col]).dt.strftime(period_format)
    
    def get_metrics(period):
        subset = df[df['period'] == period]
        total_val = float(subset[value_col].sum())
        count_val = int(subset[id_col].nunique())
        avg_val = float(total_val / count_val) if count_val > 0 else 0.0
        return total_val, count_val, avg_val

    curr_sales, curr_bills, curr_avg = get_metrics(current_period)
    prev_sales, prev_bills, prev_avg = get_metrics(previous_period)

    return {
        "total_sales": {"value": curr_sales, "compare": prev_sales},
        "total_bills": {"value": curr_bills, "compare": prev_bills},
        "avg_bill_value": {"value": curr_avg, "compare": prev_avg}
    }