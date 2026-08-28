"""
Generiert Brickmerge-Import aus Business Order Reports (Depot) und Amazon Bestellberichten (Verkäufe).
"""
import re
import numpy as np
import pandas as pd
import sqlite3


class AmazonImporter:
    """
    Amazon Bestellungen und Verkäufe importieren und neue Daten in Brickmerge-Import-Format exportieren.
    """

    def __init__(self, use_received: bool = True, db_path: str = "amazon_order_import.db"):
        self.use_received = use_received  # Amazon Receiving Daten verwenden, um fehlende Artikel abzugleichen
        self.aggregate_sales = True  # Verkäufe zu gleichem Preis im selben Import und Monat zusammenfassen
        self.shipping_cost_base = 5.0  # Schätzung Versandkosten für Amazon Verkäufe:
        self.shipping_cost_percentage = 0.03  # shipping_cost_base + shipping_cost_percentage * Verkaufspreis

        self.conn = sqlite3.connect(db_path)
        self._init_db()

    def _init_db(self):
        """Erstellt die Tabellen für bereits verarbeitete Positionen."""
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS imported_purchase_orders (
                    "Payment reference ID" TEXT,
                    "Item model number" TEXT,
                    ASIN TEXT,
                    import_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY ("Payment reference ID", "Item model number")
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS imported_sales_orders (
                    "amazon-order-id" TEXT,
                    asin TEXT,
                    import_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY ("amazon-order-id", asin)
                )
            """)

    def import_from_amazon_orders_report(self, amazon_order_report_filename: str) -> pd.DataFrame | None:
        """
        Erstellt eine Brickmerge Depot Import CSV aus einem Amazon Orders Report.
        :return:
        """
        usecols = [
            'Order ID', 'Order Date', 'Payment reference ID', 'Item model number', 'Item Quantity', 'Payment Amount',
            'Item Net Total', 'Item subtotal sum', 'Received quantity', 'ASIN', 'Title', 'Manufacturer',
            'Item Subtotal', 'Item Shipping & Handling', 'Item Promotion', 'Item VAT', 'Pricing savings programme',
            'Pricing discount applied', 'Item Subtotal VAT Rate',
        ]
        try:
            amz_report: pd.DataFrame = pd.read_csv(
                amazon_order_report_filename,
                decimal=',',
                on_bad_lines='skip',
                usecols=lambda c: c in usecols,
                parse_dates=['Order Date'],
                date_format='%d/%m/%Y',
                dtype={'Item Quantity': float, 'Received quantity': float,
                       'Payment Amount': float, 'Item Net Total': float, 'Item subtotal sum': float, },
                converters={
                    'Item model number': lambda x: str(x).strip('=" '),
                },
            )
        except FileNotFoundError:
            print(f"Fehler: Datei {amazon_order_report_filename} nicht gefunden.")
            return None

        # Ermitteln des Amazon-Marktplatzes basierend auf den Artikelnamen
        locale = self._detect_amazon_marketplace(amz_report['Title'])
        locale_file_prefix = locale.replace('.', '_') + '_'
        print(f"Amazon-Marktplatz erkannt: {locale}")

        # Zeilen ohne Payment Reference ignorieren (noch nicht verschickte oder stornierte Bestellungen)
        amz_report = amz_report.dropna(subset=['Payment reference ID']).copy()
        amz_report = amz_report.loc[amz_report['Payment reference ID'].str.strip() != '']
        amz_report = self.fix_order_report_duplicates(amz_report)

        # Nur LEGO importieren
        amz_report = amz_report.loc[amz_report['Manufacturer'].str.lower() == 'lego']

        if amz_report.empty:
            print("Keine bezahlten/gültigen Bestellungen in der CSV gefunden.")
            return None

        # Bereits importierte Bestellpositionen aus der Datenbank holen und neue Bestellpositionen filtern
        processed_df = pd.read_sql(
            'SELECT "Payment reference ID", "Item model number" FROM imported_purchase_orders', self.conn)
        if not processed_df.empty:
            keys = ['Payment reference ID', 'Item model number']
            is_processed = amz_report.set_index(keys).index.isin(processed_df.set_index(keys).index)
            new_orders: pd.DataFrame = amz_report[~is_processed].copy()
        else:
            new_orders: pd.DataFrame = amz_report.copy()

        if self.use_received and 'Received quantity' in new_orders.columns:
            received_mask = new_orders['Received quantity'] > 0
            missing_items_mask = received_mask & (new_orders['Received quantity'] != new_orders['Item Quantity'])
            if missing_items_mask.any():
                orders_with_missing_items = new_orders[missing_items_mask]
                print("Lieferungen mit fehlenden Artikeln prüfen (Fälle als CSV exportiert): \n",
                      orders_with_missing_items[
                          ['Order ID', 'Item model number', 'Item Quantity', 'Received quantity']])
                orders_with_missing_items.to_csv(locale_file_prefix + "missing_items.csv", index=False)
                new_orders.loc[missing_items_mask, 'Item subtotal sum'] = (
                        new_orders['Item subtotal sum'] * new_orders['Received quantity'] / new_orders['Item Quantity']
                )
                new_orders.loc[missing_items_mask, 'Item Quantity'] = new_orders['Received quantity']
            new_orders = new_orders[received_mask]

        if new_orders.empty:
            print("Keine neuen Zahlungen/Lieferungen zum Importieren gefunden.")
            return None

        # Separate Lieferungen aus selber Bestellung zusammenfassen
        aggregated_report = new_orders.groupby(['Order ID', 'ASIN']).agg({
            'Order Date': 'first',
            'Item model number': 'first',
            'Item Quantity': 'sum',
            'Item subtotal sum': 'sum',
        }).reset_index()
        report = aggregated_report.copy().reset_index()

        # Brickmerge Import Format
        report['buy_price'] = (report['Item subtotal sum'] / report['Item Quantity'] * 1.19).round(2)
        report['note'] = locale
        report.rename(columns={
            'Item model number': 'setNo',
            'Item Quantity': 'qty',
            'Order Date': 'buy_date',
        }, inplace=True)
        timestamp = report['buy_date'].max().date().strftime('%d_%m_%Y')
        report['buy_date'] = report['buy_date'].dt.strftime('%d.%m.%Y')
        report[['setNo', 'qty', 'buy_date', 'buy_price', 'note']].to_csv(
            locale_file_prefix + timestamp + "_brickmerge_import.csv",
            sep=';', decimal=',', index=False)

        # Importierte Bestellpositionen in die Datenbank schreiben
        new_payment_ids = new_orders[['Payment reference ID', 'Item model number', 'ASIN']].drop_duplicates()
        new_payment_ids.to_sql('imported_purchase_orders', self.conn, if_exists='append', index=False)

        print(
            f"Erfolgreich Import CSV für {len(report)} neue Bestellpositionen erstellt.")
        return report

    def import_amazon_sales(self, amazon_sales_report_filename: str, depot_export_file: str) -> pd.DataFrame | None:
        """
        Erstellt eine Brickmerge Verkäufe Import CSV aus einem Amazon Bestellbericht.
        """
        usecols = [
            'amazon-order-id', 'purchase-date', 'order-status', 'fulfillment-channel', 'product-name',
            'asin', 'quantity', 'item-price', 'shipping-price', 'item-promotion-discount', 'ship-promotion-discount'
        ]
        try:
            amz_report = pd.read_csv(
                amazon_sales_report_filename,
                sep='\t',
                on_bad_lines='skip',
                converters={
                    'product-name': self.get_set_number_from_title,
                },
                parse_dates=['purchase-date'],
                usecols=lambda c: c in usecols
            )
        except FileNotFoundError:
            print(f"Fehler: Datei {amazon_sales_report_filename} nicht gefunden.")
            return None

        # Nur versandte Bestellungen betrachten
        amz_report = amz_report[amz_report['order-status'] == 'Shipped']

        # Alte Bestellungen aus der Datenbank holen und neue Bestellungen filtern
        processed_df = pd.read_sql('SELECT "amazon-order-id", "asin" FROM imported_sales_orders', self.conn)
        if not processed_df.empty:
            keys = ['amazon-order-id', 'asin']
            is_processed = amz_report.set_index(keys).index.isin(processed_df.set_index(keys).index)
            new_orders = amz_report[~is_processed].copy()
        else:
            new_orders = amz_report.copy()

        # Set-Nr aus ASIN ermitteln falls nicht vorhanden
        new_orders['set_identifier'] = new_orders['product-name'].fillna(
            new_orders['asin'].map(self.asin_to_set_number_fallback))
        if new_orders['set_identifier'].isna().any():
            missing_identifiers = new_orders[new_orders['set_identifier'].isna()]
            print("CSV Datei mit Bestellpositionen ohne Set-Nummer erstellt, für Import ignoriert.")
            missing_identifiers.to_csv("missing_set_identifiers.csv", index=False)
        new_orders = new_orders[~new_orders['set_identifier'].isna()]

        if new_orders.empty:
            print("Keine neuen Zahlungen/Lieferungen zum Importieren gefunden.")
            return None

        # Verkaufspreis berechnen
        new_orders['total_revenue'] = new_orders[['item-price', 'shipping-price']].sum(axis=1)
        discount_columns = {'item-promotion-discount', 'ship-promotion-discount'}.intersection(set(new_orders.columns))
        if discount_columns:
            new_orders['total_revenue'] -= new_orders[list(discount_columns)].sum(axis=1)
        new_orders['sale_price'] = new_orders['total_revenue'] / new_orders['quantity']

        # Verkaufskosten schätzen
        new_orders['sales_cost'] = (
                new_orders['total_revenue'] * (0.15 * 1.19 + self.shipping_cost_percentage) + self.shipping_cost_base)

        if self.aggregate_sales:
            new_orders['groupby_price'] = new_orders['sale_price'].map(int)
            new_orders['groupby_month'] = new_orders['purchase-date'].dt.strftime('%Y-%m')
            new_orders['note'] = new_orders['purchase-date']
            aggregated_sales = new_orders.groupby(
                ['asin', 'groupby_month', 'groupby_price', 'fulfillment-channel']
            ).agg({
                'set_identifier': 'first',
                'purchase-date': 'max',
                'sale_price': 'mean',
                'quantity': 'sum',
                'sales_cost': 'sum',
                'note': lambda d: f"Zusammengefasste Verkäufe {d.min().strftime('%d.%m.')}"
                                  f" - {d.max().strftime('%d.%m.%Y')}" if d.min().date() != d.max().date() else "",
            })
            report = aggregated_sales.copy().reset_index()
        else:
            report = new_orders.copy()

        # Brickmerge Import Format
        channel = np.where(report['fulfillment-channel'] == 'Amazon', 'Amazon FBA', 'Amazon FBM')
        import_df = pd.DataFrame({'setNo': report['set_identifier'].map(lambda x: x if '-' not in x else None),
                                  'artikleNo': report['set_identifier'].map(lambda x: x if '-' in x else None),
                                  'sale_date': report['purchase-date'].dt.strftime('%d.%m.%Y'),
                                  'sale_price': report['sale_price'].round(2),
                                  'qty': report['quantity'],
                                  'fees': report['sales_cost'].round(2),
                                  'channel': channel,
                                  'note': report['note'] if 'note' in report.columns else None,
                                  })

        import_df['buy_price'] = self.process_sales(import_df, depot_export_file).round(2)

        timestamp = new_orders['purchase-date'].max().date().isoformat()
        import_df.to_csv(f"amazon_sales_{timestamp}_brickmerge_import.csv", index=False)

        # Importierte Bestellpositionen in die Datenbank schreiben
        new_order_ids = new_orders[['amazon-order-id', 'asin']].drop_duplicates()
        new_order_ids.to_sql('imported_sales_orders', self.conn, if_exists='append', index=False)

        print(
            f"Erfolgreich Import CSV für {import_df.qty.sum()} neue Sales erstellt.")
        return import_df

    @staticmethod
    def fix_order_report_duplicates(amazon_order_report: pd.DataFrame) -> pd.DataFrame:
        """
        Doppelte Zeilen aus dem Amazon Bestellbericht korrigieren.
        """
        # 1. Identifikation-Spalten festlegen
        main_cols = {
            'Order ID', 'ASIN', 'Item Quantity', 'Received quantity', 'Item Net Total', 'Item subtotal sum',
        }
        identification_cols = {
            'Item Subtotal', 'Item Shipping & Handling', 'Item Promotion', 'Item VAT', 'Pricing savings programme',
            'Pricing discount applied', 'Item Subtotal VAT Rate',
        }
        group_cols = [col for col in main_cols.union(identification_cols) if col in amazon_order_report.columns]

        # Doppelte Zeilen mit Mismatch in Payment Amount und Item Net Total finden
        mismatch_mask = (amazon_order_report['Payment Amount'] != amazon_order_report['Item Net Total'])
        dup_counts = pd.Series(1, index=amazon_order_report.index)
        if mismatch_mask.any():
            dup_counts.loc[mismatch_mask] = amazon_order_report[mismatch_mask].groupby(
                group_cols, dropna=False
            )['Order ID'].transform('size')
        mask_dup = dup_counts > 1

        # Identifikation Spalten entfernen
        cols_to_drop = [c for c in identification_cols if c in amazon_order_report.columns]
        fixed_report = amazon_order_report.drop(columns=cols_to_drop).copy()

        if mask_dup.any():
            print(
                f"Hinweis: {mask_dup.sum()} doppelte Einträge im Order Report erkannt."
                f" Mengen und Beträge werden durch die Anzahl der Dopplungen geteilt.")

            # 3. Alle relevanten Spalten durch die Anzahl der Duplikate teilen
            cols_to_divide = [
                'Item Quantity', 'Received quantity', 'Item Net Total', 'Item subtotal sum',
            ]
            cols_to_divide = [c for c in cols_to_divide if c in fixed_report.columns]

            for col in cols_to_divide:
                fixed_report[col] = fixed_report[col].astype(float)
                fixed_report.loc[mask_dup, col] /= dup_counts[mask_dup]

            if 'Payment Amount' in fixed_report.columns:
                # Verify Payment sum
                payments = fixed_report[['Order ID', 'Payment reference ID', 'Payment Amount']].drop_duplicates()
                total_payments_by_order = payments.groupby('Order ID')['Payment Amount'].sum()
                order_totals = fixed_report.groupby('Order ID')['Item Net Total'].sum()

                payment_differences = total_payments_by_order - order_totals
                is_different_mask = payment_differences.abs() > 0.1
                if is_different_mask.any():
                    order_with_differences = payment_differences[is_different_mask].index
                    differences_mask = amazon_order_report['Order ID'].isin(order_with_differences) & mask_dup
                    amazon_order_report.loc[differences_mask].to_csv('unresolved_duplicates.csv', index=False)
                    print("Hinweis: Unresolved Payment Differences:\n", payment_differences[is_different_mask])

        return fixed_report

    @staticmethod
    def get_set_number_from_title(title: str) -> str | None:
        """
        Extrahiert die Set-Nr (5+ Ziffern) aus dem Artikeltitel.
        Gibt None zurück, falls keine oder mehrere gefunden wurden.
        """
        if not isinstance(title, str) or 'minifiguren serie' in title.lower():
            return None

        # Nummern im Titel suchen, Bindestrich-Endung mitnehmen
        matches = re.findall(r'\b\d{5,}(?:-\d+)?\b', title)

        # Falls nur ein Match, dieses ausgeben
        if len(matches) == 1:
            return matches[0]

        # Falls nur eine 5-stellige Nummer im Titel, diese ausgeben
        five_digit_matches = [m for m in matches if len(m.split('-')[0]) == 5]
        if len(five_digit_matches) == 1:
            return five_digit_matches[0]

        # Keine oder mehrere 5-stellige Nummern im Titel
        print(f"Keine Set-Nr im Titel gefunden: {title}")
        return None

    @staticmethod
    def _detect_amazon_marketplace(titles_series: pd.Series) -> str:
        """
        Ermittelt anhand von simplen Worthäufigkeiten in den Artikelnamen,
        um welchen Amazon-Marktplatz es sich bei dem Export handelt.
        """
        # Alle Titel zu einem riesigen, kleingeschriebenen String zusammenfassen
        all_text = " ".join(titles_series.dropna().astype(str)).lower()

        # Typische, eindeutige LEGO-Keywords pro Sprache
        language_scores = {
            'Amazon.de': sum(
                all_text.count(w) for w in ['spielzeug', 'geschenk', 'junge', 'mädchen', 'jahre', 'kinder']),
            'Amazon.fr': sum(
                all_text.count(w) for w in ['jouet', 'cadeau', 'garçon', 'fille', 'ans', 'dès', 'enfants']),
            'Amazon.it': sum(
                all_text.count(w) for w in ['giocattolo', 'costruzioni', 'bambini', 'anni', 'ragazzo', 'ragazza']),
            'Amazon.es': sum(
                all_text.count(w) for w in ['juguete', 'piezas', 'niños', 'años', 'niño', 'niña', 'desde']),
        }

        # Den Shop mit den meisten Treffern ermitteln
        best_match = max(language_scores, key=language_scores.get)

        # Sicherheitsnetz: Die gefundenen Keywords müssen in Relation zur Dateigröße stehen
        if language_scores[best_match] > 0.5 * len(titles_series):
            return best_match

        # Fallback auf Amazon.de
        return 'Amazon.de'

    def asin_to_set_number_fallback(self, asin: str) -> str | None:
        """
        Set-Nr aus ASIN ermitteln anhand von manueller Zuordnung oder vergangener Einkäufe
        """
        # 1. Mit manueller Zuordnung
        manual_entries = {
            'B0DRYF9TL5': '71048-12',
            'B0FM8LKWXZ': '71050-12',
            'B0G64WCRKF': '71051-12',
            'B0GXWW6NLG': '71052-12',
        }
        if asin in manual_entries:
            return manual_entries[asin]

        # 2. Zuordnung anhand der eigenen Amazon-Käufe
        try:
            set_no_df = pd.read_sql('SELECT "Item model number" FROM imported_purchase_orders WHERE ASIN = ?',
                                    self.conn, params=(asin,))
        except Exception as e:
            print(f"Fehler bei DB-Abfrage für ASIN {asin}: {e}")
            return None

        if not set_no_df.empty:
            result = set_no_df.iloc[0]['Item model number']
            if pd.notna(result):
                return str(result)

        return None

    @staticmethod
    def process_sales(sales: pd.DataFrame, brickmerge_depot_export: str) -> pd.Series:
        """
        Übergangslösung, um importierte Sales mit Depot bestand zu verrechnen.
        :param sales: Sales DataFrame (Brickmerge Verkäufe Format)
        :param brickmerge_depot_export: Brickmerge Depot Export CSV
        :return: EK Preise als Series, Deport import wird als CSV ausgegeben
        """
        # 1. Depot einlesen
        bm_depot_df = pd.read_csv(brickmerge_depot_export, sep=';', decimal=',',
                                  parse_dates=['buy_date'], date_format='%d.%m.%Y',
                                  dtype={'setNo': str, 'EAN': str, 'artikleNo': str, 'qty': int, 'buy_price': float,
                                         'condition': str, 'storage': str, 'note': str, 'setnote': str})
        ek_list = []

        for _, row in sales.iterrows():
            # 2. Identifikator bestimmen
            article_no = row.get('artikleNo')
            set_no = row.get('setNo')

            if isinstance(article_no, str):
                mask = bm_depot_df['artikleNo'] == article_no
            else:
                mask = bm_depot_df['setNo'] == set_no

            # Nur Bestand berücksichtigen, der > 0 ist, sortiert nach FIFO
            mask &= bm_depot_df['qty'] > 0
            stock_indices = bm_depot_df[mask].sort_values('buy_date').index

            qty_needed = row['qty']
            total_ek = 0.0
            total_ek_qty = 0
            qty_fulfilled = 0

            # 3. Bestand zeilenweise abbauen
            for idx in stock_indices:
                if qty_needed <= 0:
                    break

                available = bm_depot_df.at[idx, 'qty']
                take = min(available, qty_needed)

                # EK addieren und Mengen aktualisieren
                if pd.notna(bm_depot_df.at[idx, 'buy_price']):
                    total_ek += take * bm_depot_df.at[idx, 'buy_price']
                    total_ek_qty += take
                qty_needed -= take
                qty_fulfilled += take

                # Bestand im Original-DataFrame reduzieren
                bm_depot_df.at[idx, 'qty'] -= take

            # 4. Durchschnittlichen EK berechnen
            avg_ek = (total_ek / total_ek_qty) if total_ek_qty > 0 else 0.0
            ek_list.append(avg_ek)

            # Warnung, falls wir im Depot nicht genug Sets hatten
            if qty_needed > 0:
                ident = article_no if isinstance(article_no, str) else set_no
                print(
                    f"Warnung: Depot-Fehlbestand für Set {ident}. Fehlende Menge: {qty_needed}")

        # 5. Alle komplett ausverkauften Zeilen (qty == 0) am Ende in einem Rutsch entfernen
        bm_depot_df = bm_depot_df[bm_depot_df['qty'] > 0].copy()

        # 6. Sicherer Export
        output_filename = brickmerge_depot_export.rsplit('.csv', 1)[0] + "_after_sales.csv"

        # Datumsformat für Brickmerge wieder herstellen
        bm_depot_df['buy_date'] = bm_depot_df['buy_date'].dt.strftime('%d.%m.%Y')
        bm_depot_df.to_csv(output_filename, sep=';', decimal=',', index=False)

        return pd.Series(ek_list, index=sales.index)

    def delete_orders_table(self):
        """
        Löscht Historie der Bestellungen aus der Datenbank.
        """
        with self.conn:
            self.conn.execute("DROP TABLE IF EXISTS imported_purchase_orders")
        self._init_db()

    def delete_sales_table(self):
        """
        Löscht Historie der Verkäufe aus der Datenbank.
        """
        with self.conn:
            self.conn.execute("DROP TABLE IF EXISTS imported_sales_orders")
        self._init_db()


if __name__ == "__main__":
    importer = AmazonImporter()

    # Order Reports Importieren:
    # importer.delete_orders_table()
    importer.import_from_amazon_orders_report("my_amazon_orders.csv")

    # Sales Reports Importieren:
    # importer.delete_sales_table()
    importer.import_amazon_sales("bestellbericht.txt", "brickmerge_bestand.csv")