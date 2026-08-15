import pandas as pd

def calculate_time_series_kpi(df, date_col, value_col, period_freq='M'):
    """
    Calculates current period total and compares it with the previous period.
    
    Args:
        df (pd.DataFrame): Input dataframe.
        date_col (str): Name of the column containing date information.
        value_col (str): Name of the column containing numeric values to aggregate.
        period_freq (str): Frequency for grouping (e.g., 'M' for month, 'D' for day).
        
    Returns:
        dict: A dictionary containing current_value, previous_value, and growth_rate.
    """
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df['period'] = df[date_col].dt.to_period(period_freq)
    
    grouped = df.groupby('period')[value_col].sum().sort_index()
    
    if len(grouped) < 2:
        return {"current_value": float(grouped.iloc[-1]) if not grouped.empty else 0, "previous_value": 0, "growth_rate": 0}
    
    current_val = float(grouped.iloc[-1])
    prev_val = float(grouped.iloc[-2])
    growth = ((current_val - prev_val) / prev_val) if prev_val != 0 else 0
    
    return {
        "current_value": current_val,
        "previous_value": prev_val,
        "growth_rate": growth
    }