import io
import os
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from database import Database
from amazon_orders import AmazonPurchasesImporter
from amazon_sales import AmazonSalesImporter
from ebay_sales import EbaySalesImporter
from bricklink_sales import BricklinkSalesImporter
from set_number_parser import SetNumberParser


class ToolTip:
    """Floating tooltip window on mouse hover."""

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip_window = None
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        if self.tip_window or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)

        frame = tk.Frame(tw, background="#1e293b", borderwidth=1, relief="solid")
        frame.pack()
        label = tk.Label(
            frame, text=self.text, justify="left",
            background="#1e293b", foreground="#f8fafc",
            font=("Segoe UI", 9), padx=10, pady=6, wraplength=340
        )
        label.pack()

    def hide_tip(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


class TextRedirector(io.StringIO):
    """Redirects stdout and stderr safely into a Tkinter console widget."""

    def __init__(self, text_widget: tk.Text):
        super().__init__()
        self.text_widget = text_widget

    def write(self, string):
        # Sicherstellen, dass das Widget im GUI-Mainthread aktualisiert wird
        def _append():
            try:
                self.text_widget.configure(state="normal")
                self.text_widget.insert(tk.END, string)
                self.text_widget.see(tk.END)
                self.text_widget.configure(state="disabled")
            except (tk.TclError, RuntimeError):
                pass

        self.text_widget.after(0, _append)

    def flush(self):
        pass


class ModernImportGUI(tk.Tk):
    # Palette
    BG_MAIN = "#f1f5f9"
    CARD_BG = "#ffffff"
    ACCENT = "#2563eb"
    ACCENT_HOVER = "#1d4ed8"
    TEXT_MAIN = "#0f172a"
    TEXT_MUTED = "#64748b"

    def __init__(self):
        super().__init__()
        self.title("Brickmerge Depot Importer")
        # self.geometry("940x760")
        self.minsize(850, 700)
        self.configure(bg=self.BG_MAIN)

        self.db = Database("import_history.db")

        # Global Depot File (auto-saved)
        saved_depot = self.db.get_setting("depot_file", "")
        self.depot_file_var = tk.StringVar(value=saved_depot)
        self.depot_file_var.trace_add("write", lambda *_: self.db.set_setting("depot_file", self.depot_file_var.get()))
        self.ek_only_var = self._bind_setting_bool("ek_calculation_only", False)

        self._init_styles()
        self._build_layout()

        sys.stdout = TextRedirector(self.log_text)
        sys.stderr = TextRedirector(self.log_text)

    def _init_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        # Notebook
        style.configure("TNotebook", background=self.BG_MAIN, borderwidth=0)
        style.configure("TNotebook.Tab", font=("Segoe UI", 10, "bold"), padding=(14, 8),
                        background="#e2e8f0", foreground=self.TEXT_MUTED)
        style.map("TNotebook.Tab",
                  background=[("selected", self.CARD_BG)],
                  foreground=[("selected", self.ACCENT)])

        # Card frames & Labels
        style.configure("Card.TFrame", background=self.CARD_BG)
        style.configure("TLabel", background=self.CARD_BG, font=("Segoe UI", 9), foreground=self.TEXT_MAIN)
        style.configure("Sub.TLabel", background=self.CARD_BG, font=("Segoe UI", 8), foreground=self.TEXT_MUTED)
        style.configure("Header.TLabel", background=self.CARD_BG, font=("Segoe UI", 10, "bold"),
                        foreground=self.TEXT_MAIN)
        style.configure("TCheckbutton", background=self.CARD_BG, font=("Segoe UI", 9))

        # Primary Buttons
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(14, 7),
                        background=self.ACCENT, foreground="#ffffff", borderwidth=0)
        style.map("Primary.TButton",
                  background=[("active", self.ACCENT_HOVER), ("pressed", "#1e40af")])

        # Secondary Buttons
        style.configure("Secondary.TButton", font=("Segoe UI", 9), padding=(10, 4),
                        background="#f1f5f9", foreground=self.TEXT_MAIN, borderwidth=1)
        style.map("Secondary.TButton", background=[("active", "#e2e8f0")])

        # Treeview
        style.configure("Treeview", font=("Segoe UI", 9), rowheight=26, borderwidth=1)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), padding=4)

    def _build_layout(self):
        # 1. Top Card: Depot Selection
        top_card = ttk.Frame(self, style="Card.TFrame", padding=12)
        top_card.pack(fill="x", padx=14, pady=(12, 6))

        depot_lbl_row = ttk.Frame(top_card, style="Card.TFrame")
        depot_lbl_row.pack(fill="x", pady=(0, 4))
        self.depot_lbl_title = ttk.Label(
            depot_lbl_row,
            text="📦 Brickmerge Depot Export (Bestand für FIFO-Verrechnung)",
            style="Header.TLabel"
        )
        self.depot_lbl_title.pack(side="left")

        depot_input_row = ttk.Frame(top_card, style="Card.TFrame")
        depot_input_row.pack(fill="x")
        self.depot_entry = ttk.Entry(depot_input_row, textvariable=self.depot_file_var, font=("Segoe UI", 9))
        self.depot_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.depot_btn_browse = ttk.Button(
            depot_input_row, text="Durchsuchen...", style="Secondary.TButton", command=self._browse_depot
        )
        self.depot_btn_browse.pack(side="left")

        # Checkbox for calculating EK only
        depot_opt_row = ttk.Frame(top_card, style="Card.TFrame")
        depot_opt_row.pack(fill="x", pady=(6, 0))
        self.chk_ek_only = ttk.Checkbutton(
            depot_opt_row,
            text="Nur EK berechnen (Depot-CSV-Datei unverändert lassen)",
            variable=self.ek_only_var
        )
        self.chk_ek_only.pack(side="left")
        ToolTip(
            self.chk_ek_only,
            "Für nachträglich erfasste Alt-Verkäufe: Ermittelt den durchschnittlichen Einkaufspreis "
            "anhand der im Depot vorhandenen Artikel und zieht keine Mengen vom aktuellen Lagerbestand ab."
        )

        # 2. Tabs
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=False, padx=14, pady=6)

        self._tab_amazon_purchases()
        self._tab_amazon_sales()
        self._tab_ebay_sales()
        self._tab_bricklink_sales()
        self._tab_settings()

        # Listener zum Ausgrauen des Depot-Bereichs bei Amazon Business Einkauf
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # 3. Bottom Console
        log_card = ttk.Frame(self, style="Card.TFrame", padding=10)
        log_card.pack(fill="both", expand=True, padx=14, pady=(6, 12))

        log_header = ttk.Frame(log_card, style="Card.TFrame")
        log_header.pack(fill="x", pady=(0, 4))
        ttk.Label(log_header, text="📋 Ausführungs-Protokoll", style="Header.TLabel").pack(side="left")

        btn_box = ttk.Frame(log_header, style="Card.TFrame")
        btn_box.pack(side="right")
        ttk.Button(btn_box, text="Rollback Sales ID Tracking", style="Secondary.TButton",
                   command=lambda: self._rollback(True)).pack(side="left", padx=4)
        ttk.Button(btn_box, text="Rollback Purchases ID Tracking", style="Secondary.TButton",
                   command=lambda: self._rollback(False)).pack(side="left", padx=4)
        ttk.Button(btn_box, text="Log leeren", style="Secondary.TButton", command=self._clear_log).pack(
            side="left", padx=(4, 0)
        )

        # Terminal text box
        self.log_text = tk.Text(
            log_card, wrap="word", height=18, state="disabled",
            bg="#0f172a", fg="#f8fafc", insertbackground="#ffffff",
            font=("Consolas", 9), relief="flat", padx=8, pady=8
        )
        scrollbar = ttk.Scrollbar(log_card, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _on_tab_changed(self, event=None):
        """Disables depot controls only when the Amazon Purchases tab is active."""
        selected_tab_text = self.notebook.tab(self.notebook.select(), "text")

        # Nur beim Einkauf ausgrauen, in allen Verkaufs-Tabs und den Einstellungen aktiv lassen
        is_purchase_tab = "Einkauf" in selected_tab_text
        state = "disabled" if is_purchase_tab else "normal"

        self.depot_entry.configure(state=state)
        self.depot_btn_browse.configure(state=state)
        self.chk_ek_only.configure(state=state)

    # ---------------- TAB DEFINITIONS ----------------

    def _tab_amazon_purchases(self):
        tab = ttk.Frame(self.notebook, style="Card.TFrame", padding=16)
        self.notebook.add(tab, text="Amazon Business Einkaufe")

        file_var = self._bind_setting("amz_b2b_file", "")
        rec_var = self._bind_setting_bool("amz_use_received", True)

        self._render_file_input(tab, "Amazon Business Orders Report (CSV):", file_var, [("CSV Files", "*.csv")])

        # Language banner note
        info_box = ttk.Frame(tab, style="Card.TFrame")
        info_box.pack(fill="x", pady=(4, 6))
        lbl_hint = ttk.Label(
            info_box,
            text="⚠️ Wichtig: Amazon-Konto vor dem Generieren des Berichts auf Englisch stellen!",
            foreground="#b45309",  # Warm Amber
            font=("Segoe UI", 9, "bold")
        )
        lbl_hint.pack(anchor="w")
        ToolTip(
            lbl_hint,
            "Amazon exportiert Spaltennamen in der jeweils im Account aktiven Oberflächensprache "
            "(z. B. 'Bestellnummer' statt 'Order ID'). Der Importer erwartet die englischen Original-Header."
        )

        row_opt = ttk.Frame(tab, style="Card.TFrame")
        row_opt.pack(fill="x", pady=(8, 12))
        chk = ttk.Checkbutton(row_opt, text="Erhaltene Stückzahlen abgleichen (Falls Receiving aktiviert bzw. Spalte im Orders Report vorhanden)",
                              variable=rec_var)
        chk.pack(side="left")
        ToolTip(chk, "Nur bereits erhaltene Artikel und Mengen werden importiert.")

        def execute():
            path = file_var.get().strip()
            if not self._check_file(path):
                return
            imp = AmazonPurchasesImporter(self.db, use_received=rec_var.get())
            self._async_task(lambda: imp.import_purchases(path))

        ttk.Button(tab, text="Amazon Business Käufe importieren", style="Primary.TButton", command=execute).pack(anchor="e")

    def _tab_amazon_sales(self):
        tab = ttk.Frame(self.notebook, style="Card.TFrame", padding=16)
        self.notebook.add(tab, text="Amazon Verkäufe")

        file_var = self._bind_setting("amz_sales_file", "")
        agg_var = self._bind_setting_bool("amz_agg_sales", True)

        self._render_file_input(tab, "Amazon Bestellberichte (TXT / TSV):", file_var,
                                [("Text Files", "*.txt"), ("TSV Files", "*.tsv"), ("All", "*.*")], multiple=True)

        row_opt = ttk.Frame(tab, style="Card.TFrame")
        row_opt.pack(fill="x", pady=(8, 12))
        self._add_aggregation_checkbox(row_opt, agg_var)

        def execute():
            raw_path = file_var.get().strip()
            if not raw_path:
                messagebox.showwarning("Datei fehlt", "Bitte wähle mindestens eine Eingabedatei aus.")
                return

            paths = [p.strip() for p in raw_path.split(";") if p.strip()]

            base_s = float(self.db.get_setting("shipping_base", "5.0"))
            pct_s = float(self.db.get_setting("shipping_percent", "3.0"))
            imp = AmazonSalesImporter(
                self.db, aggregate_sales=agg_var.get(),
                depot_export_file=self.depot_file_var.get().strip() or None,
                shipping_cost_base=base_s, shipping_cost_percentage=pct_s,
                ek_calculation_only=self.ek_only_var.get()
            )
            self._async_task(lambda: imp.import_sales(paths))

        ttk.Button(tab, text="Amazon Verkäufe importieren", style="Primary.TButton", command=execute).pack(anchor="e")

    def _tab_ebay_sales(self):
        tab = ttk.Frame(self.notebook, style="Card.TFrame", padding=16)
        self.notebook.add(tab, text="eBay Verkäufe")

        file_var = self._bind_setting("ebay_sales_file", "")
        agg_var = self._bind_setting_bool("ebay_agg_sales", True)
        treat_ship_var = self._bind_setting_bool("ebay_treat_shipping_revenue", False)

        self._render_file_input(tab, "eBay Orders Report (CSV):", file_var, [("CSV Files", "*.csv")])

        row_opt = ttk.Frame(tab, style="Card.TFrame")
        row_opt.pack(fill="x", pady=(6, 4))
        self._add_aggregation_checkbox(row_opt, agg_var)

        row_opt2 = ttk.Frame(tab, style="Card.TFrame")
        row_opt2.pack(fill="x", pady=(4, 12))
        chk_ship = ttk.Checkbutton(row_opt2, text="Versandkosten immer schätzen",
                                   variable=treat_ship_var)
        chk_ship.pack(side="left")
        ToolTip(chk_ship,
                "Nimmt, auch wenn Versandkosten für eine Bestellung angegeben sind, die geschätzten Versandkosten für die Verkaufskosten.")

        def execute():
            path = file_var.get().strip()
            if not self._check_file(path):
                return
            base_s = float(self.db.get_setting("shipping_base", "5.0"))
            pct_s = float(self.db.get_setting("shipping_percent", "3.0"))
            fee_pct = float(self.db.get_setting("ebay_fee_percent", "12.0"))
            ad_pct = float(self.db.get_setting("ebay_ad_percent", "2.0"))

            imp = EbaySalesImporter(
                self.db, aggregate_sales=agg_var.get(),
                depot_export_file=self.depot_file_var.get().strip() or None,
                ebay_fee_percent=fee_pct, default_ad_percent=ad_pct,
                shipping_cost_base=base_s, shipping_cost_percentage=pct_s,
                always_estimate_real_shipping_cost=treat_ship_var.get(),
                ek_calculation_only=self.ek_only_var.get()
            )
            self._async_task(lambda: imp.import_sales(path))

        ttk.Button(tab, text="eBay Verkäufe importieren", style="Primary.TButton", command=execute).pack(anchor="e")

    def _tab_bricklink_sales(self):
        tab = ttk.Frame(self.notebook, style="Card.TFrame", padding=16)
        self.notebook.add(tab, text="BrickLink Verkäufe")

        file_var = self._bind_setting("bl_sales_file", "")
        agg_var = self._bind_setting_bool("bl_agg_sales", True)

        self._render_file_input(tab, "BrickLink Orders Received Download (CSV):", file_var, [("CSV Files", "*.csv")])

        # Detail items banner note
        info_box = ttk.Frame(tab, style="Card.TFrame")
        info_box.pack(fill="x", pady=(4, 6))
        lbl_hint = ttk.Label(
            info_box,
            text="⚠️ Wichtig: Beim BrickLink-Download 'Include detail items' aktivieren!",
            foreground="#b45309",
            font=("Segoe UI", 9, "bold")
        )
        lbl_hint.pack(anchor="w")
        ToolTip(
            lbl_hint,
            "Wird der Haken bei 'Include detail items' beim Exportieren auf BrickLink nicht gesetzt, "
            "fehlen alle Artikelzeilen. Der Import kann dann keine Verkäufe erfassen."
        )

        row_opt = ttk.Frame(tab, style="Card.TFrame")
        row_opt.pack(fill="x", pady=(4, 12))
        self._add_aggregation_checkbox(row_opt, agg_var)

        def execute():
            path = file_var.get().strip()
            if not self._check_file(path):
                return
            bl_fee = float(self.db.get_setting("bl_fee_percent", "3.0"))
            pp_fee = float(self.db.get_setting("bl_paypal_fee_percent", "2.0"))
            str_fee = float(self.db.get_setting("bl_stripe_fee_percent", "2.0"))

            imp = BricklinkSalesImporter(
                self.db,
                aggregate_sales=agg_var.get(),
                depot_export_file=self.depot_file_var.get().strip() or None,
                bricklink_fee_percent=bl_fee,
                paypal_fee_percent=pp_fee,
                stripe_fee_percent=str_fee,
                ek_calculation_only=self.ek_only_var.get()
            )
            self._async_task(lambda: imp.import_sales(path))

        ttk.Button(tab, text="BrickLink Verkäufe importieren", style="Primary.TButton", command=execute).pack(anchor="e")

    def _tab_settings(self):
        """Constructs the settings tab for fee rates, shipping estimates, and identifier mappings."""
        tab = ttk.Frame(self.notebook, style="Card.TFrame", padding=16)
        self.notebook.add(tab, text="⚙️ Einstellungen & Mappings")

        # Top half: Shipping & Fees configuration
        config_frame = ttk.Frame(tab, style="Card.TFrame")
        config_frame.pack(fill="x", pady=(0, 12))

        ttk.Label(
            config_frame,
            text="Standard-Gebühren & Versandkostenschätzung",
            style="Header.TLabel"
        ).pack(anchor="w", pady=(0, 6))

        grid_box = ttk.Frame(config_frame, style="Card.TFrame")
        grid_box.pack(fill="x")

        # Configuration Items
        field_entries = {}
        specs = [
            ("shipping_base", "Versand pauschal (brutto €):", "5.0",
             "Für Schätzung Versandkosten (Amazon & eBay): Wird bei eBay nur berücksichtigt, wenn kostenloser Versand vorliegt (bzw. die Zusatzoption aktiv ist)."),
            ("shipping_percent", "Versand variabel (brutto %):", "3.0",
             "Für Schätzung Versandkosten (Amazon & eBay): Wird bei eBay nur berücksichtigt, wenn kostenloser Versand vorliegt (bzw. die Zusatzoption aktiv ist)."),
            ("ebay_fee_percent", "eBay Verkaufsgebühr (netto %):", "12.0", "Reguläre Verkaufsgebühr bei eBay."),
            ("ebay_ad_percent", "eBay Anzeigen (netto %):", "2.0", "Zuschlag für eBay-Anzeigenverkäufe."),
            ("bl_fee_percent", "BrickLink Gebühr (brutto %):", "3.0", "Reguläre Verkaufsgebühr auf BrickLink."),
            ("bl_paypal_fee_percent", "PayPal Kosten (brutto %):", "2.0",
             "Nur den Anteil eingeben, der NICHT bereits durch die BrickLink Handling Fee abgedeckt ist."),
            ("bl_stripe_fee_percent", "Stripe Kosten (brutto %):", "2.0",
             "Nur den Anteil eingeben, der NICHT bereits durch die BrickLink Handling Fee abgedeckt ist."),
        ]

        for i, (key, label_text, default_val, tip_text) in enumerate(specs):
            col = (i % 3) * 2
            row = i // 3

            lbl = ttk.Label(grid_box, text=label_text)
            lbl.grid(row=row, column=col, sticky="w", padx=(12 if col > 0 else 0, 4), pady=4)
            ToolTip(lbl, tip_text)

            val = self.db.get_setting(key, default_val)
            v = tk.StringVar(value=val)
            ent = ttk.Entry(grid_box, textvariable=v, width=9)
            ent.grid(row=row, column=col + 1, sticky="w", pady=4)
            ToolTip(ent, tip_text)
            field_entries[key] = v

        def save_conf():
            """Persists updated fee and shipping estimates to the database."""
            for k, var in field_entries.items():
                self.db.set_setting(k, var.get().strip().replace(',', '.'))
            messagebox.showinfo("Gespeichert", "Versand- und Gebührensätze wurden in der Datenbank gespeichert.")

        # Button-Leiste: SKU-Muster links/mittig, Speichern rechts
        btn_bar = ttk.Frame(config_frame, style="Card.TFrame")
        btn_bar.pack(fill="x", pady=(8, 0))

        btn_sku = ttk.Button(
            btn_bar,
            text="⚙️ SKU-Erkennungsmuster (Amazon / eBay)...",
            style="Secondary.TButton",
            command=lambda: SkuSettingsDialog(self, self.db)
        )
        btn_sku.pack(side="left")
        ToolTip(
            btn_sku,
            "Konfiguriere und teste das Template zur automatischen\n"
            "Erkennung von Setnummern aus SKUs (z. B. {SET}[-{WERT,1,4}])."
        )

        ttk.Button(
            btn_bar,
            text="Gebühren & Versand speichern",
            style="Secondary.TButton",
            command=save_conf
        ).pack(side="right")

        # Bottom half: Mappings Treeview
        map_card = ttk.Frame(tab, style="Card.TFrame")
        map_card.pack(fill="both", expand=True, pady=(6, 0))

        ttk.Label(
            map_card,
            text="Manuelle Set-Zuordnungen: ASIN / eBay Artikel-Nr. oder SKU / BrickLink Item-Nr. ➔ brickmerge",
            style="Header.TLabel"
        ).pack(anchor="w", pady=(0, 6))

        tree_split = ttk.Frame(map_card, style="Card.TFrame")
        tree_split.pack(fill="both", expand=True)

        cols = ("platform", "identifier", "set_number", "note")
        tree = ttk.Treeview(tree_split, columns=cols, show="headings", height=6)
        tree.heading("platform", text="Plattform")
        tree.heading("identifier", text="Identifier (ASIN, eBay Nr., etc.)")
        tree.heading("set_number", text="Brickmerge Set-Nummer")
        tree.heading("note", text="Bezeichnung / Notiz")

        tree.column("platform", width=80, anchor="center")
        tree.column("identifier", width=190)
        tree.column("set_number", width=160)
        tree.column("note", width=200)

        tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tree_split, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side="left", fill="y")

        def reload_tree():
            """Refreshes the mapping table from SQLite."""
            for itm in tree.get_children():
                tree.delete(itm)
            for m in self.db.get_all_mappings():
                tree.insert("", "end", values=m)

        reload_tree()

        # Side controls for adding/editing mappings
        side = ttk.Frame(tree_split, style="Card.TFrame", padding=(12, 0, 0, 0))
        side.pack(side="right", fill="y")

        p_var = tk.StringVar(value="BrickLink")
        i_var = tk.StringVar()
        s_var = tk.StringVar()
        n_var = tk.StringVar()

        ttk.Label(side, text="Plattform:").pack(anchor="w")
        combo_p = ttk.Combobox(
            side,
            textvariable=p_var,
            values=["BrickLink", "eBay", "Amazon"],
            state="readonly",
            width=17
        )
        combo_p.pack(anchor="w", pady=(0, 4))

        ttk.Label(side, text="Identifier:").pack(anchor="w")
        ttk.Entry(side, textvariable=i_var, width=19).pack(anchor="w", pady=(0, 2))

        # Dynamic contextual hint for platform identifier conventions
        lbl_hint = ttk.Label(side, text="", font=("Segoe UI", 7), wraplength=145)
        lbl_hint.pack(anchor="w", pady=(0, 4))

        def update_hint(*_):
            """Updates the identifier hint based on the selected platform."""
            plat = p_var.get()
            if plat == "BrickLink":
                lbl_hint.config(
                    text="Hinweis: Sets immer inkl. Suffix angeben (z. B. 6533318-1)",
                    foreground="#d97706"
                )
            elif plat == "eBay":
                lbl_hint.config(
                    text="eBay Artikelnummer (12-stellig) oder Bestandseinheit (SKU)",
                    foreground="#64748b"
                )
            else:
                lbl_hint.config(
                    text="ASIN (10-stellig, z. B. B00...)",
                    foreground="#64748b"
                )

        combo_p.bind("<<ComboboxSelected>>", update_hint)
        update_hint()

        ttk.Label(side, text="Set-Nr (z. B. 71048-36):").pack(anchor="w")
        ttk.Entry(side, textvariable=s_var, width=19).pack(anchor="w", pady=(0, 4))
        ttk.Label(side, text="Notiz:").pack(anchor="w")
        ttk.Entry(side, textvariable=n_var, width=19).pack(anchor="w", pady=(0, 8))

        def add_item():
            """Validates inputs and saves or updates the custom mapping."""
            p, _i, s, n = p_var.get().strip(), i_var.get().strip(), s_var.get().strip(), n_var.get().strip()
            if not _i or not s:
                messagebox.showwarning("Fehlende Werte", "Identifier und Set-Nummer sind Pflichtfelder.")
                return

            # Warn user if they forgot the typical BrickLink set suffix
            if p == "BrickLink" and _i.isdigit():
                if not messagebox.askyesno(
                        "Suffix fehlt möglicherweise",
                        f"'{_i}' enthält kein Suffix wie '-1'.\n\n"
                        "Möchtest du die Zuordnung trotzdem genau so speichern?"
                ):
                    return

            self.db.set_mapping(p, _i, s, n)
            reload_tree()
            i_var.set("")
            s_var.set("")
            n_var.set("")

        def delete_item():
            """Removes the selected mapping row from the database and UI."""
            sel = tree.selection()
            if not sel:
                return
            _val = tree.item(sel[0], "values")
            if messagebox.askyesno("Löschen", f"Zuordnung für '{_val[1]}' entfernen?"):
                self.db.delete_mapping(_val[0], _val[1])
                reload_tree()

        def on_select(e):
            """Populates input fields when a table entry is clicked."""
            sel = tree.selection()
            if sel:
                _v = tree.item(sel[0], "values")
                p_var.set(_v[0])
                i_var.set(_v[1])
                s_var.set(_v[2])
                n_var.set(_v[3])
                update_hint()

        tree.bind("<<TreeviewSelect>>", on_select)

        ttk.Button(side, text="Zuordnung speichern", style="Primary.TButton", command=add_item).pack(fill="x", pady=2)
        ttk.Button(side, text="Ausgewählte löschen", style="Secondary.TButton", command=delete_item).pack(fill="x", pady=2)

    # ---------------- HELPERS ----------------

    def _add_aggregation_checkbox(self, parent, variable: tk.BooleanVar) -> ttk.Checkbutton:
        """Creates a standardized aggregation checkbox with explanation tooltip."""
        chk = ttk.Checkbutton(
            parent,
            text="Verkäufe monatlich bündeln",
            variable=variable
        )
        chk.pack(side="left")
        ToolTip(
            chk,
            "Gebündelt wird monatlich anhand des Nettoerlöses (Verkaufspreis abzgl. Gebühren & Versand) in 1€-Schritten."
        )
        return chk

    def _render_file_input(self, parent, title: str, var: tk.StringVar, ftypes: list, multiple: bool = False,):
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=(0, 4))
        ttk.Label(row, text=title, style="Header.TLabel").pack(anchor="w", pady=(0, 2))

        in_row = ttk.Frame(row, style="Card.TFrame")
        in_row.pack(fill="x")
        ttk.Entry(in_row, textvariable=var, font=("Segoe UI", 9)).pack(side="left", fill="x", expand=True, padx=(0, 6))

        def pick():
            if multiple:
                chosen = filedialog.askopenfilenames(filetypes=ftypes)
                if chosen:
                    var.set(";".join(chosen))
            else:
                chosen = filedialog.askopenfilename(filetypes=ftypes)
                if chosen:
                    var.set(chosen)

        ttk.Button(in_row, text="Auswählen...", style="Secondary.TButton", command=pick).pack(side="left")

    def _browse_depot(self):
        f = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv"), ("All", "*.*")])
        if f:
            self.depot_file_var.set(f)

    def _bind_setting(self, key: str, default: str) -> tk.StringVar:
        val = self.db.get_setting(key, default)
        v = tk.StringVar(value=val)
        v.trace_add("write", lambda *_: self.db.set_setting(key, v.get()))
        return v

    def _bind_setting_bool(self, key: str, default: bool) -> tk.BooleanVar:
        val = self.db.get_setting(key, str(default)) == "True"
        v = tk.BooleanVar(value=val)
        v.trace_add("write", lambda *_: self.db.set_setting(key, str(v.get())))
        return v

    def _check_file(self, path: str) -> bool:
        if not path or not os.path.isfile(path):
            messagebox.showwarning("Datei fehlt", "Bitte wähle eine gültige Eingabedatei aus.")
            return False
        return True

    def _async_task(self, task):
        def runner():
            try:
                task()
            except Exception as e:
                print(f"Fehler: {e}")
                messagebox.showerror("Verarbeitungsfehler", f"Ein Fehler ist aufgetreten:\n{e}")

        threading.Thread(target=runner, daemon=True).start()

    def _rollback(self, is_sales: bool):
        label = "Sales (Verkäufe)" if is_sales else "Purchases (Einkäufe)"
        message = f"Letzten Batch für {label} aus der lokalen Datenbank (Tracking-Historie) entfernen?"
        if is_sales:
            message += "\n\nACHTUNG: Die Depot-CSV-Datei wird nicht zurückgesetzt."
        if messagebox.askyesno("Ja", message):
            self.db.delete_last_processed(is_sales=is_sales)

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")


class SkuSettingsDialog(tk.Toplevel):
    """Modaler Konfigurations- und Testdialog für das SKU-Erkennungsmuster."""

    def __init__(self, parent: tk.Tk | tk.Toplevel, db) -> None:
        super().__init__(parent)
        self.db = db
        self.parent = parent
        self.title("SKU-Erkennung konfigurieren")
        self.resizable(False, False)
        self.transient(parent)

        # Sichere Presets ohne fehleranfällige Wildcard '*'
        self.presets: dict[str, str] = {
            "Standard: Optionales Suffix ({SET}[-{WERT,1,4}])": (
                "{SET}[-{WERT,1,4}]"
            ),
            "Nur reine Ziffern ({SET}[-{ZAHL,1,2}])": "{SET}[-{ZAHL,1,2}]",
            "Nur reiner Text ({SET}[-{TEXT,1,4}])": "{SET}[-{TEXT,1,4}]",
            "3-Teilig ({SET}[-{ZAHL,1,1}][-{WERT,1,4}])": (
                "{SET}[-{ZAHL,1,1}][-{WERT,1,4}]"
            ),
            "Exakt nur Setnummer ({SET})": "{SET}",
            "Benutzerdefiniert": "",
        }

        self._updating_from_preset = False
        self._build_ui()
        self._center_window()
        self.grab_set()

        self.bind("<Escape>", lambda _: self.destroy())
        self.bind("<Return>", lambda _: self._save_and_close())

    def _build_ui(self) -> None:
        main_frame = ttk.Frame(self, style="Card.TFrame", padding=20)
        main_frame.pack(fill="both", expand=True)

        current_val = self.db.get_setting("sku_template", "{SET}[-{WERT,1,4}]")
        self.pattern_var = tk.StringVar(value=current_val)
        self.preset_var = tk.StringVar(value="Benutzerdefiniert")

        for name, tmpl in self.presets.items():
            if tmpl == current_val:
                self.preset_var.set(name)
                break

        # Header-Bereich
        ttk.Label(
            main_frame,
            text="SKU-Erkennung (Amazon & eBay)",
            style="Header.TLabel",
        ).pack(anchor="w", pady=(0, 2))

        ttk.Label(
            main_frame,
            text="Definiere das Schema, mit dem LEGO-Setnummern aus SKUs extrahiert werden.",
            font=("Segoe UI", 9),
            foreground="#64748b",
        ).pack(anchor="w", pady=(0, 16))

        # 1. Preset Dropdown
        row_preset = ttk.Frame(main_frame, style="Card.TFrame")
        row_preset.pack(fill="x", pady=(0, 8))
        ttk.Label(
            row_preset, text="Vorlage:", width=10, font=("Segoe UI", 9, "bold")
        ).pack(side="left")

        cb = ttk.Combobox(
            row_preset,
            textvariable=self.preset_var,
            values=list(self.presets.keys()),
            state="readonly",
            width=42,
        )
        cb.pack(side="left", fill="x", expand=True)
        cb.bind("<<ComboboxSelected>>", self._on_preset_selected)

        # 2. Template Eingabezeile
        row_tmpl = ttk.Frame(main_frame, style="Card.TFrame")
        row_tmpl.pack(fill="x", pady=(0, 14))
        ttk.Label(
            row_tmpl, text="Muster:", width=10, font=("Segoe UI", 9, "bold")
        ).pack(side="left")

        ent_pattern = ttk.Entry(
            row_tmpl, textvariable=self.pattern_var, width=42
        )
        ent_pattern.pack(side="left", fill="x", expand=True)
        self.pattern_var.trace_add("write", self._on_pattern_edited)

        # 3. Moderne Live-Vorschau Karte (Card-Stil statt LabelFrame)
        preview_card = tk.Frame(
            main_frame,
            bg="#f8fafc",
            highlightbackground="#e2e8f0",
            highlightthickness=1,
            padx=14,
            pady=12,
        )
        preview_card.pack(fill="x", pady=(0, 14))

        # Kopfzeile der Vorschau
        preview_header = tk.Frame(preview_card, bg="#f8fafc")
        preview_header.pack(fill="x", pady=(0, 8))

        tk.Label(
            preview_header,
            text="LIVE-VORSCHAU",
            font=("Segoe UI", 8, "bold"),
            fg="#94a3b8",
            bg="#f8fafc",
        ).pack(side="left")

        # Eingabe & Status-Badge
        preview_body = tk.Frame(preview_card, bg="#f8fafc")
        preview_body.pack(fill="x")

        tk.Label(
            preview_body,
            text="Test-SKU:",
            font=("Segoe UI", 9),
            fg="#475569",
            bg="#f8fafc",
        ).pack(side="left", padx=(0, 8))

        self.test_sku_var = tk.StringVar(value="75192-NEW")
        ent_test = ttk.Entry(preview_body, textvariable=self.test_sku_var, width=18)
        ent_test.pack(side="left", padx=(0, 12))
        self.test_sku_var.trace_add("write", lambda *_: self._run_test())

        # Status-Badge (Pille mit Hintergrundfarbe)
        self.lbl_badge = tk.Label(
            preview_body,
            text="",
            font=("Segoe UI", 9, "bold"),
            padx=8,
            pady=2,
            bd=0,
        )
        self.lbl_badge.pack(side="left")

        # 4. Syntax-Hinweise (Sauber strukturiert ohne Wildcard-Verleitung)
        help_card = ttk.Frame(main_frame, style="Card.TFrame")
        help_card.pack(fill="x", pady=(0, 18))

        help_tokens = (
            "• {SET} : 4–7 Ziffern (LEGO Set-Nummer)\n"
            "• {WERT,min,max} : Alphanumerisch (z. B. 'NEW', '1', 'OVP')\n"
            "• {ZAHL,min,max} : Nur Ziffern | {TEXT,min,max} : Nur Buchstaben\n"
            "• [...] : Optionaler Bereich (z. B. '[-{WERT,1,4}]')"
        )
        ttk.Label(
            help_card,
            text=help_tokens,
            font=("Segoe UI", 8),
            foreground="#64748b",
            justify="left",
        ).pack(anchor="w")

        # 5. Buttons
        btn_box = ttk.Frame(main_frame, style="Card.TFrame")
        btn_box.pack(fill="x")

        ttk.Button(
            btn_box,
            text="Abbrechen",
            style="Secondary.TButton",
            command=self.destroy,
        ).pack(side="right", padx=(8, 0))

        ttk.Button(
            btn_box,
            text="Speichern",
            style="Primary.TButton",
            command=self._save_and_close,
        ).pack(side="right")

        self._run_test()

    def _center_window(self) -> None:
        self.update_idletasks()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()

        parent_x = self.parent.winfo_rootx()
        parent_y = self.parent.winfo_rooty()
        parent_w = self.parent.winfo_width()
        parent_h = self.parent.winfo_height()

        x = parent_x + max(0, (parent_w - w) // 2)
        y = parent_y + max(0, (parent_h - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _on_preset_selected(self, _event=None) -> None:
        selected_template = self.presets.get(self.preset_var.get())
        if selected_template:
            self._updating_from_preset = True
            self.pattern_var.set(selected_template)
            self._updating_from_preset = False
        self._run_test()

    def _on_pattern_edited(self, *_) -> None:
        if not self._updating_from_preset:
            cur = self.pattern_var.get().strip()
            match_found = False
            for name, tmpl in self.presets.items():
                if tmpl == cur and name != "Benutzerdefiniert":
                    self.preset_var.set(name)
                    match_found = True
                    break
            if not match_found:
                self.preset_var.set("Benutzerdefiniert")

        self._run_test()

    def _run_test(self) -> None:
        tmpl = self.pattern_var.get().strip()
        sample = self.test_sku_var.get().strip()

        if not tmpl or not sample:
            self.lbl_badge.config(text="", bg="#f8fafc")
            return

        try:
            parser = SetNumberParser(tmpl)
            found = parser.get_set_number_from_sku(sample)
            if found:
                self.lbl_badge.config(
                    text=f"✓ Erkannt: {found}",
                    fg="#15803d",
                    bg="#dcfce7",  # Subtiles Tailwind-Grün
                )
            else:
                self.lbl_badge.config(
                    text="✗ Kein Treffer",
                    fg="#b91c1c",
                    bg="#fee2e2",  # Subtiles Tailwind-Rot
                )
        except (re.error, ValueError):
            self.lbl_badge.config(
                text="! Ungültiges Muster",
                fg="#b45309",
                bg="#fef3c7",  # Subtiles Tailwind-Bernstein
            )

    def _save_and_close(self) -> None:
        tmpl = self.pattern_var.get().strip()
        if not tmpl:
            messagebox.showwarning(
                "Leeres Muster",
                "Das SKU-Muster darf nicht leer sein.",
                parent=self,
            )
            return

        try:
            SetNumberParser.compile_sku_template(tmpl)
            self.db.set_setting("sku_template", tmpl)
            self.destroy()
        except Exception as e:
            messagebox.showerror(
                "Ungültiges Muster",
                f"Das angegebene Muster konnte nicht kompiliert werden:\n{e}",
                parent=self,
            )


if __name__ == "__main__":
    app = ModernImportGUI()
    app.mainloop()
