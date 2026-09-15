"""Imports sales from Amazon seller reports."""
import os
import numpy as np
import pandas as pd
from database import Database
from sales import SalesImporter


class AmazonSalesImporter(SalesImporter):
    """Imports sales from Amazon seller reports."""

    def __init__(self, db: Database, aggregate_sales: bool = True, depot_export_file: str | None = None,
                 shipping_cost_base: float = 5.0, shipping_cost_percentage: float | int = 3,
                 ek_calculation_only: bool = False):
        super().__init__(aggregate_sales, depot_export_file, shipping_cost_base, shipping_cost_percentage,
                         ek_calculation_only)
        self.db = db

    def import_sales(self, amazon_sales_report_filename: str) -> pd.DataFrame | None:
        """Processes Amazon seller report and exports in Brickmerge sales format."""
        usecols = [
            'amazon-order-id', 'purchase-date', 'order-status', 'fulfillment-channel', 'product-name', 'ship-country',
            'asin', 'quantity', 'item-price', 'shipping-price', 'item-promotion-discount', 'ship-promotion-discount'
        ]
        output_directory = os.path.dirname(os.path.abspath(amazon_sales_report_filename))
        try:
            amz_report = pd.read_csv(
                amazon_sales_report_filename,
                sep='\t',
                on_bad_lines='skip',
                converters={'product-name': self.get_set_number_from_title},
                parse_dates=['purchase-date'],
                usecols=lambda c: c in usecols
            )
        except FileNotFoundError:
            print(f"Error: File {amazon_sales_report_filename} not found.")
            return None

        # Filter for shipped orders only
        amz_report = amz_report[amz_report['order-status'] == 'Shipped']

        new_orders = self.db.filter_new_sales(amz_report, 'Amazon', 'amazon-order-id', 'asin')

        # Fallback to map set number from ASIN
        new_orders['set_identifier'] = new_orders['product-name'].fillna(
            new_orders['asin'].map(self.asin_to_set_number_fallback)
        )
        if new_orders['set_identifier'].isna().any():
            missing_identifiers = new_orders[new_orders['set_identifier'].isna()]
            print("Notice: Sales without set identifiers exported to 'missing_set_identifiers.csv'.")
            missing_identifiers.to_csv(os.path.join(output_directory, "missing_set_identifiers.csv"), index=False)
        new_orders = new_orders[~new_orders['set_identifier'].isna()].copy()

        if new_orders.empty:
            print("No new sales to import.")
            return None

        # Calculate revenue and fees
        new_orders['total_revenue'] = new_orders[['item-price', 'shipping-price']].sum(axis=1)
        discount_columns = {'item-promotion-discount', 'ship-promotion-discount'}.intersection(set(new_orders.columns))
        if discount_columns:
            new_orders['total_revenue'] -= new_orders[list(discount_columns)].sum(axis=1)
        new_orders['sale_price'] = new_orders['total_revenue'] / new_orders['quantity']

        # Estimate sales costs (Amazon commission + VAT + estimated shipping)
        new_orders['sales_cost'] = (
                new_orders['total_revenue'] * (0.15 * 1.19 + self.shipping_cost_percentage) + self.shipping_cost_base
        )

        new_orders['channel'] = np.where(new_orders['fulfillment-channel'] == 'Amazon', 'Amazon FBA', 'Amazon FBM')
        new_orders.rename(columns={'purchase-date': 'sale_date'}, inplace=True)

        import_df = self.export_sales(new_orders, 'Amazon', output_directory)
        self.db.record_sales(new_orders, 'Amazon', 'amazon-order-id', 'asin')
        return import_df

    def asin_to_set_number_fallback(self, asin: str) -> str | None:
        """Maps an ASIN to a known Lego set number via manual overrides or purchase history."""
        # 1. Check custom DB mapping
        db_mapping = self.db.get_mapped_set_number('Amazon', asin)
        if db_mapping:
            return db_mapping

        # 2. Check previous Amazon purchase history
        return self.db.get_set_number_by_asin(asin)