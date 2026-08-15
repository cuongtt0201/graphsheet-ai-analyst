import pandas as pd

def get_top_n_and_others_aggregation(
    df: pd.DataFrame,
    group_col: str,
    value_col: str,
    n: int = 10,
    other_label: str = "Khác",
    aggregation_func: str = "sum"
) -> list:
    """
    Aggregates a value column by a categorical column, taking the top N categories
    and grouping the rest into an 'Other' category.

    This is useful for visualizing data with many categories where only the most
    significant ones are relevant, and the long tail can be combined.

    Args:
        df (pd.DataFrame): The input DataFrame.
        group_col (str): The name of the column to group by (categorical column).
        value_col (str): The name of the column to aggregate (numeric column).
        n (int, optional): The number of top categories to retain. Defaults to 10.
        other_label (str, optional): The label for the aggregated 'Other' category.
                                     Defaults to "Khác" (Vietnamese for "Other").
        aggregation_func (str, optional): The aggregation function to apply.
                                          Supported values are 'sum', 'mean', 'count', 'min', 'max'.
                                          Defaults to "sum".

    Returns:
        list: A list of dictionaries, where each dictionary represents a category
              with 'label' (category name) and 'value' (aggregated value).
              The list is sorted by value in descending order.

    Raises:
        ValueError: If `group_col` or `value_col` are not found in the DataFrame.
        ValueError: If `aggregation_func` is not one of the supported functions.
    """
    if group_col not in df.columns:
        raise ValueError(f"Column '{group_col}' not found in the DataFrame.")
    if value_col not in df.columns:
        raise ValueError(f"Column '{value_col}' not found in the DataFrame.")

    supported_funcs = ['sum', 'mean', 'count', 'min', 'max']
    if aggregation_func not in supported_funcs:
        raise ValueError(f"Unsupported aggregation function: '{aggregation_func}'. "
                         f"Supported functions are: {', '.join(supported_funcs)}")

    # Ensure value_col is numeric and handle NaNs before aggregation
    temp_df = df.copy()
    temp_df[value_col] = pd.to_numeric(temp_df[value_col], errors='coerce').fillna(0)

    # Perform aggregation
    grouped_data = temp_df.groupby(group_col)[value_col].agg(aggregation_func).reset_index()
    grouped_data = grouped_data.sort_values(value_col, ascending=False)

    chart_data = []
    if len(grouped_data) > n:
        top_items = grouped_data.head(n)
        # The 'Other' category typically sums the remaining values, regardless of the primary aggregation_func
        other_value = grouped_data.iloc[n:][value_col].sum()
        chart_data = [
            {"label": str(row[group_col]), "value": float(row[value_col])}
            for _, row in top_items.iterrows()
        ]
        chart_data.append({"label": other_label, "value": float(other_value)})
    else:
        chart_data = [
            {"label": str(row[group_col]), "value": float(row[value_col])}
            for _, row in grouped_data.iterrows()
        ]

    return chart_data