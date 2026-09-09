from __future__ import annotations

import json
import zipfile
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path

import httpx
import yaml

MASTER_URLS = {
    "KOSPI": "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
    "KOSDAQ": "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
}


@dataclass(frozen=True)
class Symbol:
    code: str
    name: str
    market: str


class SymbolCatalog:
    def __init__(self, cache_path: str | Path, fallback_path: str | Path | None = None):
        self.cache_path = Path(cache_path)
        self.fallback_path = Path(fallback_path) if fallback_path else None
        self.symbols: list[Symbol] = []

    @staticmethod
    def parse_master(content: bytes, market: str) -> list[Symbol]:
        results: list[Symbol] = []
        for line in content.splitlines():
            if len(line) < 61:
                continue
            code = line[0:9].decode("euc-kr", errors="ignore").strip()
            name = line[21:61].decode("euc-kr", errors="ignore").strip()
            if len(code) > 6:
                code = code[-6:]
            if code.isdigit() and len(code) == 6 and name:
                results.append(Symbol(code, name, market))
        return results

    def load(self) -> list[Symbol]:
        if self.cache_path.exists():
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.symbols = [Symbol(**item) for item in raw]
            return self.symbols
        if self.fallback_path and self.fallback_path.exists():
            raw = yaml.safe_load(self.fallback_path.read_text(encoding="utf-8")) or {}
            self.symbols = [
                Symbol(str(item["code"]), str(item["name"]), "KOSPI")
                for item in raw.get("symbols", [])
            ]
        return self.symbols

    def refresh(self, timeout: float = 30.0) -> list[Symbol]:
        symbols: list[Symbol] = []
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            for market, url in MASTER_URLS.items():
                response = client.get(url)
                response.raise_for_status()
                with zipfile.ZipFile(BytesIO(response.content)) as archive:
                    names = archive.namelist()
                    if not names:
                        raise ValueError(f"{market} master archive is empty")
                    symbols.extend(self.parse_master(archive.read(names[0]), market))
        unique = {symbol.code: symbol for symbol in symbols}
        self.symbols = sorted(unique.values(), key=lambda item: (item.name, item.code))
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps([asdict(item) for item in self.symbols], ensure_ascii=False),
            encoding="utf-8",
        )
        return self.symbols

    def search(self, query: str, limit: int = 100) -> list[Symbol]:
        needle = query.strip().casefold()
        if not needle:
            return self.symbols[:limit]
        prefix: list[Symbol] = []
        contains: list[Symbol] = []
        for symbol in self.symbols:
            code = symbol.code.casefold()
            name = symbol.name.casefold()
            if code.startswith(needle) or name.startswith(needle):
                prefix.append(symbol)
            elif needle in code or needle in name:
                contains.append(symbol)
        return (prefix + contains)[:limit]

