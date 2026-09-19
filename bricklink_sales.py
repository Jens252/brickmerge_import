"""Imports Bricklink Order CSVs with detail items."""
import os
import numpy as np
import pandas as pd
from eu_countries import is_eu
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
                   'Base Grand Total', 'Order Status', 'Pmt Method', 'Location', 'Batch', 'Batch Date', 'Condition',
                   'Sub-Condition', 'Qty', 'Each', 'Item Type', 'Item Number', 'My Cost']
        try:
            raw_df = pd.read_csv(csv_filepath,
                                 sep=',',
                                 usecols=lambda c: c in usecols,
                                 index_col=False,
                                 on_bad_lines='skip',
                                 dtype={'Order ID': str, 'Base Currency': str, 'Total Items': float,
                                        'Order Status': str, 'Pmt Method': str, 'Batch': str, 'Condition': str,
                                        'Qty': float, 'Sub-Condition': str, 'Item Type': str, 'Item Number': str},
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
            print("Fehler: Keine Artikelzeilen gefunden. Wurde beim BrickLink-Export 'Include detail items' aktiviert?")
            return None

        # Batches derselben Artikelnummer innerhalb derselben Bestellung bündeln
        items = (
            items.groupby(['Order ID', 'Item Number'], group_keys=False)
            .apply(self.aggregate_batches)
            .reset_index(drop=True)
        )

        new_items = self.db.filter_new_sales(items, 'Bricklink', 'Order ID', 'Item Number')
        if new_items.empty:
            print("No new BrickLink sales found.")
            return None

        if not ignore_discounts:
            discount_ratio = (new_items['Credit'] + new_items['Coupon Credit']) / new_items['Order Total']
            new_items['sale_price'] = new_items['Each'] * (1.0 - discount_ratio)
        else:
            new_items['sale_price'] = new_items['Each']

        # Adjust item price for VAT
        is_eu_country = new_items['Location'].map(is_eu)
        new_items['sale_price'] = (new_items['sale_price'] * np.where(is_eu_country, 1.0, 1.19)).round(2)

        new_items['sales_cost'] = new_items.apply(
            lambda row: self.get_fees(row['Pmt Method'], row['sale_price'] * row['Qty']), axis=1)

        new_items['set_identifier'] = new_items['Item Number'].map(
            lambda x: self.db.get_mapped_set_number('BrickLink', x) or self.map_bricklink_item_number(x))

        new_items.rename(columns={'Batch Date': 'sale_date',
                                  'Qty': 'quantity',
                                  'My Cost': 'buy_price'}, inplace=True)

        import_df = self.export_sales(new_items, 'Bricklink', os.path.dirname(os.path.abspath(csv_filepath)))
        self.db.record_sales(new_items, 'Bricklink', 'Order ID', 'Item Number')
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

    @staticmethod
    def aggregate_batches(group: pd.DataFrame) -> pd.Series:
        """Batches aggregieren: Mehrere Batches derselben Item Number zusammenfassen"""
        order_id, item_number = group.name if hasattr(group, "name") else (None, None)

        total_qty = group['Qty'].sum()
        # Gewichteter Durchschnitt für Einzelpreise bei unterschiedlichen Batches
        if total_qty > 0:
            weighted_each = (group['Each'] * group['Qty']).sum() / total_qty
            weighted_cost = (group['My Cost'] * group['Qty']).sum() / total_qty
        else:
            weighted_each = group['Each'].iloc[0]
            weighted_cost = group['My Cost'].iloc[0]

        batch_dates = group['Batch Date'].dropna()
        sale_date = batch_dates.max() if not batch_dates.empty else group['Order Date'].iloc[0]

        return pd.Series(
            {
                'Order ID': order_id,
                'Item Number': item_number,
                'Order Date': group['Order Date'].iloc[0],
                'Base Currency': group['Base Currency'].iloc[0],
                'Credit': group['Credit'].iloc[0],
                'Coupon Credit': group['Coupon Credit'].iloc[0],
                'Order Total': group['Order Total'].iloc[0],
                'Base Grand Total': group['Base Grand Total'].iloc[0],
                'Order Status': group['Order Status'].iloc[0],
                'Pmt Method': group['Pmt Method'].iloc[0],
                'Location': group['Location'].iloc[0],
                'Batch': group['Batch'].count(),
                'Batch Date': sale_date,
                'Condition': ', '.join(group['Condition'].dropna().unique()),
                'Sub-Condition': ', '.join(group['Sub-Condition'].dropna().unique()),
                'Item Type': group['Item Type'].iloc[0],
                'Qty': total_qty,
                'Each': weighted_each,
                'My Cost': weighted_cost,
            }
        )

    @staticmethod
    def map_bricklink_item_number(item_number: str | None) -> str | None:
        """
        Convert Bricklink item numbers to a brickmerge format where different.
        """
        if not item_number or not isinstance(item_number, str):
            return item_number

        item_str = item_number.strip()

        # Split item number into base and suffix
        parts = item_str.split('-')
        if len(parts) == 2:
            base, suffix = parts[0], parts[1]

            if len(base) == 5 and base.isdigit():
                if 71045 <= int(base) < 71060:
                    # Convert format for CMF complete series or boxes
                    if suffix == '2':
                        return f"{base}-12"
                    elif suffix == '3':
                        return f"{base}-36"

        return item_str