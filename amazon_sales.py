"""Imports sales from Amazon seller reports."""
import os
import numpy as np
import pandas as pd
from database import Database
from sales import SalesImporter
from set_number_parser import SetNumberParser


class AmazonSalesImporter(SalesImporter):
    """Imports sales from Amazon seller reports."""

    def __init__(self, db: Database, aggregate_sales: bool = True, depot_export_file: str | None = None,
                 shipping_cost_base: float = 5.0, shipping_cost_percentage: float | int = 3,
                 ek_calculation_only: bool = False):
        super().__init__(aggregate_sales, depot_export_file, shipping_cost_base, shipping_cost_percentage,
                         ek_calculation_only)
        self.db = db

    def import_sales(self, filenames: str | list[str]) -> pd.DataFrame | None:
        """Processes one or multiple Amazon seller reports and exports in Brickmerge sales format."""
        file_list = [filenames] if isinstance(filenames, str) else filenames

        if not file_list:
            print("Fehler: Keine Dateien zum Importieren übergeben.")
            return None

        usecols = [
            'amazon-order-id', 'purchase-date', 'last-updated-date', 'order-status', 'fulfillment-channel',
            'sales-channel', 'product-name', 'ship-country', 'sku', 'asin', 'quantity', 'item-price', 'shipping-price',
            'item-promotion-discount', 'ship-promotion-discount', 'order-item-id'
        ]
        output_directory = os.path.dirname(os.path.abspath(file_list[0]))
        loaded_dfs: list[pd.DataFrame] = []

        for fpath in file_list:
            if not os.path.isfile(fpath):
                print(f"Error: File '{fpath}' not found.")
                continue

            try:
                with open(fpath, 'r', encoding='utf-8-sig', errors='ignore') as f:
                    raw_header = f.readline()
                # Alle Spalten trennen, von \xa0, \r, \n und Leerzeichen befreien
                clean_headers = [c.replace('\xa0', '').strip() for c in raw_header.split('\t')]

                df = pd.read_csv(
                    fpath,
                    sep='\t',
                    on_bad_lines='skip',
                    skiprows=1,
                    names=clean_headers,
                    dtype={'amazon-order-id': str, 'order-status': str, 'fulfillment-channel': str,
                           'sales-channel': str, 'product-name': str, 'ship-country': str, 'sku': str, 'asin': str,
                           'quantity': float, 'item-price': float, 'shipping-price': float,
                           'item-promotion-discount': float, 'ship-promotion-discount': float, 'order-item-id': str},
                    parse_dates=['purchase-date', 'last-updated-date'],
                    date_format='ISO8601',
                    usecols=lambda c: c in usecols
                )
                loaded_dfs.append(df)
            except Exception as e:
                print(f"Fehler beim Einlesen von '{fpath}': {e}")

        if not loaded_dfs:
            print("Keine gültigen Amazon-Verkaufsberichte gefunden.")
            return None

        amz_report = (pd.concat(loaded_dfs, ignore_index=True)
                      .sort_values(by='last-updated-date', ascending=False)
                      .drop_duplicates(subset=['amazon-order-id', 'order-item-id'])
                      .sort_values(by='purchase-date', ascending=False)
                      .reset_index(drop=True))
        if len(file_list) > 1:
            print(f"Kombinierte {len(loaded_dfs)} Verkaufsberichte ({len(amz_report)} Zeilen gesamt).")

        # Filter for shipped orders only
        amz_report = amz_report[
            (amz_report['order-status'] == 'Shipped') & (amz_report['sales-channel'] != 'Non-Amazon')]

        new_orders = self.db.filter_new_sales(amz_report, 'Amazon', 'amazon-order-id', 'order-item-id')

        # Get set identifier from ASIN, SKU or product name
        sku_template = self.db.get_setting("sku_template", "{SET}[-{WERT,1,4}]")
        parser = SetNumberParser(sku_template)
        new_orders['set_identifier'] = new_orders['asin'].map(self.asin_to_set_number_fallback).fillna(
            new_orders['sku'].map(parser.get_set_number_from_sku)).fillna(
            new_orders['product-name'].map(parser.get_set_number_from_title))
        if new_orders['set_identifier'].isna().any():
            missing_identifiers = new_orders[new_orders['set_identifier'].isna()]
            print("Notice: Sales without assigned set number exported to 'amazon_missing_set_numbers.csv'.")
            missing_identifiers.to_csv(os.path.join(output_directory, "amazon_missing_set_numbers.csv"))
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
        self.db.record_sales(new_orders, 'Amazon', 'amazon-order-id', 'order-item-id')
        return import_df

    def asin_to_set_number_fallback(self, asin: str) -> str | None:
        """Maps an ASIN to a known Lego set number via manual overrides or purchase history."""
        # 1. Check custom DB mapping
        db_mapping = self.db.get_mapped_set_number('Amazon', asin)
        if db_mapping:
            return db_mapping

        # 2. Check previous Amazon purchase history
        return self.db.get_set_number_by_asin(asin)