from app.data_ingestion.tickers import trading_symbol


def test_trading_symbol():
    assert trading_symbol("MMC") == "MRSH" and trading_symbol("BK") == "BNY" and trading_symbol("BRK.B") == "BRK-B"
    assert trading_symbol("AAPL") == "AAPL"
    # 1 Oct 2026: Fiserv is FISV again (FI is dead), EQR became VMRK
    assert trading_symbol("FISV") == "FISV" and trading_symbol("FI") == "FISV" and trading_symbol("EQR") == "VMRK"
