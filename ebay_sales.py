"""Process eBay Orders Reports and export as Brickmerge sales CSV."""
import os
import numpy as np
import pandas as pd
from database import Database
from sales import SalesImporter
from set_number_parser import SetNumberParser


class EbaySalesImporter(SalesImporter):
    """
    Imports eBay Orders CSVs.
    Fees: If shipping costs > 0 only ebay fee, otherwise adds estimated shipping (5€ + 3%).
    """

    def __init__(self, db: Database, aggregate_sales: bool = True, depot_export_file: str | None = None,
                 ebay_fee_percent: float | int = 12, default_ad_percent: float | int = 2,
                 shipping_cost_base: float = 5.0, shipping_cost_percentage: float | int = 3,
                 always_estimate_real_shipping_cost: bool = False, ek_calculation_only: bool = False):
        super().__init__(aggregate_sales, depot_export_file, shipping_cost_base, shipping_cost_percentage,
                         ek_calculation_only)
        self.db = db
        self.ebay_fee_percent = ebay_fee_percent / 100 * 1.19
        self.default_ad_percent = default_ad_percent / 100 * 1.19
        self.always_estimate_real_shipping_cost = always_estimate_real_shipping_cost

    def import_sales(self, csv_filepath: str) -> pd.DataFrame | None:
        """
        Processes eBay Orders Report and exports in Brickmerge sales format.
        """
        usecols = ['Verkaufsprotokollnummer', 'Bestellnummer', 'Artikelnummer', 'Angebotstitel', 'Bestandseinheit',
                   'Verkauft über Anzeigen', 'Anzahl', 'Verkauft für', 'Verpackung und Versand', 'Inklusive MwSt.-Satz',
                   'Gesamtbetrag', 'Verkauft am', 'Verschickt am - Datum']
        output_directory = os.path.dirname(os.path.abspath(csv_filepath))
        try:
            df = pd.read_csv(csv_filepath,
                             sep=';',
                             decimal=',',
                             skiprows=1,
                             on_bad_lines='skip',
                             usecols=lambda c: c in usecols,
                             dtype={'Verkaufsprotokollnummer': str, 'Bestellnummer': str, 'Artikelnummer': str,
                                    'Angebotstitel': str, 'Bestandseinheit': str, 'Anzahl': float,
                                    'Inklusive MwSt.-Satz': float},
                             converters={
                                 'Verkauft für': self._parse_currency,
                                 'Verpackung und Versand': self._parse_currency,
                                 'Gesamtbetrag': self._parse_currency,
                                 'Verkauft über Anzeigen': lambda x: False if (pd.isna(x) or x == 'Nein') else True,
                             },
                             )
        except FileNotFoundError:
            print(f"Error: File {csv_filepath} not found.")
            return None

        # Replace german month abbreviations and parse dates
        month_replacements = {
            r'-Mär-': '-Mar-',
            r'-Mai-': '-May-',
            r'-Okt-': '-Oct-',
            r'-Dez-': '-Dec-'
        }
        for col in ['Verkauft am', 'Verschickt am - Datum']:
            if col in df.columns:
                s = df[col].astype(str)
                for de, en in month_replacements.items():
                    s = s.str.replace(de, en, regex=True)
                df[col] = pd.to_datetime(s, format='%d-%b-%y', errors='coerce')

        # Calculate per-line ship cost and filter data
        is_order_summary_col = df['Gesamtbetrag'].notna() & df['Artikelnummer'].isna()
        is_single_line_order_col = df['Gesamtbetrag'].notna() & df['Artikelnummer'].notna()
        is_header = is_order_summary_col | is_single_line_order_col
        base_total = np.where(is_order_summary_col, df['Verkauft für'],
                              np.where(is_single_line_order_col, df['Verkauft für'] * df['Anzahl'], np.nan))
        df['paid_ratio'] = np.where(is_header, (df['Gesamtbetrag'] - df['Verpackung und Versand']) / base_total, np.nan)
        df['shipping_ratio'] = np.where(is_header, df['Verpackung und Versand'] / base_total, np.nan)
        df[['paid_ratio', 'shipping_ratio', 'Gesamtbetrag']] = df[
            ['paid_ratio', 'shipping_ratio', 'Gesamtbetrag']].ffill()
        df['paid_item_price'] = df['Verkauft für'] * df['paid_ratio']
        df['unit_shipping'] = df['Verkauft für'] * df['shipping_ratio']
        diff_mask = (
                ((df['paid_ratio'] - 1.0).abs() > 0.001)
                & df['Verschickt am - Datum'].notna()
                & df['Artikelnummer'].notna()
                & (df['Gesamtbetrag'] != 0.0)
        )
        if diff_mask.any():
            print("Partial refund found for the following orders:\n",
                  df.loc[diff_mask, ['Bestellnummer', 'Artikelnummer', 'Bestandseinheit', 'Verkauft für',
                                     'paid_ratio', 'paid_item_price', 'unit_shipping']].to_string(),
                  "\nAdjusting item price to paid_item_price value if paid_ratio > 0.5, ignoring if < 0.5.")
        items = df[df['Verschickt am - Datum'].notna() & df['Artikelnummer'].notna() & (df['paid_ratio'] >= 0.5)].copy()

        if items.empty:
            print("No line items found in eBay report.")
            return None

        new_items = self.db.filter_new_sales(items, 'eBay', 'Bestellnummer', 'Artikelnummer')
        if new_items.empty:
            print("No new eBay sales found.")
            return None

        new_items.rename(columns={
            'Anzahl': 'quantity',
            'Verkauft am': 'sale_date'
        }, inplace=True)

        has_shipping = new_items['unit_shipping'] > 0.0
        fee_percent = self.ebay_fee_percent + np.where(new_items['Verkauft über Anzeigen'], self.default_ad_percent, 0)

        # Fold shipping per unit into item sale price for Brickmerge export
        new_items['sale_price'] = (new_items['paid_item_price'] + new_items['unit_shipping']).round(2)
        position_total = new_items['sale_price'] * new_items['quantity']

        # Platform commission on total order revenue plus actual postage costs
        fee_separate_ship = (position_total * fee_percent) + (new_items['unit_shipping'] * new_items['quantity'])

        # Platform commission plus estimated postage (base + variable percent) for free shipping orders
        fee_estimated_ship = (position_total * fee_percent) + (
                self.shipping_cost_base + (self.shipping_cost_percentage * position_total)
        )

        # Apply actual shipping deduction unless shipping is free or estimation is enforced
        new_items['sales_cost'] = np.where(
            has_shipping & (~self.always_estimate_real_shipping_cost),
            fee_separate_ship,
            fee_estimated_ship
        ).round(2)

        # Find Set numbers for eBay items
        sku_template = self.db.get_setting("sku_template", "{SET}[-{WERT,1,4}]")
        parser = SetNumberParser(sku_template)

        def _resolve_ebay_identifier(row) -> str | float:
            # 1. Check für custom eBay item number mapping
            item_no = str(row['Artikelnummer']).strip() if pd.notna(row['Artikelnummer']) else None
            if item_no:
                mapped = self.db.get_mapped_set_number('eBay', item_no)
                if mapped:
                    return mapped

            # 2. Check für custom eBay sku mapping
            sku = str(row['Bestandseinheit']).strip() if pd.notna(row['Bestandseinheit']) else None
            if sku:
                mapped = self.db.get_mapped_set_number('eBay', sku)
                if mapped:
                    return mapped

            # 3. Return sku if no mapping found
            return parser.get_set_number_from_sku(sku) or np.nan

        new_items['set_identifier'] = new_items.apply(_resolve_ebay_identifier, axis=1)

        # Fallback to Title Regex Extraction
        missing = new_items['set_identifier'].isna()
        if missing.any():
            new_items.loc[missing, 'set_identifier'] = new_items.loc[missing, 'Angebotstitel'].map(
                parser.get_set_number_from_title
            )
        if new_items['set_identifier'].isna().any():
            missing_identifiers = new_items[new_items['set_identifier'].isna()]
            print("Notice: Sales without assigned set number exported to 'ebay_missing_set_numbers.csv'.")
            missing_identifiers.to_csv(os.path.join(output_directory, "ebay_missing_set_numbers.csv"))

        import_df = self.export_sales(new_items, 'eBay', output_directory)
        self.db.record_sales(new_items, 'eBay', 'Bestellnummer', 'Artikelnummer')
        return import_df
