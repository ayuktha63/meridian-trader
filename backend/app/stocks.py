"""NSE F&O stocks: which ones there are comes from Angel One; what they are
called does not.

Angel One's scrip master is the authority on which stocks carry a listed
future, the lot it trades in and the tick it is quoted in. All three change —
the NSE revises lots about twice a year — and a wrong lot silently multiplies
the risk on an order. So none of them is written down here: `set_universe()`
is handed a fresh read each time the master loads, and until the first one
there are no stocks at all.

The master carries a ticker and nothing about the company behind it.
`REFERENCE` supplies the two things it lacks: a name to show, and the sector
the live sector-exposure limit groups by. It is a label, never a price or a
size. A stock Angel One lists that is missing from it is still tradable —
shown under its ticker, and counted with the other unlabelled ones as a
single sector, so the limit binds them together rather than not at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Listing:
    """One stock as Angel One's scrip master lists it."""
    symbol: str
    lot_size: int        # shares in one futures lot
    tick_size: float     # of the cash share, in rupees


#: Every NSE stock with an unexpired future, as of the last scrip-master load.
UNIVERSE: List[Listing] = []
_LISTED: Dict[str, Listing] = {}

UNCLASSIFIED = "Unclassified"

#: ticker -> (display name, sector)
REFERENCE: Dict[str, Tuple[str, str]] = {
    # ── Banks & financials ────────────────────────────────────
    "HDFCBANK": ("HDFC Bank", "Financials"),
    "ICICIBANK": ("ICICI Bank", "Financials"),
    "SBIN": ("State Bank of India", "Financials"),
    "KOTAKBANK": ("Kotak Mahindra Bank", "Financials"),
    "AXISBANK": ("Axis Bank", "Financials"),
    "INDUSINDBK": ("IndusInd Bank", "Financials"),
    "BANKBARODA": ("Bank of Baroda", "Financials"),
    "PNB": ("Punjab National Bank", "Financials"),
    "FEDERALBNK": ("Federal Bank", "Financials"),
    "IDFCFIRSTB": ("IDFC First Bank", "Financials"),
    "AUBANK": ("AU Small Finance Bank", "Financials"),
    "BAJFINANCE": ("Bajaj Finance", "Financials"),
    "BAJAJFINSV": ("Bajaj Finserv", "Financials"),
    "SBILIFE": ("SBI Life Insurance", "Financials"),
    "HDFCLIFE": ("HDFC Life Insurance", "Financials"),
    "ICICIGI": ("ICICI Lombard", "Financials"),
    "ICICIPRULI": ("ICICI Prudential Life", "Financials"),
    "CHOLAFIN": ("Cholamandalam Finance", "Financials"),
    "SHRIRAMFIN": ("Shriram Finance", "Financials"),
    "MUTHOOTFIN": ("Muthoot Finance", "Financials"),
    "LICHSGFIN": ("LIC Housing Finance", "Financials"),
    "HDFCAMC": ("HDFC AMC", "Financials"),

    # ── IT ────────────────────────────────────────────────────
    "TCS": ("Tata Consultancy Services", "IT"),
    "INFY": ("Infosys", "IT"),
    "HCLTECH": ("HCL Technologies", "IT"),
    "WIPRO": ("Wipro", "IT"),
    "TECHM": ("Tech Mahindra", "IT"),
    "LTIM": ("LTIMindtree", "IT"),
    "PERSISTENT": ("Persistent Systems", "IT"),
    "COFORGE": ("Coforge", "IT"),
    "MPHASIS": ("Mphasis", "IT"),

    # ── Energy & utilities ────────────────────────────────────
    "RELIANCE": ("Reliance Industries", "Energy"),
    "ONGC": ("Oil & Natural Gas Corp", "Energy"),
    "BPCL": ("Bharat Petroleum", "Energy"),
    "IOC": ("Indian Oil Corp", "Energy"),
    "GAIL": ("GAIL India", "Energy"),
    "NTPC": ("NTPC", "Utilities"),
    "POWERGRID": ("Power Grid Corp", "Utilities"),
    "TATAPOWER": ("Tata Power", "Utilities"),
    "ADANIENT": ("Adani Enterprises", "Energy"),
    "ADANIPORTS": ("Adani Ports & SEZ", "Infrastructure"),
    "ADANIGREEN": ("Adani Green Energy", "Utilities"),
    "COALINDIA": ("Coal India", "Energy"),

    # ── Autos ─────────────────────────────────────────────────
    "MARUTI": ("Maruti Suzuki", "Auto"),
    "TATAMOTORS": ("Tata Motors", "Auto"),
    "M&M": ("Mahindra & Mahindra", "Auto"),
    "BAJAJ-AUTO": ("Bajaj Auto", "Auto"),
    "HEROMOTOCO": ("Hero MotoCorp", "Auto"),
    "EICHERMOT": ("Eicher Motors", "Auto"),
    "TVSMOTOR": ("TVS Motor", "Auto"),
    "ASHOKLEY": ("Ashok Leyland", "Auto"),
    "BOSCHLTD": ("Bosch", "Auto"),
    "MOTHERSON": ("Samvardhana Motherson", "Auto"),
    "BALKRISIND": ("Balkrishna Industries", "Auto"),

    # ── Metals & mining ───────────────────────────────────────
    "TATASTEEL": ("Tata Steel", "Metals"),
    "JSWSTEEL": ("JSW Steel", "Metals"),
    "HINDALCO": ("Hindalco Industries", "Metals"),
    "VEDL": ("Vedanta", "Metals"),
    "SAIL": ("Steel Authority of India", "Metals"),
    "NATIONALUM": ("National Aluminium", "Metals"),
    "JINDALSTEL": ("Jindal Steel & Power", "Metals"),
    "HINDZINC": ("Hindustan Zinc", "Metals"),

    # ── FMCG & consumer ───────────────────────────────────────
    "HINDUNILVR": ("Hindustan Unilever", "FMCG"),
    "ITC": ("ITC", "FMCG"),
    "NESTLEIND": ("Nestle India", "FMCG"),
    "BRITANNIA": ("Britannia Industries", "FMCG"),
    "DABUR": ("Dabur India", "FMCG"),
    "GODREJCP": ("Godrej Consumer", "FMCG"),
    "MARICO": ("Marico", "FMCG"),
    "TATACONSUM": ("Tata Consumer Products", "FMCG"),
    "COLPAL": ("Colgate-Palmolive India", "FMCG"),
    "VBL": ("Varun Beverages", "FMCG"),
    "TITAN": ("Titan Company", "Consumer"),
    "TRENT": ("Trent", "Consumer"),
    "DMART": ("Avenue Supermarts", "Consumer"),
    "ASIANPAINT": ("Asian Paints", "Consumer"),
    "PIDILITIND": ("Pidilite Industries", "Consumer"),
    "HAVELLS": ("Havells India", "Consumer"),
    "VOLTAS": ("Voltas", "Consumer"),

    # ── Pharma & healthcare ───────────────────────────────────
    "SUNPHARMA": ("Sun Pharmaceutical", "Pharma"),
    "DRREDDY": ("Dr Reddy's Laboratories", "Pharma"),
    "CIPLA": ("Cipla", "Pharma"),
    "DIVISLAB": ("Divi's Laboratories", "Pharma"),
    "APOLLOHOSP": ("Apollo Hospitals", "Healthcare"),
    "LUPIN": ("Lupin", "Pharma"),
    "AUROPHARMA": ("Aurobindo Pharma", "Pharma"),
    "TORNTPHARM": ("Torrent Pharmaceuticals", "Pharma"),
    "ZYDUSLIFE": ("Zydus Lifesciences", "Pharma"),
    "MAXHEALTH": ("Max Healthcare", "Healthcare"),

    # ── Cement, infra & industrials ───────────────────────────
    "LT": ("Larsen & Toubro", "Industrials"),
    "ULTRACEMCO": ("UltraTech Cement", "Cement"),
    "GRASIM": ("Grasim Industries", "Cement"),
    "SHREECEM": ("Shree Cement", "Cement"),
    "AMBUJACEM": ("Ambuja Cements", "Cement"),
    "SIEMENS": ("Siemens India", "Industrials"),
    "ABB": ("ABB India", "Industrials"),
    "BEL": ("Bharat Electronics", "Defence"),
    "HAL": ("Hindustan Aeronautics", "Defence"),
    "BHEL": ("BHEL", "Industrials"),
    "CUMMINSIND": ("Cummins India", "Industrials"),

    # ── Telecom, media & others ───────────────────────────────
    "BHARTIARTL": ("Bharti Airtel", "Telecom"),
    "IDEA": ("Vodafone Idea", "Telecom"),
    "INDIGO": ("InterGlobe Aviation", "Aviation"),
    "IRCTC": ("IRCTC", "Travel"),
    "ZOMATO": ("Eternal (Zomato)", "Internet"),
    "PAYTM": ("One97 (Paytm)", "Internet"),
    "NAUKRI": ("Info Edge", "Internet"),
    "PFC": ("Power Finance Corp", "Financials"),
    "RECLTD": ("REC", "Financials"),
    "IRFC": ("Indian Railway Finance", "Financials"),
    "UPL": ("UPL", "Chemicals"),
    "SRF": ("SRF", "Chemicals"),
    "PIIND": ("PI Industries", "Chemicals"),
    "DEEPAKNTR": ("Deepak Nitrite", "Chemicals"),
    "TATACHEM": ("Tata Chemicals", "Chemicals"),
    "DLF": ("DLF", "Realty"),
    "GODREJPROP": ("Godrej Properties", "Realty"),
    "OBEROIRLTY": ("Oberoi Realty", "Realty"),
    "JSWENERGY": ("JSW Energy", "Utilities"),
    "JUBLFOOD": ("Jubilant FoodWorks", "Consumer"),
    "PAGEIND": ("Page Industries", "Consumer"),
    "BERGEPAINT": ("Berger Paints", "Consumer"),
}


def set_universe(listings: List[Listing]) -> None:
    """Adopt a fresh read of the scrip master. The containers are changed in
    place: other modules hold these same objects."""
    UNIVERSE[:] = sorted(listings, key=lambda listing: listing.symbol)
    _LISTED.clear()
    _LISTED.update({listing.symbol: listing for listing in UNIVERSE})


def display_of(symbol: str) -> str:
    known = REFERENCE.get(symbol)
    return known[0] if known else symbol


def sector_of(symbol: str) -> Optional[str]:
    """The sector a stock's exposure is counted under, or None for anything
    that is not a listed stock."""
    known = REFERENCE.get(symbol)
    if known:
        return known[1]
    return UNCLASSIFIED if symbol in _LISTED else None
