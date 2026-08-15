import pandas as pd

def calculate_time_series_aggregation(df, date_col, value_col, freq='M'):
    """
    Aggregates a numeric column by a specific time frequency.

    Args:
        df (pd.DataFrame): The input dataframe.
        date_col (str): The name of the datetime column.
        value_col (str): The name of the numeric column to aggregate.
        freq (str): The pandas frequency string (e.g., 'M' for month, 'D' for day).

    Returns:
        pd.DataFrame: A dataframe with the aggregated time periods and values.
    """
    df_copy = df.copy()
    df_copy[date_col] = pd.to_datetime(df_copy[date_col])
    period_col = 'period'
    df_copy[period_col] = df_copy[date_col].dt.to_period(freq)
    result = df_copy.groupby(period_col)[value_col].sum().reset_index()
    result[period_col] = result[period_col].astype(str)
    return result