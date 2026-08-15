import pandas as pd

def calculate_top_n_distribution(df, category_col, value_col, n=5):
    """
    Calculates the distribution of a value column grouped by a category column,
    grouping the top N items individually and aggregating the rest as 'Other'.

    Args:
        df (pd.DataFrame): The input dataframe.
        category_col (str): The column name to group by.
        value_col (str): The column name to sum.
        n (int): Number of top items to display individually.

    Returns:
        list: A list of dictionaries containing 'label' and 'value' keys.
    """
    grouped = df.groupby(category_col)[value_col].sum().reset_index()
    top_n = grouped.nlargest(n, value_col)
    other_val = grouped[value_col].sum() - top_n[value_col].sum()

    result = [{"label": str(row[category_col]), "value": float(row[value_col])} for _, row in top_n.iterrows()]
    
    if other_val > 0:
        result.append({"label": "Khác", "value": float(other_val)})
        
    return result