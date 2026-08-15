import pandas as pd
import numpy as np

def generate_overview_report(df: pd.DataFrame, date_col: str, status_col: str, status_value: str, sales_col: str, total_col: str, bill_id_col: str, customer_col: str, store_col: str, region_col: str, customer_type_col: str) -> dict:
    """
    Generates an overview report layout containing KPIs (current vs previous month) and multi-dimensional breakdown charts.

    Parameters:
    - df (pd.DataFrame): Input dataframe.
    - date_col (str): Name of the date column.
    - status_col (str): Name of the status column.
    - status_value (str): Value representing successful records (e.g., 'success').
    - sales_col (str): Name of the sales revenue column.
    - total_col (str): Name of the total invoice value column.
    - bill_id_col (str): Name of the bill/invoice identifier or unique count column.
    - customer_col (str): Name of the customer identifier column.
    - store_col (str): Name of the store name column.
    - region_col (str): Name of the region column.
    - customer_type_col (str): Name of the customer type column.

    Returns:
    - dict: A structured layout dictionary containing 'kpis' and 'charts'.
    """
    df_success = df[df[status_col] == status_value].copy()
    df_success[date_col] = pd.to_datetime(df_success[date_col])

    def calculate_kpis(data):
        kpis = []
        if not data.empty:
            total_doanh_so = data[sales_col].sum()
            total_tong = data[total_col].sum()
            num_bills = data[bill_id_col].nunique()
            num_customers = data[customer_col].nunique()

            kpis.append({
                "name": "Tổng Doanh Số",
                "value": float(total_doanh_so)
            })
            kpis.append({
                "name": "Tổng Giá Trị Hóa Đơn",
                "value": float(total_tong)
            })
            kpis.append({
                "name": "Số Lượng Hóa Đơn",
                "value": int(num_bills)
            })
            kpis.append({
                "name": "Số Lượng Khách Hàng",
                "value": int(num_customers)
            })
        return kpis

    kpis_current = calculate_kpis(df_success)

    current_month = df_success[date_col].max().month
    current_year = df_success[date_col].max().year

    if current_month == 1:
        prev_month = 12
        prev_year = current_year - 1
    else:
        prev_month = current_month - 1
        prev_year = current_year

    df_prev_month = df_success[(df_success[date_col].dt.month == prev_month) & (df_success[date_col].dt.year == prev_year)]
    kpis_prev = calculate_kpis(df_prev_month)

    final_kpis = []
    for i, kpi_curr in enumerate(kpis_current):
        kpi_obj = {
            "name": kpi_curr["name"],
            "value": kpi_curr["value"]
        }
        if i < len(kpis_prev):
            kpi_obj["compare_value"] = kpis_prev[i]["value"]
            kpi_obj["compare_label"] = "tháng trước"
        final_kpis.append(kpi_obj)

    df_success['month'] = df_success[date_col].dt.to_period('M').astype(str)
    monthly_sales = df_success.groupby('month')[sales_col].sum().reset_index()
    monthly_sales['month'] = pd.to_datetime(monthly_sales['month'])
    monthly_sales = monthly_sales.sort_values('month')

    last_full_month_date = pd.to_datetime(df_success[date_col].max().strftime('%Y-%m-01')) - pd.DateOffset(months=1)
    monthly_sales_full = monthly_sales[monthly_sales['month'] <= last_full_month_date]

    store_sales = df_success.groupby(store_col)[sales_col].sum().reset_index().nlargest(12, sales_col)
    region_sales = df_success.groupby(region_col)[sales_col].sum().reset_index()
    customer_type_sales = df_success.groupby(customer_type_col)[sales_col].sum().reset_index()
    customer_sales = df_success.groupby(customer_col)[sales_col].sum().reset_index().nlargest(12, sales_col)

    layout = {
        "kpis": final_kpis,
        "charts": [
            {
                "title": "Xu hướng Doanh Số Theo Tháng",
                "type": "line",
                "role": "trend",
                "size": "lg",
                "data": [{"label": month.strftime('%Y-%m'), "value": float(value)} for month, value in zip(monthly_sales_full['month'], monthly_sales_full[sales_col])]
            },
            {
                "title": "Doanh Số Theo Cửa Hàng (Top 12)",
                "type": "horizontal-bar",
                "role": "breakdown",
                "size": "md",
                "data": [{"label": row[store_col], "value": float(row[sales_col])} for _, row in store_sales.iterrows()]
            },
            {
                "title": "Doanh Số Theo Khu Vực",
                "type": "bar",
                "role": "breakdown",
                "size": "sm",
                "data": [{"label": row[region_col], "value": float(row[sales_col])} for _, row in region_sales.iterrows()]
            },
            {
                "title": "Tỷ Trọng Doanh Số Theo Loại Khách Hàng",
                "type": "pie",
                "role": "breakdown",
                "size": "sm",
                "data": [{"label": row[customer_type_col], "value": float(row[sales_col])} for _, row in customer_type_sales.iterrows()]
            },
            {
                "title": "Doanh Số Theo Khách Hàng (Top 12)",
                "type": "horizontal-bar",
                "role": "detail",
                "size": "lg",
                "data": [{"label": row[customer_col], "value": float(row[sales_col])} for _, row in customer_sales.iterrows()]
            }
        ]
    }
    return layout