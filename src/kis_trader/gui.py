from __future__ import annotations

import argparse
import queue
import threading
import tkinter as tk
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import pystray
import yaml
from PIL import Image, ImageDraw

from .collector import BackgroundCollector, Snapshot
from .config import ConfigError, Settings, load_settings
from .kis_client import KisClient
from .ledger import Ledger
from .market_data import MarketData
from .secrets import Credentials, SecretStore, SecretStoreError


class TraderApp:
    def __init__(self, root: tk.Tk, settings: Settings, config_path: Path):
        self.root = root
        self.settings = settings
        self.config_path = config_path
        self.ledger = Ledger(settings.storage.database_path)
        self.ledger.initialize()
        self.secret_store = SecretStore()
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
        connection = ttk.Frame(notebook, padding=14)
        notebook.add(dashboard, text="대시보드")
        notebook.add(connection, text="API 연결")
        self._build_dashboard(dashboard)
        self._build_connection(connection)

    def _build_dashboard(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent)
        controls.pack(fill="x", pady=(0, 12))
        ttk.Label(controls, text="수집 종목", style="Section.TLabel").pack(side="left")
        self.symbols_var = tk.StringVar(value="005930, 009150, 042660")
        ttk.Entry(controls, textvariable=self.symbols_var, width=34).pack(side="left", padx=10)
        ttk.Label(controls, text="주기(초)").pack(side="left", padx=(10, 4))
        self.interval_var = tk.IntVar(value=60)
        ttk.Spinbox(controls, from_=5, to=3600, textvariable=self.interval_var, width=7).pack(
            side="left"
        )
        self.start_button = ttk.Button(controls, text="수집 시작", command=self.start_collection)
        self.start_button.pack(side="right")
        self.stop_button = ttk.Button(
            controls, text="수집 중지", command=self.stop_collection, state="disabled"
        )
        self.stop_button.pack(side="right", padx=8)

        columns = ("time", "symbol", "price", "change", "volume")
        self.tree = ttk.Treeview(parent, columns=columns, show="headings", height=13)
        headings = {
            "time": "수집시각",
            "symbol": "종목코드",
            "price": "현재가",
            "change": "등락률(%)",
            "volume": "누적거래량",
        }
        widths = {"time": 190, "symbol": 100, "price": 130, "change": 110, "volume": 150}
        for name in columns:
            self.tree.heading(name, text=headings[name])
            self.tree.column(name, width=widths[name], anchor="center")
        self.tree.pack(fill="both", expand=True)

        ttk.Label(parent, text="운영 로그", style="Section.TLabel").pack(
            fill="x", pady=(14, 6)
        )
        self.log = tk.Text(parent, height=8, state="disabled", font=("Consolas", 9), wrap="word")
        self.log.pack(fill="x")

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
        symbols = [item.strip() for item in self.symbols_var.get().split(",") if item.strip()]
        if not symbols:
            raise ValueError("수집 종목을 입력하세요")
        if len(symbols) > self.settings.risk.max_symbols_per_day:
            raise ValueError(f"종목은 최대 {self.settings.risk.max_symbols_per_day}개입니다")
        if any(not value.isdigit() or len(value) != 6 for value in symbols):
            raise ValueError("종목코드는 6자리 숫자여야 합니다")
        return list(dict.fromkeys(symbols))

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
        if not self.exiting:
            self.root.after(150, self._drain_events)

    def _insert_snapshot(self, snapshot: Snapshot) -> None:
        self.tree.insert(
            "",
            0,
            values=(
                snapshot.collected_at.replace("T", " "),
                snapshot.symbol,
                f"{int(snapshot.price):,}" if snapshot.price.isdigit() else snapshot.price,
                snapshot.change_rate,
                snapshot.volume,
            ),
        )
        children = self.tree.get_children()
        for item in children[200:]:
            self.tree.delete(item)

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
