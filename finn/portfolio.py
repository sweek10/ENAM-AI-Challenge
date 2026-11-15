"""Mock portfolio: 15 NSE/BSE stocks across sectors and sizes. Edit freely.

bse_code is the BSE scrip code, used for BSE's announcements API.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Stock:
    symbol: str      # NSE symbol
    bse_code: str    # BSE scrip code
    name: str
    sector: str
    size: str        # large / mid

    @property
    def yahoo(self) -> str:
        return f"{self.symbol}.NS"


PORTFOLIO = [
    Stock("RELIANCE",   "500325", "Reliance Industries",     "Energy / Conglomerate", "large"),
    Stock("HDFCBANK",   "500180", "HDFC Bank",               "Banking",               "large"),
    Stock("TCS",        "532540", "Tata Consultancy Services","IT Services",          "large"),
    Stock("BAJFINANCE", "500034", "Bajaj Finance",           "NBFC",                  "large"),
    Stock("MARUTI",     "532500", "Maruti Suzuki",           "Auto",                  "large"),
    Stock("SUNPHARMA",  "524715", "Sun Pharmaceutical",      "Pharma",                "large"),
    Stock("TATASTEEL",  "500470", "Tata Steel",              "Metals",                "large"),
    Stock("LT",         "500510", "Larsen & Toubro",         "Infra / Capital Goods", "large"),
    Stock("ITC",        "500875", "ITC",                     "FMCG",                  "large"),
    Stock("TRENT",      "500251", "Trent",                   "Retail",                "large"),
    Stock("ETERNAL",    "543320", "Eternal (Zomato)",        "Internet / Delivery",   "large"),
    Stock("HAL",        "541154", "Hindustan Aeronautics",   "Defence PSU",           "large"),
    Stock("DIXON",      "540699", "Dixon Technologies",      "Electronics Mfg",       "mid"),
    Stock("PERSISTENT", "533179", "Persistent Systems",      "IT Services",           "mid"),
    Stock("DEEPAKNTR",  "506401", "Deepak Nitrite",          "Chemicals",             "mid"),
]

BY_SYMBOL = {s.symbol: s for s in PORTFOLIO}


def get_stock(symbol: str) -> Stock:
    return BY_SYMBOL[symbol.upper()]
