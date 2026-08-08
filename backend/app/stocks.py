"""NSE F&O stock universe.

Every name here has a listed futures and options contract on the NSE, so a
directional call on one can be expressed as cash, futures or options. Writing
them out as full `Instrument` literals would be unreadable at this count, so
the table below carries only what differs per stock and the instruments are
generated from it.

Two caveats, both deliberate:

* **Lot sizes change.** The NSE revises them roughly twice a year, and a wrong
  lot size silently multiplies your risk. The values here are a starting point;
  when Kite is connected the instrument dump is authoritative and
  `refresh_lot_sizes()` overwrites them. `TradePanel` shows which source it is
  using.
* **Base prices only seed the simulator.** They are approximate and go stale
  immediately; the moment a live provider serves the symbol, real prices
  replace them entirely.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

#: (tradingsymbol, display name, sector, lot size, indicative price)
NSE_FO_STOCKS: List[Tuple[str, str, str, int, float]] = [
    # ── Banks & financials ────────────────────────────────────
    ("HDFCBANK", "HDFC Bank", "Financials", 550, 1_680.0),
    ("ICICIBANK", "ICICI Bank", "Financials", 700, 1_240.0),
    ("SBIN", "State Bank of India", "Financials", 750, 820.0),
    ("KOTAKBANK", "Kotak Mahindra Bank", "Financials", 400, 1_760.0),
    ("AXISBANK", "Axis Bank", "Financials", 625, 1_120.0),
    ("INDUSINDBK", "IndusInd Bank", "Financials", 700, 980.0),
    ("BANKBARODA", "Bank of Baroda", "Financials", 2925, 245.0),
    ("PNB", "Punjab National Bank", "Financials", 8000, 105.0),
    ("FEDERALBNK", "Federal Bank", "Financials", 5000, 195.0),
    ("IDFCFIRSTB", "IDFC First Bank", "Financials", 7500, 68.0),
    ("AUBANK", "AU Small Finance Bank", "Financials", 1000, 610.0),
    ("BAJFINANCE", "Bajaj Finance", "Financials", 750, 7_100.0),
    ("BAJAJFINSV", "Bajaj Finserv", "Financials", 500, 1_640.0),
    ("SBILIFE", "SBI Life Insurance", "Financials", 375, 1_480.0),
    ("HDFCLIFE", "HDFC Life Insurance", "Financials", 1100, 640.0),
    ("ICICIGI", "ICICI Lombard", "Financials", 325, 1_820.0),
    ("ICICIPRULI", "ICICI Prudential Life", "Financials", 925, 630.0),
    ("CHOLAFIN", "Cholamandalam Finance", "Financials", 625, 1_420.0),
    ("SHRIRAMFIN", "Shriram Finance", "Financials", 825, 3_100.0),
    ("MUTHOOTFIN", "Muthoot Finance", "Financials", 375, 1_920.0),
    ("LICHSGFIN", "LIC Housing Finance", "Financials", 1000, 620.0),
    ("HDFCAMC", "HDFC AMC", "Financials", 200, 4_200.0),

    # ── IT ────────────────────────────────────────────────────
    ("TCS", "Tata Consultancy Services", "IT", 175, 4_100.0),
    ("INFY", "Infosys", "IT", 400, 1_880.0),
    ("HCLTECH", "HCL Technologies", "IT", 350, 1_780.0),
    ("WIPRO", "Wipro", "IT", 3000, 545.0),
    ("TECHM", "Tech Mahindra", "IT", 600, 1_680.0),
    ("LTIM", "LTIMindtree", "IT", 150, 5_800.0),
    ("PERSISTENT", "Persistent Systems", "IT", 100, 5_600.0),
    ("COFORGE", "Coforge", "IT", 125, 7_400.0),
    ("MPHASIS", "Mphasis", "IT", 275, 2_900.0),

    # ── Energy & utilities ────────────────────────────────────
    ("RELIANCE", "Reliance Industries", "Energy", 500, 1_320.0),
    ("ONGC", "Oil & Natural Gas Corp", "Energy", 3850, 255.0),
    ("BPCL", "Bharat Petroleum", "Energy", 1975, 320.0),
    ("IOC", "Indian Oil Corp", "Energy", 6500, 142.0),
    ("GAIL", "GAIL India", "Energy", 4575, 195.0),
    ("NTPC", "NTPC", "Utilities", 1500, 355.0),
    ("POWERGRID", "Power Grid Corp", "Utilities", 3600, 315.0),
    ("TATAPOWER", "Tata Power", "Utilities", 3375, 400.0),
    ("ADANIENT", "Adani Enterprises", "Energy", 300, 2_450.0),
    ("ADANIPORTS", "Adani Ports & SEZ", "Infrastructure", 800, 1_380.0),
    ("ADANIGREEN", "Adani Green Energy", "Utilities", 600, 1_060.0),
    ("COALINDIA", "Coal India", "Energy", 2100, 405.0),

    # ── Autos ─────────────────────────────────────────────────
    ("MARUTI", "Maruti Suzuki", "Auto", 50, 12_400.0),
    ("TATAMOTORS", "Tata Motors", "Auto", 800, 760.0),
    ("M&M", "Mahindra & Mahindra", "Auto", 200, 3_050.0),
    ("BAJAJ-AUTO", "Bajaj Auto", "Auto", 75, 9_100.0),
    ("HEROMOTOCO", "Hero MotoCorp", "Auto", 150, 4_450.0),
    ("EICHERMOT", "Eicher Motors", "Auto", 175, 5_100.0),
    ("TVSMOTOR", "TVS Motor", "Auto", 350, 2_600.0),
    ("ASHOKLEY", "Ashok Leyland", "Auto", 2500, 225.0),
    ("BOSCHLTD", "Bosch", "Auto", 25, 34_000.0),
    ("MOTHERSON", "Samvardhana Motherson", "Auto", 3600, 155.0),
    ("BALKRISIND", "Balkrishna Industries", "Auto", 300, 2_650.0),

    # ── Metals & mining ───────────────────────────────────────
    ("TATASTEEL", "Tata Steel", "Metals", 5500, 145.0),
    ("JSWSTEEL", "JSW Steel", "Metals", 675, 1_020.0),
    ("HINDALCO", "Hindalco Industries", "Metals", 1400, 640.0),
    ("VEDL", "Vedanta", "Metals", 1150, 460.0),
    ("SAIL", "Steel Authority of India", "Metals", 8000, 118.0),
    ("NATIONALUM", "National Aluminium", "Metals", 3750, 195.0),
    ("JINDALSTEL", "Jindal Steel & Power", "Metals", 625, 920.0),
    ("HINDZINC", "Hindustan Zinc", "Metals", 1300, 480.0),

    # ── FMCG & consumer ───────────────────────────────────────
    ("HINDUNILVR", "Hindustan Unilever", "FMCG", 300, 2_380.0),
    ("ITC", "ITC", "FMCG", 1600, 420.0),
    ("NESTLEIND", "Nestle India", "FMCG", 250, 2_240.0),
    ("BRITANNIA", "Britannia Industries", "FMCG", 200, 4_900.0),
    ("DABUR", "Dabur India", "FMCG", 1250, 520.0),
    ("GODREJCP", "Godrej Consumer", "FMCG", 500, 1_180.0),
    ("MARICO", "Marico", "FMCG", 1200, 690.0),
    ("TATACONSUM", "Tata Consumer Products", "FMCG", 550, 1_060.0),
    ("COLPAL", "Colgate-Palmolive India", "FMCG", 350, 2_460.0),
    ("VBL", "Varun Beverages", "FMCG", 1200, 480.0),
    ("TITAN", "Titan Company", "Consumer", 175, 3_400.0),
    ("TRENT", "Trent", "Consumer", 100, 5_600.0),
    ("DMART", "Avenue Supermarts", "Consumer", 150, 3_900.0),
    ("ASIANPAINT", "Asian Paints", "Consumer", 200, 2_420.0),
    ("PIDILITIND", "Pidilite Industries", "Consumer", 250, 2_960.0),
    ("HAVELLS", "Havells India", "Consumer", 500, 1_580.0),
    ("VOLTAS", "Voltas", "Consumer", 375, 1_620.0),

    # ── Pharma & healthcare ───────────────────────────────────
    ("SUNPHARMA", "Sun Pharmaceutical", "Pharma", 350, 1_760.0),
    ("DRREDDY", "Dr Reddy's Laboratories", "Pharma", 625, 1_240.0),
    ("CIPLA", "Cipla", "Pharma", 375, 1_520.0),
    ("DIVISLAB", "Divi's Laboratories", "Pharma", 100, 6_100.0),
    ("APOLLOHOSP", "Apollo Hospitals", "Healthcare", 125, 7_200.0),
    ("LUPIN", "Lupin", "Pharma", 425, 2_080.0),
    ("AUROPHARMA", "Aurobindo Pharma", "Pharma", 550, 1_180.0),
    ("TORNTPHARM", "Torrent Pharmaceuticals", "Pharma", 250, 3_300.0),
    ("ZYDUSLIFE", "Zydus Lifesciences", "Pharma", 900, 960.0),
    ("MAXHEALTH", "Max Healthcare", "Healthcare", 550, 1_120.0),

    # ── Cement, infra & industrials ───────────────────────────
    ("LT", "Larsen & Toubro", "Industrials", 150, 3_650.0),
    ("ULTRACEMCO", "UltraTech Cement", "Cement", 50, 11_600.0),
    ("GRASIM", "Grasim Industries", "Cement", 250, 2_680.0),
    ("SHREECEM", "Shree Cement", "Cement", 25, 29_000.0),
    ("AMBUJACEM", "Ambuja Cements", "Cement", 1800, 560.0),
    ("SIEMENS", "Siemens India", "Industrials", 125, 3_100.0),
    ("ABB", "ABB India", "Industrials", 125, 5_300.0),
    ("BEL", "Bharat Electronics", "Defence", 2850, 395.0),
    ("HAL", "Hindustan Aeronautics", "Defence", 150, 4_300.0),
    ("BHEL", "BHEL", "Industrials", 2625, 230.0),
    ("CUMMINSIND", "Cummins India", "Industrials", 300, 3_400.0),

    # ── Telecom, media & others ───────────────────────────────
    ("BHARTIARTL", "Bharti Airtel", "Telecom", 475, 1_920.0),
    ("IDEA", "Vodafone Idea", "Telecom", 80000, 8.5),
    ("INDIGO", "InterGlobe Aviation", "Aviation", 150, 5_400.0),
    ("IRCTC", "IRCTC", "Travel", 875, 760.0),
    ("ZOMATO", "Eternal (Zomato)", "Internet", 2425, 265.0),
    ("PAYTM", "One97 (Paytm)", "Internet", 725, 980.0),
    ("NAUKRI", "Info Edge", "Internet", 375, 1_420.0),
    ("PFC", "Power Finance Corp", "Financials", 1300, 405.0),
    ("RECLTD", "REC", "Financials", 1500, 420.0),
    ("IRFC", "Indian Railway Finance", "Financials", 4750, 128.0),
    ("UPL", "UPL", "Chemicals", 1355, 660.0),
    ("SRF", "SRF", "Chemicals", 375, 2_900.0),
    ("PIIND", "PI Industries", "Chemicals", 175, 3_700.0),
    ("DEEPAKNTR", "Deepak Nitrite", "Chemicals", 250, 2_100.0),
    ("TATACHEM", "Tata Chemicals", "Chemicals", 550, 1_020.0),
    ("DLF", "DLF", "Realty", 825, 720.0),
    ("GODREJPROP", "Godrej Properties", "Realty", 275, 2_180.0),
    ("OBEROIRLTY", "Oberoi Realty", "Realty", 700, 1_760.0),
    ("JSWENERGY", "JSW Energy", "Utilities", 1300, 520.0),
    ("JUBLFOOD", "Jubilant FoodWorks", "Consumer", 1000, 680.0),
    ("PAGEIND", "Page Industries", "Consumer", 15, 44_000.0),
    ("BERGEPAINT", "Berger Paints", "Consumer", 1100, 520.0),
]

#: Lot sizes get overwritten from Kite's instrument dump when it is available.
#: Kept module-level so `instruments.py` and the trade panel read one source.
LOT_SIZES: Dict[str, int] = {row[0]: row[3] for row in NSE_FO_STOCKS}
LOT_SOURCE: Dict[str, str] = {row[0]: "table" for row in NSE_FO_STOCKS}


def refresh_lot_sizes(dump: Dict[str, int]) -> int:
    """Adopt authoritative lot sizes from a broker instrument dump.

    Returns the number of stocks corrected. Kite's dump is the only thing that
    knows the current lot size, so it always wins over the table above.
    """
    changed = 0
    for symbol, lot in dump.items():
        if lot and LOT_SIZES.get(symbol) != lot:
            LOT_SIZES[symbol] = int(lot)
            LOT_SOURCE[symbol] = "broker"
            changed += 1
        elif lot:
            LOT_SOURCE[symbol] = "broker"
    return changed


def sectors() -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for symbol, _display, sector, _lot, _price in NSE_FO_STOCKS:
        out.setdefault(sector, []).append(symbol)
    return out
