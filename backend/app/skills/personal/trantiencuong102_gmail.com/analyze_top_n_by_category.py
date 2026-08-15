import pandas as pd

def analyze_top_n_by_category(df: pd.DataFrame, category_col: str, value_col: str, n: int = 10) -> pd.DataFrame:
    """
    Analyzes the top N categories based on the sum of a specified value column.

    This function groups the DataFrame by a given category column, calculates the sum
    of a specified value column for each category, and then returns the top N
    categories with the highest sums.

    Args:
        df (pd.DataFrame): The input DataFrame.
        category_col (str): The name of the column to group by (e.g., 'TÊN NV', 'ĐỊA CHỈ').
        value_col (str): The name of the column to sum (e.g., 'THÀNH TIỀN').
        n (int, optional): The number of top categories to return. Defaults to 10.

    Returns:
        pd.DataFrame: A DataFrame containing the top N categories and their summed values,
                      sorted in descending order of the value.
                      Columns: [category_col, value_col].

    Raises:
        ValueError: If one or more specified columns are not found in the DataFrame.
    """
    if not all(col in df.columns for col in [category_col, value_col]):
        raise ValueError(f"One or more specified columns ({category_col}, {value_col}) not found in DataFrame.")

    # Ensure value_col is numeric and handle NaNs if necessary
    # For robustness, convert to numeric and fillna if not already done in preprocessing.
    # Assuming input df has already been preprocessed for NaNs in value_col as in the original code.
    # df[value_col] = pd.to_numeric(df[value_col], errors='coerce').fillna(0)

    grouped_data = df.groupby(category_col)[value_col].sum().nlargest(n).reset_index()
    return grouped_data