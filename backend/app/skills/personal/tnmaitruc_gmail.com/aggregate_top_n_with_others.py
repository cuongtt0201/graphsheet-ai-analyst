import pandas as pd

def aggregate_top_n_with_others(df, group_col, value_col, n=10, other_label='Khác'):
    """
    Groups data by a column, sums a value column, and aggregates all rows beyond the top N into an 'Others' category.

    Args:
        df (pd.DataFrame): The input dataframe.
        group_col (str): The column name to group by.
        value_col (str): The column name to sum.
        n (int): The number of top items to keep separate.
        other_label (str): The label to use for the aggregated remaining items.

    Returns:
        pd.DataFrame: A dataframe containing the top N items and the aggregated 'Others' row.
    """
    grouped = df.groupby(group_col)[value_col].sum().reset_index()
    grouped = grouped.sort_values(by=value_col, ascending=False)

    if len(grouped) > n:
        top_n = grouped.head(n)
        others_sum = grouped.iloc[n:][value_col].sum()
        others_df = pd.DataFrame([{group_col: other_label, value_col: others_sum}])
        return pd.concat([top_n, others_df], ignore_index=True)
    
    return grouped