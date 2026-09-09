from __future__ import annotations

import argparse
import queue
import threading
import tkinter as tk
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import pystray
import yaml
from matplotlib import rcParams
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from PIL import Image, ImageDraw

from .collector import BackgroundCollector, Snapshot
from .config import ConfigError, Settings, load_settings
from .kis_client import KisClient
from .ledger import Ledger
from .market_data import MarketData
from .secrets import Credentials, SecretStore, SecretStoreError
from .symbol_catalog import Symbol, SymbolCatalog


class TraderApp:
    def __init__(self, root: tk.Tk, settings: Settings, config_path: Path):
        self.root = root
        self.settings = settings
        self.config_path = config_path
        self.ledger = Ledger(settings.storage.database_path)
        self.ledger.initialize()
        self.secret_store = SecretStore()
        self.catalog = SymbolCatalog(
            settings.project_root / ".master" / "symbols.json",
            settings.project_root / "config" / "symbols.yaml",
        )
        self.catalog.load()
        self.collector: BackgroundCollector | None = None
        self.tray_icon: pystray.Icon | None = None
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.exiting = False

        self.root.title("KIS Intraday Trader")
        self.root.geometry("1040x700")
        self.root.minsize(900, 620)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self._configure_style()
        self._build_ui()
        self._load_saved_credentials()
        self._load_recent_snapshots()
        self.root.after(150, self._drain_events)

    def _configure_style(self) -> None:
        rcParams["font.family"] = "Malgun Gothic"
        rcParams["axes.unicode_minus"] = False
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", rowheight=28)

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, padding=18)
        shell.pack(fill="both", expand=True)

        header = ttk.Frame(shell)
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(header, text="KIS Intraday Trader", style="Title.TLabel").pack(side="left")
        self.status_var = tk.StringVar(value="연결 안 됨")
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").pack(side="right")

        notebook = ttk.Notebook(shell)
        notebook.pack(fill="both", expand=True)
        dashboard = ttk.Frame(notebook, padding=14)
        symbols = ttk.Frame(notebook, padding=14)
        connection = ttk.Frame(notebook, padding=14)
        notebook.add(dashboard, text="대시보드")
        notebook.add(symbols, text="종목 선택")
        notebook.add(connection, text="API 연결")
        self._build_dashboard(dashboard)
        self._build_symbol_selector(symbols)
        self._build_connection(connection)

    def _build_dashboard(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent)
        controls.pack(fill="x", pady=(0, 12))
        self.collection_summary_var = tk.StringVar()
        ttk.Label(controls, textvariable=self.collection_summary_var, style="Section.TLabel").pack(
            side="left"
        )
        ttk.Label(controls, text="주기(초)").pack(side="left", padx=(10, 4))
        self.interval_var = tk.IntVar(value=self.settings.collection.interval_seconds)
        ttk.Spinbox(controls, from_=5, to=3600, textvariable=self.interval_var, width=7).pack(
            side="left"
        )
        self.start_button = ttk.Button(controls, text="수집 시작", command=self.start_collection)
        self.start_button.pack(side="right")
        self.stop_button = ttk.Button(
            controls, text="수집 중지", command=self.stop_collection, state="disabled"
        )
        self.stop_button.pack(side="right", padx=8)

        workspace = ttk.Panedwindow(parent, orient="horizontal")
        workspace.pack(fill="both", expand=True)
        watch_panel = ttk.Frame(workspace, padding=(0, 0, 10, 0))
        chart_panel = ttk.Frame(workspace, padding=(10, 0, 0, 0))
        workspace.add(watch_panel, weight=2)
        workspace.add(chart_panel, weight=5)

        ttk.Label(watch_panel, text="관심종목", style="Section.TLabel").pack(
            anchor="w", pady=(0, 6)
        )
        columns = ("name", "code", "price", "change", "volume")
        self.watch_tree = ttk.Treeview(watch_panel, columns=columns, show="headings", height=16)
        for key, label, width, anchor in (
            ("name", "종목명", 135, "w"),
            ("code", "코드", 72, "center"),
            ("price", "현재가", 88, "e"),
            ("change", "등락률", 68, "e"),
            ("volume", "거래량", 95, "e"),
        ):
            self.watch_tree.heading(key, text=label)
            self.watch_tree.column(key, width=width, minwidth=55, anchor=anchor)
        self.watch_tree.tag_configure("rise", foreground="#c62828")
        self.watch_tree.tag_configure("fall", foreground="#1565c0")
        self.watch_tree.pack(fill="both", expand=True)
        self.watch_tree.bind("<<TreeviewSelect>>", self._on_watch_selected)

        quote_header = ttk.Frame(chart_panel)
        quote_header.pack(fill="x", pady=(0, 6))
        self.quote_name_var = tk.StringVar(value="종목을 선택하세요")
        self.quote_price_var = tk.StringVar(value="-")
        self.quote_change_var = tk.StringVar(value="-")
        ttk.Label(quote_header, textvariable=self.quote_name_var, style="Section.TLabel").pack(
            side="left"
        )
        ttk.Label(quote_header, textvariable=self.quote_price_var, font=("Segoe UI", 16, "bold")).pack(
            side="left", padx=(18, 8)
        )
        self.quote_change_label = ttk.Label(quote_header, textvariable=self.quote_change_var)
        self.quote_change_label.pack(side="left")
        ranges = ttk.Frame(quote_header)
        ranges.pack(side="right")
        self.chart_days = 90
        for label, days in (("1개월", 31), ("3개월", 93), ("1년", 366)):
            ttk.Button(ranges, text=label, width=7, command=lambda value=days: self._set_chart_range(value)).pack(
                side="left", padx=(4, 0)
            )

        self.figure = Figure(figsize=(7, 4.6), dpi=100, facecolor="#ffffff")
        grid = self.figure.add_gridspec(4, 1, hspace=0.08)
        self.price_axis = self.figure.add_subplot(grid[:3, 0])
        self.volume_axis = self.figure.add_subplot(grid[3, 0], sharex=self.price_axis)
        self.chart_canvas = FigureCanvasTkAgg(self.figure, master=chart_panel)
        self.chart_canvas.get_tk_widget().pack(fill="both", expand=True)
        self._draw_empty_chart("관심종목을 선택하면 일봉 차트를 조회합니다")

        ttk.Label(parent, text="운영 로그", style="Section.TLabel").pack(
            fill="x", pady=(14, 6)
        )
        self.log = tk.Text(parent, height=5, state="disabled", font=("Consolas", 9), wrap="word")
        self.log.pack(fill="x")

    def _draw_empty_chart(self, message: str) -> None:
        self.price_axis.clear()
        self.volume_axis.clear()
        self.price_axis.text(
            0.5, 0.5, message, transform=self.price_axis.transAxes, ha="center", va="center", color="#666666"
        )
        self.price_axis.set_xticks([])
        self.price_axis.set_yticks([])
        self.volume_axis.set_visible(False)
        self.chart_canvas.draw_idle()

    def _set_chart_range(self, days: int) -> None:
        self.chart_days = days
        selected = self.watch_tree.selection()
        if selected:
            self._request_quote_history(selected[0])

    def _on_watch_selected(self, _event: object = None) -> None:
        selected = self.watch_tree.selection()
        if selected:
            self._request_quote_history(selected[0])

    def _request_quote_history(self, symbol: str) -> None:
        values = self.watch_tree.item(symbol, "values")
        name = str(values[0]) if values else symbol
        self.quote_name_var.set(f"{name}  {symbol}")
        self._draw_empty_chart("일봉 데이터를 불러오는 중입니다")

        def work() -> None:
            try:
                self.secret_store.apply_to_environment(self.settings)
                end = date.today()
                start = end - timedelta(days=self.chart_days)
                with KisClient(self.settings) as client:
                    market = MarketData(client)
                    quote = market.current_price(symbol)
                    history = market.daily_prices(symbol, start, end)
                self.events.put(("history", (symbol, name, quote, history)))
            except Exception as exc:
                self.events.put(("history_error", (symbol, str(exc))))

        threading.Thread(target=work, name=f"kis-chart-{symbol}", daemon=True).start()

    def _render_history(self, symbol: str, name: str, quote: dict, rows: list[dict]) -> None:
        selected = self.watch_tree.selection()
        if selected and selected[0] != symbol:
            return
        parsed = []
        for row in rows:
            try:
                parsed.append(
                    (
                        datetime.strptime(row["stck_bsop_date"], "%Y%m%d"),
                        int(row["stck_clpr"]),
                        int(row.get("acml_vol", 0)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        parsed.sort(key=lambda item: item[0])
        price = str(quote.get("stck_prpr", ""))
        change = str(quote.get("prdy_ctrt", ""))
        self.quote_name_var.set(f"{name}  {symbol}")
        self.quote_price_var.set(f"{int(price):,}원" if price.isdigit() else price or "-")
        self.quote_change_var.set(f"{change}%")
        if not parsed:
            self._draw_empty_chart("표시할 일봉 데이터가 없습니다")
            return
        dates = [item[0] for item in parsed]
        closes = [item[1] for item in parsed]
        volumes = [item[2] for item in parsed]
        ma5 = [sum(closes[max(0, index - 4) : index + 1]) / min(5, index + 1) for index in range(len(closes))]
        ma20 = [sum(closes[max(0, index - 19) : index + 1]) / min(20, index + 1) for index in range(len(closes))]
        self.price_axis.clear()
        self.volume_axis.clear()
        self.volume_axis.set_visible(True)
        self.price_axis.plot(dates, closes, color="#202124", linewidth=1.5, label="종가")
        self.price_axis.plot(dates, ma5, color="#d32f2f", linewidth=1.0, label="MA5")
        self.price_axis.plot(dates, ma20, color="#1976d2", linewidth=1.0, label="MA20")
        self.price_axis.grid(True, color="#e5e7eb", linewidth=0.7)
        self.price_axis.legend(loc="upper left", frameon=False, ncol=3, fontsize=8)
        self.price_axis.tick_params(axis="x", labelbottom=False)
        self.price_axis.ticklabel_format(axis="y", style="plain", useOffset=False)
        self.price_axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value:,.0f}"))
        bar_colors = ["#c62828" if index == 0 or closes[index] >= closes[index - 1] else "#1565c0" for index in range(len(closes))]
        self.volume_axis.bar(dates, volumes, color=bar_colors, width=0.8, alpha=0.65)
        self.volume_axis.grid(True, axis="y", color="#eeeeee", linewidth=0.6)
        self.volume_axis.tick_params(axis="x", labelrotation=0, labelsize=8)
        self.volume_axis.tick_params(axis="y", labelsize=8)
        self.volume_axis.yaxis.set_major_formatter(
            FuncFormatter(lambda value, _pos: f"{value / 1_000_000:.1f}M")
        )
        self.figure.autofmt_xdate(rotation=0)
        self.chart_canvas.draw_idle()

    def _build_symbol_selector(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Label(toolbar, text="종목 검색", style="Section.TLabel").pack(side="left")
        self.symbol_search_var = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=self.symbol_search_var, width=30)
        search.pack(side="left", padx=10)
        search.bind("<KeyRelease>", lambda _event: self._filter_catalog())
        ttk.Button(toolbar, text="종목 마스터 갱신", command=self.refresh_symbol_catalog).pack(
            side="right"
        )

        panes = ttk.Panedwindow(parent, orient="horizontal")
        panes.pack(fill="both", expand=True)
        available = ttk.Frame(panes, padding=(0, 0, 8, 0))
        selected = ttk.Frame(panes, padding=(8, 0, 0, 0))
        panes.add(available, weight=1)
        panes.add(selected, weight=1)
        ttk.Label(available, text="검색 결과").pack(anchor="w", pady=(0, 6))
        ttk.Label(selected, text="수집 대상").pack(anchor="w", pady=(0, 6))
        columns = ("code", "name", "market")
        self.available_tree = ttk.Treeview(available, columns=columns, show="headings", height=15)
        self.selected_tree = ttk.Treeview(selected, columns=columns, show="headings", height=15)
        for tree in (self.available_tree, self.selected_tree):
            for key, label, width in (
                ("code", "종목코드", 90),
                ("name", "종목명", 180),
                ("market", "시장", 75),
            ):
                tree.heading(key, text=label)
                tree.column(key, width=width, anchor="center" if key != "name" else "w")
            tree.pack(fill="both", expand=True)
        self.available_tree.bind("<Double-1>", lambda _event: self.add_selected_symbols())
        self.selected_tree.bind("<Double-1>", lambda _event: self.remove_selected_symbols())

        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="선택 추가 →", command=self.add_selected_symbols).pack(side="left")
        ttk.Button(actions, text="← 선택 제거", command=self.remove_selected_symbols).pack(
            side="left", padx=8
        )
        self.selection_info_var = tk.StringVar()
        ttk.Label(actions, textvariable=self.selection_info_var).pack(side="right")
        self._filter_catalog()
        known = {item.code: item for item in self.catalog.symbols}
        for code in self.settings.collection.symbols:
            symbol = known.get(code, Symbol(code, code, "KRX"))
            self._insert_symbol(self.selected_tree, symbol)
        self._update_collection_summary()

    @staticmethod
    def _insert_symbol(tree: ttk.Treeview, symbol: Symbol) -> None:
        if tree.exists(symbol.code):
            return
        tree.insert("", "end", iid=symbol.code, values=(symbol.code, symbol.name, symbol.market))

    def _filter_catalog(self) -> None:
        if not hasattr(self, "available_tree"):
            return
        for item in self.available_tree.get_children():
            self.available_tree.delete(item)
        for symbol in self.catalog.search(self.symbol_search_var.get(), limit=200):
            self._insert_symbol(self.available_tree, symbol)

    def add_selected_symbols(self) -> None:
        current = set(self.selected_tree.get_children())
        additions = [item for item in self.available_tree.selection() if item not in current]
        if len(current) + len(additions) > self.settings.collection.max_symbols:
            messagebox.showwarning(
                "선택 제한",
                f"수집 종목은 최대 {self.settings.collection.max_symbols}개입니다.",
                parent=self.root,
            )
            return
        for item in additions:
            values = self.available_tree.item(item, "values")
            self.selected_tree.insert("", "end", iid=item, values=values)
        self._update_collection_summary()

    def remove_selected_symbols(self) -> None:
        for item in self.selected_tree.selection():
            self.selected_tree.delete(item)
        self._update_collection_summary()

    def _update_collection_summary(self) -> None:
        count = len(self.selected_tree.get_children())
        per_request = 1.05 if self.settings.market_data_environment == "demo" else 0.25
        seconds = count * per_request
        self.collection_summary_var.set(f"수집 종목 {count}개")
        self.selection_info_var.set(
            f"{count}/{self.settings.collection.max_symbols}개 · 1회 예상 {seconds:.1f}초"
        )
        self._sync_watchlist()

    def _sync_watchlist(self) -> None:
        if not hasattr(self, "watch_tree") or not hasattr(self, "selected_tree"):
            return
        selected = set(self.selected_tree.get_children())
        for item in self.watch_tree.get_children():
            if item not in selected:
                self.watch_tree.delete(item)
        for code in self.selected_tree.get_children():
            values = self.selected_tree.item(code, "values")
            name = str(values[1]) if len(values) > 1 else code
            if not self.watch_tree.exists(code):
                self.watch_tree.insert("", "end", iid=code, values=(name, code, "-", "-", "-"))

    def refresh_symbol_catalog(self) -> None:
        self.selection_info_var.set("공식 종목 마스터 갱신 중...")

        def work() -> None:
            try:
                symbols = self.catalog.refresh()
                self.events.put(("catalog", len(symbols)))
            except Exception as exc:
                self.events.put(("catalog_error", str(exc)))

        threading.Thread(target=work, name="kis-symbol-master", daemon=True).start()

    def _build_connection(self, parent: ttk.Frame) -> None:
        form = ttk.Frame(parent)
        form.pack(anchor="nw", fill="x")
        ttk.Label(form, text="KIS API 연결", style="Section.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 14)
        )
        self.app_key_var = tk.StringVar()
        self.app_secret_var = tk.StringVar()
        self.account_var = tk.StringVar()
        self.reveal_credentials_var = tk.BooleanVar(value=False)
        self.environment_var = tk.StringVar(value=self.settings.market_data_environment)
        fields = [
            ("App Key", self.app_key_var, "•"),
            ("App Secret", self.app_secret_var, "•"),
            ("계좌번호 앞 8자리", self.account_var, ""),
        ]
        self.credential_entries: list[ttk.Entry] = []
        for row, (label, variable, mask) in enumerate(fields, start=1):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=7)
            entry = ttk.Entry(form, textvariable=variable, show=mask, width=58)
            entry.grid(
                row=row, column=1, columnspan=2, sticky="ew", padx=(16, 0), pady=7
            )
            if row <= 2:
                self.credential_entries.append(entry)
        ttk.Label(form, text="시세 환경").grid(row=4, column=0, sticky="w", pady=7)
        env = ttk.Frame(form)
        env.grid(row=4, column=1, sticky="w", padx=(16, 0), pady=7)
        ttk.Radiobutton(env, text="실전 시세", variable=self.environment_var, value="real").pack(
            side="left"
        )
        ttk.Radiobutton(env, text="모의 시세", variable=self.environment_var, value="demo").pack(
            side="left", padx=18
        )
        ttk.Checkbutton(
            form,
            text="키 표시",
            variable=self.reveal_credentials_var,
            command=self._toggle_credential_visibility,
        ).grid(row=5, column=0, sticky="w", pady=(12, 0))
        actions = ttk.Frame(form)
        actions.grid(row=5, column=1, columnspan=2, sticky="w", padx=(16, 0), pady=(18, 0))
        ttk.Button(actions, text="안전하게 저장", command=self.save_credentials).pack(side="left")
        ttk.Button(actions, text="연결 테스트", command=self.test_connection).pack(
            side="left", padx=8
        )
        ttk.Label(
            form,
            text="키와 계좌번호는 Windows 자격 증명 저장소에 보관됩니다. 주문 기능은 잠겨 있습니다.",
            foreground="#555555",
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(22, 0))
        form.columnconfigure(1, weight=1)

    def _toggle_credential_visibility(self) -> None:
        mask = "" if self.reveal_credentials_var.get() else "•"
        for entry in self.credential_entries:
            entry.configure(show=mask)

    def _load_saved_credentials(self) -> None:
        try:
            credentials = self.secret_store.load()
        except SecretStoreError as exc:
            self._write_log(str(exc))
            return
        if credentials:
            self.app_key_var.set(credentials.app_key)
            self.app_secret_var.set(credentials.app_secret)
            self.account_var.set(credentials.account_no)
            self.secret_store.apply_to_environment(self.settings)
            self.status_var.set("자격 증명 저장됨")

    def _credentials_from_form(self) -> Credentials:
        return Credentials(
            self.app_key_var.get().strip(),
            self.app_secret_var.get().strip(),
            self.account_var.get().strip(),
        )

    def _apply_environment_selection(self) -> None:
        selected = self.environment_var.get()
        if selected not in {"real", "demo"}:
            raise ConfigError("시세 환경을 선택하세요")
        self.settings = replace(self.settings, market_data_environment=selected)
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        raw["market_data_environment"] = selected
        self.config_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        self._update_collection_summary()

    def save_credentials(self) -> bool:
        try:
            credentials = self._credentials_from_form()
            self.secret_store.save(credentials)
            self._apply_environment_selection()
            self.secret_store.apply_to_environment(self.settings)
        except (ConfigError, SecretStoreError) as exc:
            messagebox.showerror("저장 실패", str(exc), parent=self.root)
            return False
        self.status_var.set("자격 증명 저장됨")
        self._write_log("API 자격 증명을 OS 저장소에 저장했습니다.")
        return True

    def test_connection(self) -> None:
        try:
            if not self.save_credentials():
                return
            symbol = self._parse_symbols()[0]
        except (ConfigError, ValueError):
            return
        self.status_var.set("연결 확인 중")

        def work() -> None:
            try:
                with KisClient(self.settings) as client:
                    output = MarketData(client).current_price(symbol)
                self.events.put(("connected", (symbol, output.get("stck_prpr", ""))))
            except Exception as exc:
                self.events.put(("error", f"연결 실패: {exc}"))

        threading.Thread(target=work, name="kis-connection-test", daemon=True).start()

    def _parse_symbols(self) -> list[str]:
        symbols = list(self.selected_tree.get_children())
        if not symbols:
            raise ValueError("수집 종목을 선택하세요")
        if len(symbols) > self.settings.collection.max_symbols:
            raise ValueError(f"수집 종목은 최대 {self.settings.collection.max_symbols}개입니다")
        if any(not value.isdigit() or len(value) != 6 for value in symbols):
            raise ValueError("종목코드는 6자리 숫자여야 합니다")
        return symbols

    def _save_collection_settings(self, symbols: list[str], interval: int) -> None:
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        collection = raw.setdefault("collection", {})
        collection["symbols"] = symbols
        collection["max_symbols"] = self.settings.collection.max_symbols
        collection["interval_seconds"] = interval
        self.config_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    def start_collection(self) -> None:
        try:
            self.secret_store.apply_to_environment(self.settings)
            symbols = self._parse_symbols()
            interval = int(self.interval_var.get())
            if interval < 5:
                raise ValueError("수집 주기는 5초 이상이어야 합니다")
        except (ConfigError, SecretStoreError, ValueError) as exc:
            messagebox.showerror("수집 시작 실패", str(exc), parent=self.root)
            return
        self._save_collection_settings(symbols, interval)
        self.collector = BackgroundCollector(
            self.settings,
            self.ledger,
            symbols,
            interval,
            on_snapshot=lambda value: self.events.put(("snapshot", value)),
            on_status=lambda value: self.events.put(("status", value)),
        )
        self.collector.start()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("백그라운드 수집 중")

    def stop_collection(self) -> None:
        if self.collector:
            self.collector.stop()
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status_var.set("수집 중지")

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "snapshot":
                self._insert_snapshot(payload)
            elif kind == "connected":
                symbol, price = payload
                self.status_var.set("API 연결됨")
                self._write_log(f"연결 성공: {symbol} 현재가 {price}")
            elif kind == "error":
                self.status_var.set("연결 실패")
                self._write_log(str(payload))
                messagebox.showerror("KIS API", str(payload), parent=self.root)
            elif kind == "status":
                self._write_log(str(payload))
            elif kind == "show":
                self.show_window()
            elif kind == "stop":
                self.stop_collection()
            elif kind == "exit":
                self.exit_app()
                return
            elif kind == "catalog":
                self._filter_catalog()
                self._update_collection_summary()
                self._write_log(f"공식 종목 마스터 {payload:,}개를 갱신했습니다.")
            elif kind == "catalog_error":
                self._update_collection_summary()
                self._write_log(f"종목 마스터 갱신 실패: {payload}")
                messagebox.showerror("종목 마스터", str(payload), parent=self.root)
            elif kind == "history":
                self._render_history(*payload)
            elif kind == "history_error":
                symbol, message = payload
                selected = self.watch_tree.selection()
                if not selected or selected[0] == symbol:
                    self._draw_empty_chart("일봉 조회에 실패했습니다")
                self._write_log(f"{symbol} 일봉 조회 실패: {message}")
        if not self.exiting:
            self.root.after(150, self._drain_events)

    def _insert_snapshot(self, snapshot: Snapshot) -> None:
        symbol_info = next(
            (item for item in self.catalog.symbols if item.code == snapshot.symbol), None
        )
        name = symbol_info.name if symbol_info else snapshot.symbol
        price = f"{int(snapshot.price):,}" if snapshot.price.isdigit() else snapshot.price
        volume = f"{int(snapshot.volume):,}" if snapshot.volume.isdigit() else snapshot.volume
        try:
            change_value = float(snapshot.change_rate)
        except ValueError:
            change_value = 0.0
        tag = "rise" if change_value > 0 else "fall" if change_value < 0 else ""
        values = (name, snapshot.symbol, price, f"{snapshot.change_rate}%", volume)
        if self.watch_tree.exists(snapshot.symbol):
            self.watch_tree.item(snapshot.symbol, values=values, tags=(tag,))
        else:
            self.watch_tree.insert(
                "", "end", iid=snapshot.symbol, values=values, tags=(tag,)
            )
        selected = self.watch_tree.selection()
        if selected and selected[0] == snapshot.symbol:
            self.quote_price_var.set(f"{price}원")
            self.quote_change_var.set(f"{snapshot.change_rate}%")

    def _load_recent_snapshots(self) -> None:
        for row in reversed(self.ledger.recent_snapshots(100)):
            self._insert_snapshot(
                Snapshot(
                    collected_at=row["collected_at"],
                    symbol=row["symbol"],
                    price=row["price"],
                    change_rate=row["change_rate"] or "",
                    volume=row["volume"] or "",
                )
            )

    def _write_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{stamp}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def hide_to_tray(self) -> None:
        self.root.withdraw()
        if self.tray_icon is None:
            self._start_tray()
        self._write_log("창을 숨겼습니다. 백그라운드 수집은 계속됩니다.")

    def _start_tray(self) -> None:
        image = Image.new("RGB", (64, 64), "#16222c")
        draw = ImageDraw.Draw(image)
        draw.rectangle((12, 34, 22, 52), fill="#2fb170")
        draw.rectangle((27, 22, 37, 52), fill="#48a8e8")
        draw.rectangle((42, 10, 52, 52), fill="#f0b429")
        menu = pystray.Menu(
            pystray.MenuItem("열기", lambda *_: self.events.put(("show", None)), default=True),
            pystray.MenuItem("수집 중지", lambda *_: self.events.put(("stop", None))),
            pystray.MenuItem("종료", lambda *_: self.events.put(("exit", None))),
        )
        self.tray_icon = pystray.Icon("kis-intraday-trader", image, "KIS Intraday Trader", menu)
        threading.Thread(target=self.tray_icon.run, name="kis-system-tray", daemon=True).start()

    def show_window(self) -> None:
        self.root.after(0, self.root.deiconify)
        self.root.after(0, self.root.lift)

    def exit_app(self) -> None:
        self.exiting = True
        if self.collector:
            self.collector.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()


def _ensure_config(path: Path) -> None:
    if path.exists():
        return
    example = path.with_name("settings.example.yaml")
    if not example.exists():
        raise ConfigError(f"Configuration file not found: {path}")
    raw = yaml.safe_load(example.read_text(encoding="utf-8"))
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="KIS Intraday Trader desktop GUI")
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--minimized", action="store_true", help="start in the system tray")
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    _ensure_config(config_path)
    settings = load_settings(config_path)
    root = tk.Tk()
    app = TraderApp(root, settings, config_path)
    if args.minimized:
        root.after(100, app.hide_to_tray)
    root.mainloop()


if __name__ == "__main__":
    main()
