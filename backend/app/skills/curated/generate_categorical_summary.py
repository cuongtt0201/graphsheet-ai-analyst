import pandas as pd

def generate_categorical_summary(df, category_col, value_col, top_n=None):
    """
    Aggregates numerical values by a categorical column and optionally returns the top N results.

    Args:
        df (pd.DataFrame): The input dataframe.
        category_col (str): The column name to group by.
        value_col (str): The numerical column to aggregate (sum).
        top_n (int, optional): If provided, returns only the top N rows sorted by value.

    Returns:
        list: A list of dictionaries with 'label' and 'value' keys.
    """
    summary = df.groupby(category_col)[value_col].sum().reset_index()
    if top_n:
        summary = summary.sort_values(by=value_col, ascending=False).head(top_n)
    
    return [{'label': str(row[category_col]), 'value': float(row[value_col])} for _, row in summary.iterrows()]