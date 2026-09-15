"""Imports Bricklink Order CSVs with detail items."""
import os
import numpy as np
import pandas as pd
import country_converter as coco
from database import Database
from sales import SalesImporter


class BricklinkSalesImporter(SalesImporter):
    """
    Imports Bricklink Order CSVs with detail items.
    Ignores shipping & handling.
    """

    def __init__(self, db: Database, aggregate_sales: bool = True, depot_export_file: str | None = None,
                 bricklink_fee_percent: float | int = 3, paypal_fee_percent: float | int = 2,
                 stripe_fee_percent: float | int = 2, ek_calculation_only: bool = False):
        super().__init__(aggregate_sales, depot_export_file, ek_calculation_only=ek_calculation_only)
        self.db = db
        self.bricklink_fee_percent = bricklink_fee_percent / 100
        self.paypal_fee_percent = paypal_fee_percent / 100  # Only the amount not paid by customer via handling fee
        self.stripe_fee_percent = stripe_fee_percent / 100  # Only the amount not paid by customer via handling fee

    def import_sales(self, csv_filepath: str, ignore_discounts: bool = False) -> pd.DataFrame | None:
        """Import sales data from a Bricklink CSV file and exports in Brickmerge sales CSV format."""
        usecols = ['Order ID', 'Order Date', 'Base Currency', 'Credit', 'Coupon Credit', 'Order Total',
                   'Base Grand Total', 'Order Status', 'Pmt Method', 'Location', 'Batch Date', 'Condition',
                   'Sub-Condition', 'Qty', 'Each', 'Item Type', 'Item Number', 'My Cost', 'Sub-Condition']
        try:
            raw_df = pd.read_csv(csv_filepath,
                                 sep=',',
                                 usecols=lambda c: c in usecols,
                                 index_col=False,
                                 on_bad_lines='skip',
                                 dtype={'Order ID': str, 'Base Currency': str, 'Total Items': float,
                                        'Order Status': str, 'Pmt Method': str, 'Condition': str,
                                        'Sub-Condition': str, 'Item Type': str, 'Item Number': str},
                                 converters={
                                     'Credit': self._parse_currency,
                                     'Coupon Credit': self._parse_currency,
                                     'Order Total': self._parse_currency,
                                     'Base Grand Total': self._parse_currency,
                                     'Each': self._parse_currency,
                                     'My Cost': self._parse_currency,
                                     'Location': lambda x: x.split(',')[0] if isinstance(x, str) else np.nan,
                                 },
                                 parse_dates=['Order Date', 'Batch Date'],
                                 date_format='%m/%d/%Y',
                                 )
        except FileNotFoundError:
            print(f"Error: File {csv_filepath} not found.")
            return None

        # Set Credit and Coupon Credit to 0.0 if they are nan in order columns
        raw_df.loc[~raw_df['Order Total'].isna(), ['Credit', 'Coupon Credit']] = (
            raw_df.loc[~raw_df['Order Total'].isna(), ['Credit', 'Coupon Credit']].fillna(0.0))

        # Forward fill order metadata
        for col in ['Order ID', 'Order Date', 'Base Currency', 'Credit', 'Coupon Credit', 'Order Total',
                    'Base Grand Total', 'Order Status', 'Pmt Method', 'Location']:
            if col in raw_df.columns:
                raw_df[col] = raw_df[col].replace('', np.nan).ffill()

        mask_shipped = raw_df['Order Status'].isin(['Shipped', 'Received', 'Completed'])
        mask_type = raw_df['Item Type'].isin(['Set', 'Gear'])
        items = raw_df[mask_shipped & mask_type].copy()
        if items.empty:
            print("Fehler: Keine Artikelzeilen gefunden. Wurde beim Bricklink-Export 'Include detail items' aktiviert?")
            return None

        new_items = self.db.filter_new_sales(items, 'Bricklink', 'Order ID', 'Item Number')
        if new_items.empty:
            print("No new Bricklink sales found.")
            return None

        if not ignore_discounts:
            discount_ratio = (new_items['Credit'] + new_items['Coupon Credit']) / new_items['Order Total']
            new_items['sale_price'] = new_items['Each'] * (1.0 - discount_ratio)
        else:
            new_items['sale_price'] = new_items['Each']

        # Adjust item price for VAT
        cc = coco.CountryConverter()
        country = new_items['Location'].str.split(',').str[0]
        is_eu = country.isin(cc.EU.name_short.to_list())
        new_items['sale_price'] = (new_items['sale_price'] * np.where(is_eu, 1.0, 1.19)).round(2)

        new_items['sales_cost'] = new_items.apply(
            lambda row: self.get_fees(row['Pmt Method'], row['sale_price'] * row['Qty']), axis=1)
        new_items.rename(columns={'Batch Date': 'sale_date',
                                  'Qty': 'quantity',
                                  'Item Number': 'set_identifier',
                                  'My Cost': 'buy_price'}, inplace=True)

        import_df = self.export_sales(new_items, 'Bricklink', os.path.dirname(os.path.abspath(csv_filepath)))
        self.db.record_sales(new_items, 'Bricklink', 'Order ID', 'set_identifier')
        return import_df

    def get_fees(self, payment_method: str, position_value: float):
        """
        Estimate fees based on payment method and position value.
        """
        if 'paypal' in payment_method.lower():
            return np.around(
                position_value * (self.paypal_fee_percent + self.bricklink_fee_percent), decimals=2)
        elif 'stripe' in payment_method.lower():
            return np.around(
                position_value * (self.stripe_fee_percent + self.bricklink_fee_percent), decimals=2)
        else:
            return np.around(position_value * self.bricklink_fee_percent, decimals=2)