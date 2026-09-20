"""Handles sales imports and generates Brickmerge depot acquisition CSVs."""
import os
import re
import numpy as np
import pandas as pd


class SalesImporter:
    """Base class providing aggregation, depot FIFO matching, and Brickmerge export for sales."""

    def __init__(self, aggregate_sales: bool = True, depot_export_file: str | None = None,
                 shipping_cost_base: float | int = 5.0, shipping_cost_percentage: float | int = 3,
                 ek_calculation_only: bool = False):
        self.aggregate_sales = aggregate_sales
        self.depot_export_file = depot_export_file
        self.shipping_cost_base = shipping_cost_base
        self.shipping_cost_percentage = shipping_cost_percentage / 100
        self.ek_calculation_only = ek_calculation_only

    def export_sales(self, sales_df: pd.DataFrame, shop: str, output_dir: str | None = None) -> pd.DataFrame:
        """Aggregates sales if configured and exports them into the Brickmerge format."""
        sales_df = sales_df.dropna(subset=['set_identifier', 'sale_date', 'sale_price', 'quantity'])
        sales_df = sales_df[sales_df['quantity'] > 0]

        if sales_df.empty:
            print("Keine Zeilen zum Exportieren übergeben.")
            return pd.DataFrame()

        sales = sales_df.copy()
        if 'channel' not in sales.columns:
            sales['channel'] = shop
        if 'buy_price' not in sales.columns:
            sales['buy_price'] = np.nan

        # Ensure datetime type for consistent formatting and grouping
        sales['sale_date'] = pd.to_datetime(sales['sale_date']).dt.normalize()

        if self.aggregate_sales:
            sales['groupby_month'] = sales['sale_date'].dt.strftime('%Y-%m')

            # 1€-Cluster relativ zum minimalen Verkaufspreis im jeweiligen Monat bilden
            sales['price_eff'] = (sales['sale_price'] - sales['sales_cost'] / sales['quantity'])  # Price without fees
            min_eff = sales.groupby(['set_identifier', 'groupby_month', 'channel'])['price_eff'].transform('min')
            sales['groupby_price'] = ((sales['price_eff'] - min_eff + 1e-9) // 1.0).astype(int)

            sales['note'] = sales['sale_date']
            aggregated_sales = sales.groupby(
                ['set_identifier', 'groupby_month', 'groupby_price', 'channel']
            ).agg({
                'sale_date': 'max',
                'sale_price': 'mean',
                'quantity': 'sum',
                'sales_cost': 'sum',
                'buy_price': 'mean',
                'note': lambda d: f"Zusammengefasste Verkäufe {d.min().strftime('%d.%m.')}"
                                  f" - {d.max().strftime('%d.%m.%Y')}" if pd.notna(d.min()) and d.min() != d.max() else "",
            })
            df = aggregated_sales.reset_index()
        else:
            df = sales

        export_df = pd.DataFrame({
            'setNo': df['set_identifier'].map(lambda x: str(x) if pd.notna(x) and '-' not in str(x) else None),
            'artikleNo': df['set_identifier'].map(lambda x: str(x) if pd.notna(x) and '-' in str(x) else None),
            'sale_date': df['sale_date'].dt.strftime('%d.%m.%Y'),
            'sale_price': df['sale_price'].round(2),
            'buy_price': df['buy_price'],
            'qty': df['quantity'].round().astype(int),
            'fees': df['sales_cost'].round(2),
            'channel': df['channel'],
            'note': df.get('note'),
        })

        if self.depot_export_file:
            if self.ek_calculation_only:
                export_df['buy_price'] = export_df['buy_price'].fillna(self.process_sales(export_df)).round(2)
            else:
                export_df['buy_price'] = self.process_sales(export_df).fillna(export_df['buy_price']).round(2)

        min_val = sales['sale_date'].min()
        max_val = sales['sale_date'].max()

        timestamp_min = min_val.date().isoformat() if pd.notna(min_val) else "unknown"
        timestamp_max = max_val.date().isoformat() if pd.notna(max_val) else "unknown"
        filename = f"{shop.lower()}_sales_{timestamp_min}_{timestamp_max}_brickmerge.csv"

        if output_dir:
            out_path = os.path.join(output_dir, filename)
        else:
            out_path = filename

        export_df.to_csv(out_path, index=False)
        print(f"Brickmerge export generated: '{filename}' ({len(export_df)} rows, {export_df['qty'].sum()} items).")
        return export_df

    def process_sales(self, sales: pd.DataFrame) -> pd.Series:
        """Matches sold items against the depot inventory using FIFO deduction.

        If ek_calculation_only is True, calculates the historical weighted average EK
        across all depot entries for the set without altering or overwriting the depot file.
        """
        if self.depot_export_file is None:
            print("No depot export file provided.")
            return pd.Series(np.nan, index=sales.index)

        bm_depot_df = pd.read_csv(
            self.depot_export_file,
            sep=';',
            decimal=',',
            parse_dates=['buy_date'],
            date_format='%d.%m.%Y',
            dtype={
                'setNo': str, 'EAN': str, 'artikleNo': str, 'qty': int,
                'buy_price': float, 'condition': str, 'storage': str,
                'note': str, 'setnote': str
            }
        )
        ek_list = []

        for _, row in sales.iterrows():
            article_no = row.get('artikleNo')
            set_no = row.get('setNo')

            # Prioritize article number if provided, fallback to set number
            if isinstance(article_no, str) and article_no.strip():
                mask = bm_depot_df['artikleNo'] == article_no
            else:
                mask = bm_depot_df['setNo'] == set_no

            mask &= bm_depot_df['qty'] > 0

            if self.ek_calculation_only:
                # Get average purchase price from depot
                matches = bm_depot_df[mask & bm_depot_df['buy_price'].notna()]
                if not matches.empty:
                    total_value = (matches['qty'] * matches['buy_price']).sum()
                    total_qty = matches['qty'].sum()
                    avg_ek = total_value / total_qty
                else:
                    ident = article_no if isinstance(article_no, str) and article_no.strip() else set_no
                    print(f"Hinweis: Kein Depot-Einkaufspreis für Set {ident} gefunden.")
                    avg_ek = np.nan
                ek_list.append(round(avg_ek, 2))
            else:
                stock_indices = bm_depot_df[mask].sort_values(by=['buy_date', 'buy_price'], ascending=[True, True],
                                                              na_position='first').index

                qty_needed = int(row['qty'])
                total_ek = 0.0
                total_ek_qty = 0

                for idx in stock_indices:
                    if qty_needed <= 0:
                        break

                    available = bm_depot_df.at[idx, 'qty']
                    buy_price = bm_depot_df.at[idx, 'buy_price']
                    take = int(min(np.nanmax([0, available]), qty_needed))

                    if pd.notna(buy_price):
                        total_ek += take * buy_price
                        total_ek_qty += take

                    qty_needed -= take
                    bm_depot_df.at[idx, 'qty'] -= take

                avg_ek = (total_ek / total_ek_qty) if total_ek_qty > 0 else np.nan
                ek_list.append(avg_ek)

                if qty_needed > 0:
                    ident = article_no if isinstance(article_no, str) and article_no.strip() else set_no
                    print(f"Warning: Depot shortage for item {ident}. Missing quantity: {qty_needed}")

        if not self.ek_calculation_only:
            # Remove exhausted stock positions and save updated depot CSV
            bm_depot_df = bm_depot_df[bm_depot_df['qty'] > 0].copy()
            bm_depot_df['buy_date'] = bm_depot_df['buy_date'].dt.strftime('%d.%m.%Y')
            bm_depot_df.to_csv(self.depot_export_file, sep=';', decimal=',', index=False)

        return pd.Series(ek_list, index=sales.index)

    @staticmethod
    def _parse_currency(val) -> float:
        if pd.isna(val):
            return np.nan
        val_str = str(val).strip()
        if not val_str:
            return np.nan

        # 1. Remove all currency symbols, letters, and spaces (retain minus signs, digits, commas, and periods)
        cleaned = re.sub(r'[^\d,.-]', '', val_str)
        if not cleaned or cleaned == '-':
            return np.nan

        # 2. Detect format and convert to standard float:
        if ',' in cleaned and '.' in cleaned:
            if cleaned.rfind(',') > cleaned.rfind('.'):
                # The period is the thousand separator, and the comma is the decimal separator (1,234.50).
                cleaned = cleaned.replace('.', '').replace(',', '.')
            else:
                # The comma is the thousand separator, and the period is the decimal separator (1,234.50).
                cleaned = cleaned.replace(',', '')
        elif ',' in cleaned:
            # German decimal comma: "44,99" -> "44.99"
            cleaned = cleaned.replace(',', '.')

        try:
            return float(cleaned)
        except ValueError:
            return 0.0
