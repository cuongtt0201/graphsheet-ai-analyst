import pandas as pd

def calculate_top_n_with_others(df, category_col, value_col, n=10, other_label='Khác'):
    """
    Aggregates data by category, keeps the top N items, and groups the remainder into an 'Other' category.

    Args:
        df (pd.DataFrame): The input dataframe.
        category_col (str): The column name to group by.
        value_col (str): The column name to aggregate (sum).
        n (int): Number of top items to keep.
        other_label (str): Label for the aggregated remainder group.

    Returns:
        pd.DataFrame: A dataframe with columns [category_col, value_col] containing top N + 1 rows.
    """
    grouped = df.groupby(category_col)[value_col].sum().reset_index()
    grouped = grouped.sort_values(value_col, ascending=False)

    if len(grouped) > n:
        top_n = grouped.head(n)
        other_sum = grouped.iloc[n:][value_col].sum()
        other_row = pd.DataFrame([{category_col: other_label, value_col: other_sum}])
        return pd.concat([top_n, other_row], ignore_index=True)
    
    return grouped