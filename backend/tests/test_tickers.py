from app.data_ingestion.tickers import trading_symbol


def test_trading_symbol():
    assert trading_symbol("MMC") == "MRSH" and trading_symbol("BK") == "BNY" and trading_symbol("BRK.B") == "BRK-B"
    assert trading_symbol("AAPL") == "AAPL"
