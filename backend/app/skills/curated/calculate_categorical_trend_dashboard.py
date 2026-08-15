import pandas as pd

def calculate_categorical_trend_dashboard(df, category_col, time_col, value_col):
    """
    Generates a structured dictionary containing aggregated metrics for categorical distribution and time-series trends.

    Args:
        df (pd.DataFrame): The input dataframe.
        category_col (str): The column name to group by for categorical distribution (e.g., 'Region').
        time_col (str): The column name representing time for trend analysis (e.g., 'Month').
        value_col (str): The numerical column to aggregate (e.g., 'Revenue').

    Returns:
        dict: A dictionary containing total sum, categorical distribution, and time-series trend data.
    """
    total_value = float(df[value_col].sum())
    
    regional_agg = df.groupby(category_col)[value_col].sum().reset_index()
    categorical_data = [{'label': str(row[category_col]), 'value': float(row[value_col])} for _, row in regional_agg.iterrows()]
    
    trend_agg = df.groupby(time_col)[value_col].sum().sort_index().reset_index()
    trend_data = [{'label': str(row[time_col]), 'value': float(row[value_col])} for _, row in trend_agg.iterrows()]
    
    return {
        "total": total_value,
        "categorical_distribution": categorical_data,
        "time_trend": trend_data
    }