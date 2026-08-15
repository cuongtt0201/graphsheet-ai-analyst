import pandas as pd

def calculate_revenue_trends(df: pd.DataFrame, date_column: str, revenue_column: str, region_column: str):
    """Calculates key revenue KPIs and aggregates revenue by region and time.

    Args:
        df (pd.DataFrame): The input DataFrame containing revenue data.
        date_column (str): The name of the column containing date information.
        revenue_column (str): The name of the column containing revenue figures.
        region_column (str): The name of the column containing region information.

    Returns:
        dict: A dictionary containing:
            - kpis (list): A list of dictionaries, each with a 'name' and 'value' for key KPIs (e.g., 'Total Revenue').
            - charts (list): A list of dictionaries, each representing a chart configuration:
                - title (str): The title of the chart.
                - type (str): The type of chart (e.g., 'bar', 'line').
                - data (list): A list of dictionaries, where each dictionary represents a data point for the chart.
    """
    # Calculate total revenue KPI
    total_revenue = int(df[revenue_column].sum())

    # Aggregate by Region
    region_data = df.groupby(region_column)[revenue_column].sum().reset_index()
    region_chart_data = [
        {"label": str(row[region_column]), "value": int(row[revenue_column])}
        for _, row in region_data.sort_values(revenue_column, ascending=False).iterrows()
    ]

    # Aggregate by Date
    # Ensure the date column is in datetime format for proper sorting and formatting
    df[date_column] = pd.to_datetime(df[date_column])
    daily_data = df.groupby(date_column)[revenue_column].sum().reset_index()
    daily_data[date_column] = daily_data[date_column].dt.strftime('%Y-%m-%d')
    time_chart_data = [
        {"label": str(row[date_column]), "value": int(row[revenue_column])}
        for _, row in daily_data.sort_values(date_column).iterrows()
    ]

    # Construct the output structure
    output = {
        "kpis": [
            {"name": "Total Revenue", "value": total_revenue}
        ],
        "charts": [
            {
                "title": "Revenue by Region",
                "type": "bar",
                "data": region_chart_data
            },
            {
                "title": "Revenue Trend Over Time",
                "type": "line",
                "data": time_chart_data
            }
        ]
    }

    return output
