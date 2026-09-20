"""Handles Amazon Business purchase imports and generates Brickmerge depot acquisition CSVs."""
import os

import numpy as np
import pandas as pd
from database import Database


class AmazonPurchasesImporter:
    """Handles Amazon Business purchase imports and generates Brickmerge depot acquisition CSVs."""

    def __init__(self, db: Database, use_received: bool = True):
        self.db = db
        self.use_received = use_received

    def import_purchases(self, amazon_order_report_filename: str) -> pd.DataFrame | None:
        """
        Creates a Brickmerge Depot import CSV from an Amazon Business Orders Report.
        """
        usecols = [
            'Order ID', 'Order Date', 'Order Net Total', 'Payment reference ID', 'Item model number', 'Item Quantity',
            'Payment Amount', 'Item Net Total', 'Item subtotal sum', 'Received quantity', 'ASIN', 'Title',
            'Manufacturer', 'Item Subtotal', 'Item Shipping & Handling', 'Item Promotion', 'Item VAT',
            'Pricing savings programme', 'Pricing discount applied', 'Item Subtotal VAT Rate',
        ]
        output_directory = os.path.dirname(os.path.abspath(amazon_order_report_filename))
        try:
            with open(amazon_order_report_filename, encoding='utf-8', errors='ignore') as f:
                first_line = f.readline()
                if first_line and 'Order ID' not in first_line:
                    print("Fehler: 'Order ID' nicht im CSV-Header gefunden.")
                    print(
                        "Hinweis: Bitte stelle vor dem Generieren des Amazon-Berichts die Sprache auf Englisch um!")
                    return None
        except Exception as e:
            print(f"Fehler beim Prüfen der Datei: {e}")

        try:
            amz_report: pd.DataFrame = pd.read_csv(
                amazon_order_report_filename,
                decimal=',',
                on_bad_lines='skip',
                usecols=lambda c: c in usecols,
                parse_dates=['Order Date'],
                date_format='%d/%m/%Y',
                dtype={
                    'Order Net Total': float, 'Item Quantity': float, 'Received quantity': float,
                    'Payment Amount': float, 'Item Net Total': float, 'Item subtotal sum': float
                },
                converters={
                    'Item model number': lambda x: str(x).strip('=" '),
                },
            )
        except FileNotFoundError:
            print(f"Error: File '{amazon_order_report_filename}' not found.")
            return None

        # Determine Amazon marketplace language from title keywords
        locale = self._detect_amazon_marketplace(amz_report['Title'])
        locale_file_prefix = locale.replace('.', '_') + '_'
        print(f"Detected Amazon marketplace: {locale}")

        # Correct duplicates in order report
        amz_report = self.fix_order_report_duplicates(amz_report)

        # Drop lines without Payment Reference (unshipped, pending or cancelled orders)
        amz_report = amz_report.dropna(subset=['Payment reference ID']).copy()
        amz_report = amz_report.loc[
            (amz_report['Payment reference ID'].str.strip() != '') & (amz_report['Item Quantity'] > 0)]

        # Import LEGO manufacturer lines only
        amz_report = amz_report.loc[amz_report['Manufacturer'].str.lower() == 'lego']

        if amz_report.empty:
            print("No valid or paid Lego orders found in report.")
            return None

        # Filter out previously imported purchase lines via Database tracking (Payment reference ID + ASIN)
        new_orders = self.db.filter_new_amazon_orders(amz_report, 'Payment reference ID', 'ASIN')

        # Verify quantities against Amazon Receiving reports if enabled
        if self.use_received and 'Received quantity' in new_orders.columns:
            received_mask = new_orders['Received quantity'] > 0
            missing_items_mask = received_mask & (new_orders['Received quantity'] != new_orders['Item Quantity'])
            if missing_items_mask.any():
                orders_with_missing_items = new_orders[missing_items_mask]
                print("Missing received quantities detected (exported cases to CSV): \n",
                      orders_with_missing_items[['Order ID', 'Item model number', 'Item Quantity', 'Received quantity']]
                      )
                orders_with_missing_items.to_csv(
                    os.path.join(output_directory, locale_file_prefix + "missing_items.csv"), index=False)
                new_orders.loc[missing_items_mask, 'Item subtotal sum'] = (
                        new_orders['Item subtotal sum'] * new_orders['Received quantity'] / new_orders['Item Quantity']
                )
                new_orders.loc[missing_items_mask, 'Item Quantity'] = new_orders['Received quantity']
            new_orders = new_orders[received_mask]

        if new_orders.empty:
            print("No new purchase lines to import.")
            return None

        # Consolidate split shipments of the same ASIN within the same order
        aggregated_report = new_orders.groupby(['Order ID', 'ASIN']).agg({
            'Order Date': 'first',
            'Item model number': 'first',
            'Item Quantity': 'sum',
            'Item subtotal sum': 'sum',
        }).reset_index()
        report = aggregated_report.copy()

        # Calculate Brickmerge depot acquisition values (including 19% VAT)
        report['buy_price'] = (report['Item subtotal sum'] / report['Item Quantity'] * 1.19).round(2)
        report['note'] = locale
        report.rename(columns={
            'Item model number': 'setNo',
            'Item Quantity': 'qty',
            'Order Date': 'buy_date',
        }, inplace=True)
        timestamp = report['buy_date'].max().date().strftime('%d_%m_%Y')
        report['buy_date'] = report['buy_date'].dt.strftime('%d.%m.%Y')
        out_filename = f"{locale_file_prefix}{timestamp}_brickmerge_import.csv"
        report[['setNo', 'qty', 'buy_date', 'buy_price', 'note']].to_csv(
            os.path.join(output_directory, out_filename), sep=';', decimal=',', index=False
        )

        # Persist newly processed purchases into the SQLite database
        self.db.record_amazon_orders(
            new_orders,
            payment_reference_col='Payment reference ID',
            asin_col='ASIN',
            item_col='Item model number'
        )

        print(f"Successfully created import CSV '{out_filename}' for {len(report)} purchase line items.")
        return report

    @staticmethod
    def fix_order_report_duplicates(amazon_order_report: pd.DataFrame) -> pd.DataFrame:
        """
        Corrects duplicate line items in Amazon Business Order Reports.
        Divides quantities and amounts across duplicate shipment rows and validates
        line subtotals against total captured payment transactions.
        """
        # 1. Define identification and detail columns
        main_cols = {
            'Order ID', 'ASIN', 'Item Quantity', 'Received quantity', 'Item Net Total', 'Item subtotal sum',
        }
        identification_cols = {
            'Item Subtotal', 'Item Shipping & Handling', 'Item Promotion', 'Item VAT', 'Pricing savings programme',
            'Pricing discount applied', 'Item Subtotal VAT Rate',
        }
        group_cols = [col for col in main_cols.union(identification_cols) if col in amazon_order_report.columns]

        # Ignore cancellations
        amazon_order_report = amazon_order_report.loc[amazon_order_report['Item Quantity'] > 0]

        # Detect duplicated lines where single-line payment amount differs from item net total
        mismatch_mask = (amazon_order_report['Payment Amount'] != amazon_order_report['Item Net Total'])
        dup_counts = pd.Series(1, index=amazon_order_report.index)
        if mismatch_mask.any():
            dup_counts.loc[mismatch_mask] = amazon_order_report[mismatch_mask].groupby(
                group_cols, dropna=False
            )['Order ID'].transform('size')
        mask_dup = dup_counts > 1

        # Drop non-essential identification columns
        cols_to_drop = [c for c in identification_cols if c in amazon_order_report.columns]
        fixed_report = amazon_order_report.drop(columns=cols_to_drop).copy()

        if mask_dup.any():
            # 1. Summe der Artikelmenge NUR über die zu teilenden Zeilen pro Bestellung ermitteln
            dup_qty_sum = fixed_report.loc[mask_dup].groupby(['Order ID', 'ASIN'])['Item Quantity'].transform('sum')

            # 2. Prüfen, ob die Summe glatt durch die Duplikate teilbar ist
            is_div = (dup_qty_sum % dup_counts.loc[mask_dup]).round(4) == 0
            mask_div = pd.Series(False, index=fixed_report.index)
            mask_div.loc[mask_dup] = is_div

            print(
                f"Notice: {mask_div.sum()} duplicate lines detected. Scaling quantities and subtotals by duplicate factor.")

            # Divide numerical totals by the duplication count
            cols_to_divide = [
                'Item Quantity', 'Received quantity', 'Item Net Total', 'Item subtotal sum',
            ]
            cols_to_divide = [c for c in cols_to_divide if c in fixed_report.columns]

            for col in cols_to_divide:
                fixed_report[col] = fixed_report[col].astype(float)
                fixed_report.loc[mask_div, col] /= dup_counts[mask_div]

            # Cross-verify calculated line totals against actual Order Total and transaction payment sums
            order_lines_sum = fixed_report.groupby('Order ID')['Item Net Total'].sum()
            order_total = fixed_report.groupby('Order ID')['Order Net Total'].first()
            payments = fixed_report[['Order ID', 'Payment reference ID', 'Payment Amount']].drop_duplicates()
            total_payments_by_order = payments.groupby('Order ID')['Payment Amount'].sum()

            line_differences = (order_lines_sum - order_total).round(2)
            compare_df = pd.DataFrame({'order_total': order_total, 'sum_lines': order_lines_sum,
                                       'pay_total': total_payments_by_order, 'diff_line_sum_total': line_differences,
                                       'diff_line_sum_pay': order_lines_sum - total_payments_by_order})
            is_different_mask = line_differences.abs() > 0.1
            if is_different_mask.any():
                order_with_differences = line_differences[is_different_mask].index
                differences_mask = fixed_report['Order ID'].isin(order_with_differences) & mask_dup
                if differences_mask.any():
                    print("Warning: The following order lines might contain unresolved duplicates: \n",
                          fixed_report.loc[
                              differences_mask, ['Order ID', 'ASIN', 'Item Quantity',
                                                 'Item subtotal sum']].to_string())
                print("Warning: Unresolved order line sum differences:\n",
                      compare_df.loc[is_different_mask].to_string())

        return fixed_report

    @staticmethod
    def _detect_amazon_marketplace(titles_series: pd.Series) -> str:
        """
        Determines the Amazon marketplace origin by matching language-specific title keywords.
        """
        all_text = " ".join(titles_series.dropna().astype(str)).lower()

        language_scores = {
            'Amazon.de': sum(
                all_text.count(w) for w in ['spielzeug', 'geschenk', 'junge', 'mädchen', 'jahre', 'kinder']
            ),
            'Amazon.fr': sum(
                all_text.count(w) for w in ['jouet', 'cadeau', 'garçon', 'fille', 'ans', 'dès', 'enfants']
            ),
            'Amazon.it': sum(
                all_text.count(w) for w in ['giocattolo', 'costruzioni', 'bambini', 'anni', 'ragazzo', 'ragazza']
            ),
            'Amazon.es': sum(
                all_text.count(w) for w in ['juguete', 'piezas', 'niños', 'años', 'niño', 'niña', 'desde']
            ),
        }

        best_match = max(language_scores, key=language_scores.__getitem__)

        # Verify that match threshold is proportional to report volume
        if language_scores[best_match] > 0.5 * len(titles_series):
            return best_match

        return 'Amazon.de'