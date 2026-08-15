import pandas as pd

def calculate_monthly_kpi_comparison(df, date_col, value_col, id_col):
    """
    Calculates current month vs previous month KPIs for revenue and unique order counts.

    Args:
        df (pd.DataFrame): The input dataframe.
        date_col (str): Name of the column containing datetime objects.
        value_col (str): Name of the column containing numeric values to sum.
        id_col (str): Name of the column containing unique identifiers (e.g., order IDs).

    Returns:
        dict: A dictionary containing 'revenue' and 'orders' with current and previous month values.
    """
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
    df['month_period'] = df[date_col].dt.to_period('M').astype(str)
    df[value_col] = pd.to_numeric(df[value_col], errors='coerce').fillna(0)

    max_month = df['month_period'].max()
    dt_max = pd.to_datetime(max_month + '-01')
    prev_month = (dt_max - pd.DateOffset(months=1)).strftime('%Y-%m')

    df_current = df[df['month_period'] == max_month]
    df_prev = df[df['month_period'] == prev_month]

    return {
        "revenue": {
            "current": float(df_current[value_col].sum()),
            "previous": float(df_prev[value_col].sum())
        },
        "orders": {
            "current": int(df_current[id_col].nunique()),
            "previous": int(df_prev[id_col].nunique())
        }
    }