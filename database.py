"""Manages SQLite storage and deduplication tracking for purchases and sales."""
import sqlite3
import pandas as pd


class Database:
    """Manages SQLite storage and deduplication tracking for purchases and sales."""

    def __init__(self, db_path: str = "import_history.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
        self._init_db()
        self.add_examples()

    def _init_db(self):
        """Initializes database tables and indexes if they do not exist."""
        with self.conn:
            # Table tracking imported purchase orders (depot acquisitions)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS imported_amazon_purchases (
                    payment_reference TEXT,
                    asin TEXT,
                    item_number TEXT,
                    import_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (payment_reference, asin)
                )
            """)

            # Table tracking sales transactions across multiple platforms
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS imported_sales (
                    platform TEXT,
                    order_id TEXT,
                    item_identifier TEXT,
                    import_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (platform, order_id, item_identifier)
                )
            """)

            # Key-Value settings store for GUI options, shipping & fees
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            # Custom manual mappings for ASIN and eBay item numbers to Lego set numbers
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS item_mappings (
                    platform   TEXT, -- 'Amazon', 'eBay' or 'BrickLink'
                    identifier TEXT, -- ASIN, eBay Item Numbers/SKUs or BrickLink Item IDs
                    set_number TEXT, -- Mapped Lego Set Number
                    note       TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (platform, identifier)
                )
            """)

            # Index to optimize platform-specific lookups during import checks
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_platform ON imported_sales(platform);")

    # ---------------- SETTINGS API ----------------

    def get_setting(self, key: str, default: str = "") -> str:
        """Retrieves a single setting value as string."""
        cur = self.conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row is not None else default

    def set_setting(self, key: str, value: str):
        """Persists a single key-value setting."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value))
            )

    def get_all_settings(self) -> dict[str, str]:
        """Retrieves all stored configuration settings."""
        cur = self.conn.execute("SELECT key, value FROM app_settings")
        return dict(cur.fetchall())

    # ---------------- ITEM MAPPINGS API ----------------

    def get_mapped_set_number(self, platform: str, identifier: str) -> str | None:
        """Retrieves custom set number mapping for an ASIN or eBay item number."""
        cur = self.conn.execute(
            "SELECT set_number FROM item_mappings WHERE platform = ? AND identifier = ?",
            (platform, identifier)
        )
        row = cur.fetchone()
        return row[0] if row else None

    def get_all_mappings(self) -> list[tuple[str, str, str, str]]:
        """Returns all custom mappings sorted by platform and identifier."""
        cur = self.conn.execute(
            "SELECT platform, identifier, set_number, note FROM item_mappings ORDER BY platform, identifier")
        return cur.fetchall()

    def set_mapping(self, platform: str, identifier: str, set_number: str, note: str = ""):
        """Adds or updates a custom identifier mapping."""
        with self.conn:
            self.conn.execute("""
                              INSERT INTO item_mappings (platform, identifier, set_number, note, updated_at)
                              VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                              ON CONFLICT(platform, identifier) DO UPDATE SET set_number = excluded.set_number,
                                                                              note       = excluded.note,
                                                                              updated_at = CURRENT_TIMESTAMP
                              """, (platform, identifier.strip(), set_number.strip(), note.strip()))

    def delete_mapping(self, platform: str, identifier: str):
        """Removes a custom mapping from the database."""
        with self.conn:
            self.conn.execute("DELETE FROM item_mappings WHERE platform = ? AND identifier = ?",
                              (platform, identifier))

    def add_examples(self):
        """Inserts default mappings if the mapping table is empty."""
        with self.conn:
            if self.conn.execute("SELECT COUNT(*) FROM item_mappings").fetchone()[0] == 0:
                default_mappings = [
                    ('BrickLink', '6533318-1', '71048-36', '36er Box Serie 27'),
                    ('BrickLink', '6623266-1', '71052-36', '36er Box Serie 29'),
                ]
                self.conn.executemany(
                    "INSERT OR IGNORE INTO item_mappings (platform, identifier, set_number, note) VALUES (?, ?, ?, ?)",
                    default_mappings
                )

    # ---------------- IMPORT TRACKING API ----------------

    def filter_new_amazon_orders(self, df: pd.DataFrame, payment_reference_col: str, asin_col: str) -> pd.DataFrame:
        """Filters out previously imported Amazon purchase items."""
        query = 'SELECT payment_reference, asin FROM imported_amazon_purchases'
        processed = pd.read_sql(query, self.conn)
        if processed.empty:
            return df.copy()

        df_keys = df[[payment_reference_col, asin_col]].astype(str)
        is_processed = df_keys.set_index([payment_reference_col, asin_col]).index.isin(
            processed.set_index(['payment_reference', 'asin']).index
        )
        return df[~is_processed].copy()

    def record_amazon_orders(self, df: pd.DataFrame, payment_reference_col: str, asin_col: str, item_col: str):
        """Persists newly imported Amazon purchases into the database."""
        if df.empty:
            return

        rename_map = {payment_reference_col: 'payment_reference', asin_col: 'asin', item_col: 'item_number'}

        records = df[list(rename_map.keys())].drop_duplicates().copy()
        records.rename(columns=rename_map, inplace=True)
        records.to_sql('imported_amazon_purchases', self.conn, if_exists='append', index=False)

    def filter_new_sales(self, df: pd.DataFrame, platform: str, order_col: str, item_col: str) -> pd.DataFrame:
        """Filters out previously imported sales records for a specific sales channel."""
        query = "SELECT order_id, item_identifier FROM imported_sales WHERE platform = ?"
        processed = pd.read_sql(query, self.conn, params=(platform,))
        if processed.empty:
            return df.copy()

        df_keys = df[[order_col, item_col]].astype(str)
        is_processed = df_keys.set_index([order_col, item_col]).index.isin(
            processed.set_index(['order_id', 'item_identifier']).index
        )
        return df[~is_processed].copy()

    def record_sales(self, df: pd.DataFrame, platform: str, order_col: str, item_col: str):
        """Persists processed sales records into the database with their respective platform tag."""
        if df.empty:
            return

        records = df[[order_col, item_col]].drop_duplicates().copy()
        records.rename(columns={order_col: 'order_id', item_col: 'item_identifier'}, inplace=True)
        records['platform'] = platform
        records.to_sql('imported_sales', self.conn, if_exists='append', index=False)

    def get_set_number_by_asin(self, asin: str) -> str | None:
        """Retrieves the known Lego set number for a given ASIN based on prior purchases."""
        cursor = self.conn.execute("""
            SELECT item_number FROM imported_amazon_purchases
            WHERE asin = ? AND item_number IS NOT NULL LIMIT 1
        """, (asin,))
        row = cursor.fetchone()
        return row[0] if row else None

    def delete_last_processed(self, is_sales: bool = True):
        """Rolls back the most recent import batch by removing records matching the latest timestamp."""
        table = 'imported_sales' if is_sales else 'imported_amazon_purchases'
        col_platform = "platform, " if is_sales else ""

        with self.conn:
            cursor = self.conn.execute(
                f"SELECT {col_platform}import_timestamp FROM {table} ORDER BY import_timestamp DESC LIMIT 1"
            )
            row = cursor.fetchone()

            if not row:
                print(f"Keine Tracking-Einträge zum Zurücksetzen in '{table}' gefunden.")
                return

            if is_sales:
                platform, last_timestamp = row
                target_desc = f"{platform} Sales ID Tracking"
            else:
                last_timestamp = row[0]
                target_desc = "Purchase ID Tracking"

            deleted_count = self.conn.execute(
                f"DELETE FROM {table} WHERE import_timestamp = ?", (last_timestamp,)
            ).rowcount

            print(
                f"Erfolgreich zurückgesetzt: {target_desc} ({deleted_count} Einträge gelöscht, Batch-Zeit: {last_timestamp})")