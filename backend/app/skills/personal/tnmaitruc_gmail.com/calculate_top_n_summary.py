import pandas as pd

def calculate_top_n_summary(df: pd.DataFrame, group_column: str, value_column: str, n: int = 10) -> list:
    """Calculates the top N items by sum of a value column, grouped by a specified column.

    This function is useful for generating summaries for charts and reports, 
    identifying the most significant contributors to a total.

    Args:
        df (pd.DataFrame): The input DataFrame.
        group_column (str): The name of the column to group by.
        value_column (str): The name of the column containing the values to sum.
        n (int, optional): The number of top items to return. Defaults to 10.

    Returns:
        list: A list of dictionaries, where each dictionary contains 'label' (the group name) 
              and 'value' (the summed value for that group), sorted in descending order.
              Returns an empty list if the input DataFrame is empty or columns are invalid.
    """
    if df.empty or group_column not in df.columns or value_column not in df.columns:
        return []

    summary = df.groupby(group_column)[value_column].sum()
    top_n_summary = summary.nlargest(n)

    data = [{'label': str(idx), 'value': float(val)} for idx, val in top_n_summary.items()]
    return data
