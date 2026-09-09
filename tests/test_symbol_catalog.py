import json

from kis_trader.symbol_catalog import SymbolCatalog


def test_parse_and_search_master(tmp_path):
    line = b"005930   " + b"KR7005930003" + "삼성전자".encode("euc-kr").ljust(40, b" ") + b"rest"
    parsed = SymbolCatalog.parse_master(line, "KOSPI")
    assert parsed[0].code == "005930"
    assert parsed[0].name == "삼성전자"
    catalog = SymbolCatalog(tmp_path / "symbols.json")
    catalog.symbols = parsed
    assert catalog.search("삼성")[0].code == "005930"


def test_cached_catalog_loads(tmp_path):
    path = tmp_path / "symbols.json"
    path.write_text(
        json.dumps([{"code": "005930", "name": "삼성전자", "market": "KOSPI"}]),
        encoding="utf-8",
    )
    assert SymbolCatalog(path).load()[0].name == "삼성전자"
