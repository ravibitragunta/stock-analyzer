"""
stocks_list.py — Nifty 200 stock universe.

The definitive list is auto-populated from the NSE instruments file downloaded
by data_fetcher.py. This file contains the hardcoded fallback list and the
sector mapping for all 200 stocks.

On first run, data_fetcher.py will call populate_stocks_from_instruments() to
enrich this list with live instrument_key and ISIN data from the Upstox master.
"""

import json
import logging
from pathlib import Path

import config
import database as db

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# NIFTY 200 STOCK LIST
# Format: (symbol, name, sector)
# instrument_key is filled in from the Upstox instruments file at runtime
# ─────────────────────────────────────────────

NIFTY_200_STOCKS: list[tuple[str, str, str]] = [
    # ── FINANCIAL SERVICES ──
    ("HDFCBANK",     "HDFC Bank Ltd",                       "Finance"),
    ("ICICIBANK",    "ICICI Bank Ltd",                      "Finance"),
    ("KOTAKBANK",    "Kotak Mahindra Bank Ltd",              "Finance"),
    ("AXISBANK",     "Axis Bank Ltd",                       "Finance"),
    ("SBIN",         "State Bank of India",                  "Finance"),
    ("BAJFINANCE",   "Bajaj Finance Ltd",                   "Finance"),
    ("BAJAJFINSV",   "Bajaj Finserv Ltd",                   "Finance"),
    ("HDFCLIFE",     "HDFC Life Insurance Co Ltd",          "Finance"),
    ("SBILIFE",      "SBI Life Insurance Co Ltd",           "Finance"),
    ("ICICIGI",      "ICICI Lombard General Insurance",     "Finance"),
    ("MUTHOOTFIN",   "Muthoot Finance Ltd",                 "Finance"),
    ("CHOLAFIN",     "Cholamandalam Investment & Finance",  "Finance"),
    ("RECLTD",       "REC Ltd",                             "Finance"),
    ("PFC",          "Power Finance Corporation Ltd",       "Finance"),
    ("IRFC",         "Indian Railway Finance Corp Ltd",     "Finance"),
    ("SHRIRAMFIN",   "Shriram Finance Ltd",                 "Finance"),
    ("M&MFIN",       "Mahindra & Mahindra Financial Serv",  "Finance"),
    ("LICHSGFIN",    "LIC Housing Finance Ltd",             "Finance"),
    ("PNBHOUSING",   "PNB Housing Finance Ltd",             "Finance"),
    ("CANFINHOME",   "Can Fin Homes Ltd",                   "Finance"),
    ("FEDERALBNK",   "Federal Bank Ltd",                    "Finance"),
    ("IDFCFIRSTB",   "IDFC First Bank Ltd",                 "Finance"),
    ("BANDHANBNK",   "Bandhan Bank Ltd",                    "Finance"),
    ("RBLBANK",      "RBL Bank Ltd",                        "Finance"),
    ("INDUSINDBK",   "IndusInd Bank Ltd",                   "Finance"),
    ("PNB",          "Punjab National Bank",                "Finance"),
    ("BANKBARODA",   "Bank of Baroda",                      "Finance"),
    ("CANBK",        "Canara Bank",                         "Finance"),
    ("UNIONBANK",    "Union Bank of India",                 "Finance"),
    ("IOB",          "Indian Overseas Bank",                "Finance"),

    # ── IT ──
    ("TCS",          "Tata Consultancy Services Ltd",       "IT"),
    ("INFY",         "Infosys Ltd",                         "IT"),
    ("WIPRO",        "Wipro Ltd",                           "IT"),
    ("HCLTECH",      "HCL Technologies Ltd",               "IT"),
    ("TECHM",        "Tech Mahindra Ltd",                   "IT"),
    ("LTIM",         "LTIMindtree Ltd",                     "IT"),
    ("MPHASIS",      "Mphasis Ltd",                         "IT"),
    ("PERSISTENT",   "Persistent Systems Ltd",              "IT"),
    ("COFORGE",      "Coforge Ltd",                         "IT"),
    ("LTTS",         "L&T Technology Services Ltd",         "IT"),
    ("OFSS",         "Oracle Financial Services Software",  "IT"),

    # ── OIL & GAS / ENERGY ──
    ("RELIANCE",     "Reliance Industries Ltd",             "Energy"),
    ("ONGC",         "Oil & Natural Gas Corp Ltd",          "Energy"),
    ("BPCL",         "Bharat Petroleum Corp Ltd",           "Energy"),
    ("IOC",          "Indian Oil Corporation Ltd",          "Energy"),
    ("GAIL",         "GAIL India Ltd",                      "Energy"),
    ("ADANIPORTS",   "Adani Ports & SEZ Ltd",               "Energy"),
    ("ADANIGREEN",   "Adani Green Energy Ltd",              "Energy"),
    ("ADANIPOWER",   "Adani Power Ltd",                     "Energy"),
    ("ADANIENT",     "Adani Enterprises Ltd",               "Energy"),
    ("TATAPOWER",    "Tata Power Company Ltd",              "Energy"),
    ("POWERGRID",    "Power Grid Corp of India Ltd",        "Energy"),
    ("NTPC",         "NTPC Ltd",                            "Energy"),
    ("CESC",         "CESC Ltd",                            "Energy"),
    ("NHPC",         "NHPC Ltd",                            "Energy"),
    ("SJVN",         "SJVN Ltd",                            "Energy"),

    # ── AUTOMOBILES ──
    ("MARUTI",       "Maruti Suzuki India Ltd",             "Auto"),
    ("TATAMOTORS",   "Tata Motors Ltd",                     "Auto"),
    ("M&M",          "Mahindra & Mahindra Ltd",             "Auto"),
    ("BAJAJ-AUTO",   "Bajaj Auto Ltd",                      "Auto"),
    ("HEROMOTOCO",   "Hero MotoCorp Ltd",                   "Auto"),
    ("EICHERMOT",    "Eicher Motors Ltd",                   "Auto"),
    ("TVSMOTOR",     "TVS Motor Company Ltd",               "Auto"),
    ("ASHOKLEY",     "Ashok Leyland Ltd",                   "Auto"),
    ("BHARATFORG",   "Bharat Forge Ltd",                    "Auto"),
    ("MOTHERSON",    "Samvardhana Motherson Intl Ltd",      "Auto"),
    ("APOLLOTYRE",   "Apollo Tyres Ltd",                    "Auto"),
    ("MRF",          "MRF Ltd",                             "Auto"),

    # ── FMCG ──
    ("HINDUNILVR",   "Hindustan Unilever Ltd",              "FMCG"),
    ("ITC",          "ITC Ltd",                             "FMCG"),
    ("NESTLEIND",    "Nestle India Ltd",                    "FMCG"),
    ("BRITANNIA",    "Britannia Industries Ltd",            "FMCG"),
    ("DABUR",        "Dabur India Ltd",                     "FMCG"),
    ("GODREJCP",     "Godrej Consumer Products Ltd",        "FMCG"),
    ("MARICO",       "Marico Ltd",                          "FMCG"),
    ("COLPAL",       "Colgate-Palmolive India Ltd",         "FMCG"),
    ("EMAMILTD",     "Emami Ltd",                           "FMCG"),
    ("TATACONSUM",   "Tata Consumer Products Ltd",          "FMCG"),
    ("VARUNBEV",     "Varun Beverages Ltd",                 "FMCG"),
    ("RADICO",       "Radico Khaitan Ltd",                  "FMCG"),
    ("UBL",          "United Breweries Ltd",                "FMCG"),
    ("MCDOWELL-N",   "United Spirits Ltd",                  "FMCG"),

    # ── PHARMA & HEALTHCARE ──
    ("SUNPHARMA",    "Sun Pharmaceutical Industries Ltd",   "Pharma"),
    ("DRREDDY",      "Dr Reddy's Laboratories Ltd",         "Pharma"),
    ("CIPLA",        "Cipla Ltd",                           "Pharma"),
    ("DIVISLAB",     "Divi's Laboratories Ltd",             "Pharma"),
    ("BIOCON",       "Biocon Ltd",                          "Pharma"),
    ("LUPIN",        "Lupin Ltd",                           "Pharma"),
    ("AUROPHARMA",   "Aurobindo Pharma Ltd",                "Pharma"),
    ("TORNTPHARM",   "Torrent Pharmaceuticals Ltd",         "Pharma"),
    ("ALKEM",        "Alkem Laboratories Ltd",              "Pharma"),
    ("IPCALAB",      "IPCA Laboratories Ltd",               "Pharma"),
    ("APOLLOHOSP",   "Apollo Hospitals Enterprise Ltd",     "Healthcare"),
    ("MAXHEALTH",    "Max Healthcare Institute Ltd",        "Healthcare"),
    ("FORTIS",       "Fortis Healthcare Ltd",               "Healthcare"),
    ("METROPOLIS",   "Metropolis Healthcare Ltd",           "Healthcare"),
    ("LALPATHLAB",   "Dr Lal PathLabs Ltd",                 "Healthcare"),

    # ── METALS & MINING ──
    ("TATASTEEL",    "Tata Steel Ltd",                      "Metal"),
    ("JSWSTEEL",     "JSW Steel Ltd",                       "Metal"),
    ("HINDALCO",     "Hindalco Industries Ltd",             "Metal"),
    ("VEDL",         "Vedanta Ltd",                         "Metal"),
    ("COALINDIA",    "Coal India Ltd",                      "Metal"),
    ("SAIL",         "Steel Authority of India Ltd",        "Metal"),
    ("NMDC",         "NMDC Ltd",                            "Metal"),
    ("NATIONALUM",   "National Aluminium Co Ltd",           "Metal"),
    ("HINDCOPPER",   "Hindustan Copper Ltd",                "Metal"),
    ("JSWENERGY",    "JSW Energy Ltd",                      "Metal"),

    # ── CAPITAL GOODS & INDUSTRIALS ──
    ("LT",           "Larsen & Toubro Ltd",                 "Industrials"),
    ("SIEMENS",      "Siemens Ltd",                         "Industrials"),
    ("ABB",          "ABB India Ltd",                       "Industrials"),
    ("BEL",          "Bharat Electronics Ltd",              "Industrials"),
    ("HAL",          "Hindustan Aeronautics Ltd",           "Industrials"),
    ("BHEL",         "Bharat Heavy Electricals Ltd",        "Industrials"),
    ("CUMMINSIND",   "Cummins India Ltd",                   "Industrials"),
    ("THERMAX",      "Thermax Ltd",                         "Industrials"),
    ("SCHAEFFLER",   "Schaeffler India Ltd",                "Industrials"),
    ("TIINDIA",      "Tube Investments of India Ltd",       "Industrials"),
    ("GRINDWELL",    "Grindwell Norton Ltd",                "Industrials"),
    ("CGPOWER",      "CG Power & Industrial Solutions",     "Industrials"),
    ("VOLTAS",       "Voltas Ltd",                          "Industrials"),
    ("BLUESTARCO",   "Blue Star Ltd",                       "Industrials"),
    ("HAVELLS",      "Havells India Ltd",                   "Industrials"),
    ("POLYCAB",      "Polycab India Ltd",                   "Industrials"),
    ("DIXON",        "Dixon Technologies India Ltd",        "Industrials"),

    # ── CEMENT ──
    ("ULTRACEMCO",   "UltraTech Cement Ltd",               "Cement"),
    ("GRASIM",       "Grasim Industries Ltd",               "Cement"),
    ("AMBUJACEM",    "Ambuja Cements Ltd",                  "Cement"),
    ("ACC",          "ACC Ltd",                             "Cement"),
    ("SHREECEM",     "Shree Cement Ltd",                    "Cement"),
    ("DALMIACEMENTB","Dalmia Bharat Ltd",                   "Cement"),
    ("JKCEMENT",     "JK Cement Ltd",                      "Cement"),

    # ── CONSUMER DURABLES / RETAIL ──
    ("TITAN",        "Titan Company Ltd",                   "Consumer"),
    ("TRENT",        "Trent Ltd",                           "Consumer"),
    ("DMART",        "Avenue Supermarts Ltd",               "Consumer"),
    ("ABFRL",        "Aditya Birla Fashion & Retail Ltd",   "Consumer"),
    ("NYKAA",        "FSN E-Commerce Ventures Ltd",        "Consumer"),
    ("KALYANKJIL",   "Kalyan Jewellers India Ltd",          "Consumer"),
    ("RAJESHEXPO",   "Rajesh Exports Ltd",                  "Consumer"),

    # ── TELECOM / MEDIA ──
    ("BHARTIARTL",   "Bharti Airtel Ltd",                   "Telecom"),
    ("IDEA",         "Vodafone Idea Ltd",                   "Telecom"),
    ("INDUSTOWER",   "Indus Towers Ltd",                    "Telecom"),
    ("ZEEL",         "Zee Entertainment Enterprises Ltd",   "Media"),
    ("PVRINOX",      "PVR Inox Ltd",                        "Media"),

    # ── REAL ESTATE ──
    ("DLF",          "DLF Ltd",                             "Realty"),
    ("GODREJPROP",   "Godrej Properties Ltd",               "Realty"),
    ("PRESTIGE",     "Prestige Estates Projects Ltd",       "Realty"),
    ("OBEROIRLTY",   "Oberoi Realty Ltd",                   "Realty"),
    ("BRIGADE",      "Brigade Enterprises Ltd",             "Realty"),
    ("SOBHA",        "Sobha Ltd",                           "Realty"),
    ("PHOENIXLTD",   "Phoenix Mills Ltd",                   "Realty"),
    ("MACROTECH",    "Macrotech Developers Ltd",            "Realty"),

    # ── CHEMICALS ──
    ("PIDILITIND",   "Pidilite Industries Ltd",             "Chemicals"),
    ("ATUL",         "Atul Ltd",                            "Chemicals"),
    ("NAVINFLUOR",   "Navin Fluorine Intl Ltd",             "Chemicals"),
    ("DEEPAKNTR",    "Deepak Nitrite Ltd",                  "Chemicals"),
    ("FLUOROCHEM",   "Gujarat Fluorochemicals Ltd",         "Chemicals"),
    ("SRF",          "SRF Ltd",                             "Chemicals"),
    ("TATACHEM",     "Tata Chemicals Ltd",                  "Chemicals"),
    ("AARTIIND",     "Aarti Industries Ltd",                "Chemicals"),
    ("ALKYLAMINE",   "Alkyl Amines Chemicals Ltd",          "Chemicals"),

    # ── PAINTS / MATERIALS ──
    ("ASIANPAINT",   "Asian Paints Ltd",                    "Materials"),
    ("BERGEPAINT",   "Berger Paints India Ltd",             "Materials"),
    ("KANSAINER",    "Kansai Nerolac Paints Ltd",           "Materials"),

    # ── NEW-AGE / INTERNET ──
    ("ZOMATO",       "Zomato Ltd",                          "Internet"),
    ("PAYTM",        "One97 Communications Ltd",            "Internet"),
    ("POLICYBZR",    "PB Fintech Ltd",                      "Internet"),
    ("IRCTC",        "Indian Railway Catering & Tourism",   "Internet"),
    ("CARTRADE",     "CarTrade Tech Ltd",                   "Internet"),

    # ── INFRASTRUCTURE / LOGISTICS ──
    ("GMRINFRA",     "GMR Airports Infrastructure Ltd",     "Infra"),
    ("IRB",          "IRB Infrastructure Developers Ltd",   "Infra"),
    ("CONCOR",       "Container Corporation of India Ltd",  "Infra"),
    ("VBL",          "Varun Beverages Ltd",                 "Infra"),
    ("CAMS",         "Computer Age Management Svcs Ltd",    "Infra"),
    ("BSE",          "BSE Ltd",                             "Infra"),

    # ── AGRICULTURE / MISC ──
    ("UPL",          "UPL Ltd",                             "Agri"),
    ("PIIND",        "PI Industries Ltd",                   "Agri"),
    ("RALLIS",       "Rallis India Ltd",                    "Agri"),
    ("COROMANDEL",   "Coromandel International Ltd",       "Agri"),
    ("CHAMBLFERT",   "Chambal Fertilizers & Chemicals",    "Agri"),
    ("GNFC",         "Gujarat Narmada Valley Fertilizers",  "Agri"),
]


def get_symbol_set() -> set[str]:
    """Return the set of all Nifty 200 symbols."""
    return {s[0] for s in NIFTY_200_STOCKS}


def populate_stocks_from_instruments(instruments_path: Path = None) -> int:
    """
    Cross-reference the hardcoded stock list with the live Upstox instruments
    JSON file to enrich each stock with its instrument_key and ISIN.

    Returns the number of stocks successfully upserted into the DB.
    """
    instruments_path = instruments_path or config.INSTRUMENTS_FILE
    if not instruments_path.exists():
        logger.warning("Instruments file not found at %s — skipping enrichment", instruments_path)
        return _populate_stocks_basic()

    logger.info("Loading instruments from %s …", instruments_path)
    with open(instruments_path) as f:
        instruments = json.load(f)

    # Build a lookup: trading_symbol → instrument record (NSE_EQ segment only)
    symbol_map: dict[str, dict] = {}
    for inst in instruments:
        if inst.get("segment") == "NSE_EQ" and inst.get("instrument_type") == "EQ":
            sym = inst.get("trading_symbol", "").strip()
            if sym:
                symbol_map[sym] = inst

    count = 0
    not_found = []
    for symbol, name, sector in NIFTY_200_STOCKS:
        inst = symbol_map.get(symbol)
        if inst:
            db.upsert_stock(
                symbol=symbol,
                name=name,
                sector=sector,
                instrument_key=inst["instrument_key"],
                isin=inst.get("isin"),
            )
            count += 1
        else:
            # Try with common alternate symbol formats
            alt = symbol.replace("-", "")
            inst = symbol_map.get(alt)
            if inst:
                db.upsert_stock(symbol, name, sector, inst["instrument_key"], inst.get("isin"))
                count += 1
            else:
                not_found.append(symbol)
                # Still insert with a placeholder key so the system can run
                db.upsert_stock(symbol, name, sector, f"NSE_EQ|{symbol}", None)

    if not_found:
        logger.warning(
            "%d symbols not found in instruments file (placeholders used): %s",
            len(not_found), not_found,
        )
    logger.info("Populated %d/%d stocks in DB", count, len(NIFTY_200_STOCKS))
    return count


def _populate_stocks_basic() -> int:
    """Fallback: insert stocks with placeholder instrument keys."""
    count = 0
    for symbol, name, sector in NIFTY_200_STOCKS:
        db.upsert_stock(symbol, name, sector, f"NSE_EQ|{symbol}", None)
        count += 1
    logger.info("Populated %d stocks with placeholder keys", count)
    return count
