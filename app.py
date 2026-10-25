"""
Relative PPP Explorer — Streamlit app built on IMF data (SDMX 3.0 API).

Definitions
-----------
S(t)   : units of Country 2 currency per 1 unit of Country 1 currency
P1, P2 : monthly CPI of Country 1 and Country 2 (IMF CPI, all items, index)
RER(t) = S(t) * P1(t) / P2(t)
RER*   = average of RER(t) over the valid months of the selected sample
Real Index(t) = 100 * RER(t) / RER*

Data: IMF SDMX 3.0 API (https://api.imf.org/external/sdmx/3.0)
  CPI : dataflow IMF.STA/CPI, key {ISO3}.CPI._T.IX.M
  FX  : dataflow IMF.STA/ER,  key {ISO3}.XDC_USD.PA_RT.M  (national currency per USD)
  Trade (balanced-trade benchmark): dataflow IMF.STA/IMTS,
        keys {REPORTER}.XG_FOB_USD.{PARTNER}.M and {REPORTER}.MG_CIF_USD.{PARTNER}.M
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------
BASE_URL = "https://api.imf.org/external/sdmx/3.0"
AGENCY = "IMF.STA"
FIRST_MONTH = pd.Timestamp("1976-01-01")
EURO_AREA = "G163"
CACHE_TTL_SECONDS = 12 * 3600
USER_AGENT = "relative-ppp-explorer/1.0 (Streamlit app; python-requests)"

st.set_page_config(page_title="Relative PPP Explorer", page_icon="💱", layout="wide")

# (ISO3 / IMF code, display name, currency code)
COUNTRIES: list[tuple[str, str, str]] = [
    (EURO_AREA, "Euro area", "EUR"),
    ("AFG", "Afghanistan", "AFN"), ("ALB", "Albania", "ALL"), ("DZA", "Algeria", "DZD"),
    ("AND", "Andorra", "EUR"), ("AGO", "Angola", "AOA"), ("ATG", "Antigua and Barbuda", "XCD"),
    ("ARG", "Argentina", "ARS"), ("ARM", "Armenia", "AMD"), ("ABW", "Aruba", "AWG"),
    ("AUS", "Australia", "AUD"), ("AUT", "Austria", "EUR"), ("AZE", "Azerbaijan", "AZN"),
    ("BHS", "Bahamas", "BSD"), ("BHR", "Bahrain", "BHD"), ("BGD", "Bangladesh", "BDT"),
    ("BRB", "Barbados", "BBD"), ("BLR", "Belarus", "BYN"), ("BEL", "Belgium", "EUR"),
    ("BLZ", "Belize", "BZD"), ("BEN", "Benin", "XOF"), ("BTN", "Bhutan", "BTN"),
    ("BOL", "Bolivia", "BOB"), ("BIH", "Bosnia and Herzegovina", "BAM"), ("BWA", "Botswana", "BWP"),
    ("BRA", "Brazil", "BRL"), ("BRN", "Brunei Darussalam", "BND"), ("BGR", "Bulgaria", "BGN/EUR"),
    ("BFA", "Burkina Faso", "XOF"), ("BDI", "Burundi", "BIF"), ("CPV", "Cabo Verde", "CVE"),
    ("KHM", "Cambodia", "KHR"), ("CMR", "Cameroon", "XAF"), ("CAN", "Canada", "CAD"),
    ("CAF", "Central African Republic", "XAF"), ("TCD", "Chad", "XAF"), ("CHL", "Chile", "CLP"),
    ("CHN", "China", "CNY"), ("COL", "Colombia", "COP"), ("COM", "Comoros", "KMF"),
    ("COD", "Congo, Dem. Rep.", "CDF"), ("COG", "Congo, Rep.", "XAF"), ("CRI", "Costa Rica", "CRC"),
    ("CIV", "Côte d'Ivoire", "XOF"), ("HRV", "Croatia", "EUR"), ("CYP", "Cyprus", "EUR"),
    ("CZE", "Czech Republic", "CZK"), ("DNK", "Denmark", "DKK"), ("DJI", "Djibouti", "DJF"),
    ("DMA", "Dominica", "XCD"), ("DOM", "Dominican Republic", "DOP"), ("ECU", "Ecuador", "USD"),
    ("EGY", "Egypt", "EGP"), ("SLV", "El Salvador", "USD"), ("GNQ", "Equatorial Guinea", "XAF"),
    ("ERI", "Eritrea", "ERN"), ("EST", "Estonia", "EUR"), ("SWZ", "Eswatini", "SZL"),
    ("ETH", "Ethiopia", "ETB"), ("FJI", "Fiji", "FJD"), ("FIN", "Finland", "EUR"),
    ("FRA", "France", "EUR"), ("GAB", "Gabon", "XAF"), ("GMB", "Gambia", "GMD"),
    ("GEO", "Georgia", "GEL"), ("DEU", "Germany", "EUR"), ("GHA", "Ghana", "GHS"),
    ("GRC", "Greece", "EUR"), ("GRD", "Grenada", "XCD"), ("GTM", "Guatemala", "GTQ"),
    ("GIN", "Guinea", "GNF"), ("GNB", "Guinea-Bissau", "XOF"), ("GUY", "Guyana", "GYD"),
    ("HTI", "Haiti", "HTG"), ("HND", "Honduras", "HNL"), ("HKG", "Hong Kong SAR", "HKD"),
    ("HUN", "Hungary", "HUF"), ("ISL", "Iceland", "ISK"), ("IND", "India", "INR"),
    ("IDN", "Indonesia", "IDR"), ("IRN", "Iran", "IRR"), ("IRQ", "Iraq", "IQD"),
    ("IRL", "Ireland", "EUR"), ("ISR", "Israel", "ILS"), ("ITA", "Italy", "EUR"),
    ("JAM", "Jamaica", "JMD"), ("JPN", "Japan", "JPY"), ("JOR", "Jordan", "JOD"),
    ("KAZ", "Kazakhstan", "KZT"), ("KEN", "Kenya", "KES"), ("KIR", "Kiribati", "AUD"),
    ("KOR", "Korea", "KRW"), ("KWT", "Kuwait", "KWD"), ("KGZ", "Kyrgyz Republic", "KGS"),
    ("LAO", "Lao P.D.R.", "LAK"), ("LVA", "Latvia", "EUR"), ("LBN", "Lebanon", "LBP"),
    ("LSO", "Lesotho", "LSL"), ("LBR", "Liberia", "LRD"), ("LBY", "Libya", "LYD"),
    ("LIE", "Liechtenstein", "CHF"), ("LTU", "Lithuania", "EUR"), ("LUX", "Luxembourg", "EUR"),
    ("MAC", "Macao SAR", "MOP"), ("MDG", "Madagascar", "MGA"), ("MWI", "Malawi", "MWK"),
    ("MYS", "Malaysia", "MYR"), ("MDV", "Maldives", "MVR"), ("MLI", "Mali", "XOF"),
    ("MLT", "Malta", "EUR"), ("MHL", "Marshall Islands", "USD"), ("MRT", "Mauritania", "MRU"),
    ("MUS", "Mauritius", "MUR"), ("MEX", "Mexico", "MXN"), ("FSM", "Micronesia", "USD"),
    ("MDA", "Moldova", "MDL"), ("MNG", "Mongolia", "MNT"), ("MNE", "Montenegro", "EUR"),
    ("MAR", "Morocco", "MAD"), ("MOZ", "Mozambique", "MZN"), ("MMR", "Myanmar", "MMK"),
    ("NAM", "Namibia", "NAD"), ("NRU", "Nauru", "AUD"), ("NPL", "Nepal", "NPR"),
    ("NLD", "Netherlands", "EUR"), ("NZL", "New Zealand", "NZD"), ("NIC", "Nicaragua", "NIO"),
    ("NER", "Niger", "XOF"), ("NGA", "Nigeria", "NGN"), ("MKD", "North Macedonia", "MKD"),
    ("NOR", "Norway", "NOK"), ("OMN", "Oman", "OMR"), ("PAK", "Pakistan", "PKR"),
    ("PLW", "Palau", "USD"), ("PAN", "Panama", "PAB"), ("PNG", "Papua New Guinea", "PGK"),
    ("PRY", "Paraguay", "PYG"), ("PER", "Peru", "PEN"), ("PHL", "Philippines", "PHP"),
    ("POL", "Poland", "PLN"), ("PRT", "Portugal", "EUR"), ("QAT", "Qatar", "QAR"),
    ("ROU", "Romania", "RON"), ("RUS", "Russia", "RUB"), ("RWA", "Rwanda", "RWF"),
    ("WSM", "Samoa", "WST"), ("SMR", "San Marino", "EUR"), ("STP", "São Tomé and Príncipe", "STN"),
    ("SAU", "Saudi Arabia", "SAR"), ("SEN", "Senegal", "XOF"), ("SRB", "Serbia", "RSD"),
    ("SYC", "Seychelles", "SCR"), ("SLE", "Sierra Leone", "SLE"), ("SGP", "Singapore", "SGD"),
    ("SVK", "Slovak Republic", "EUR"), ("SVN", "Slovenia", "EUR"), ("SLB", "Solomon Islands", "SBD"),
    ("SOM", "Somalia", "SOS"), ("ZAF", "South Africa", "ZAR"), ("SSD", "South Sudan", "SSP"),
    ("ESP", "Spain", "EUR"), ("LKA", "Sri Lanka", "LKR"), ("KNA", "St. Kitts and Nevis", "XCD"),
    ("LCA", "St. Lucia", "XCD"), ("VCT", "St. Vincent and the Grenadines", "XCD"),
    ("SDN", "Sudan", "SDG"), ("SUR", "Suriname", "SRD"), ("SWE", "Sweden", "SEK"),
    ("CHE", "Switzerland", "CHF"), ("SYR", "Syria", "SYP"), ("TJK", "Tajikistan", "TJS"),
    ("TZA", "Tanzania", "TZS"), ("THA", "Thailand", "THB"), ("TLS", "Timor-Leste", "USD"),
    ("TGO", "Togo", "XOF"), ("TON", "Tonga", "TOP"), ("TTO", "Trinidad and Tobago", "TTD"),
    ("TUN", "Tunisia", "TND"), ("TUR", "Türkiye", "TRY"), ("TKM", "Turkmenistan", "TMT"),
    ("TUV", "Tuvalu", "AUD"), ("UGA", "Uganda", "UGX"), ("UKR", "Ukraine", "UAH"),
    ("ARE", "United Arab Emirates", "AED"), ("GBR", "United Kingdom", "GBP"),
    ("USA", "United States", "USD"), ("URY", "Uruguay", "UYU"), ("UZB", "Uzbekistan", "UZS"),
    ("VUT", "Vanuatu", "VUV"), ("VEN", "Venezuela", "VES"), ("VNM", "Vietnam", "VND"),
    ("YEM", "Yemen", "YER"), ("ZMB", "Zambia", "ZMW"), ("ZWE", "Zimbabwe", "ZWG"),
]
COUNTRY_NAME = {c: n for c, n, _ in COUNTRIES}
COUNTRY_CCY = {c: k for c, _, k in COUNTRIES}
CODES_SORTED = [EURO_AREA] + sorted([c for c, _, _ in COUNTRIES if c != EURO_AREA], key=lambda c: COUNTRY_NAME[c])


def country_label(code: str) -> str:
    return f"{COUNTRY_NAME.get(code, code)} ({code}, {COUNTRY_CCY.get(code, '?')})"


# --------------------------------------------------------------------------------------
# IMF API access and parsing
# --------------------------------------------------------------------------------------
class TransientFetchError(Exception):
    """Network / server problem. Raised (not cached) so that a later rerun can retry."""


_PERIOD_RE = re.compile(r"^\s*(\d{4})\s*[-/]?\s*M?(\d{1,2})(?:[-/]\d{1,2})?\s*$", re.IGNORECASE)


def parse_period(value) -> pd.Timestamp:
    """Convert IMF monthly periods ('2024-M01', '2024-01', '2024M1') to month-start Timestamps."""
    m = _PERIOD_RE.match(str(value))
    if not m:
        return pd.NaT
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        return pd.NaT
    return pd.Timestamp(year=year, month=month, day=1)


def _to_series(records: dict) -> pd.Series:
    if not records:
        return pd.Series(dtype="float64")
    s = pd.Series(records, dtype="object")
    s.index = [parse_period(p) for p in s.index]
    s = pd.to_numeric(s, errors="coerce")
    s = s[~pd.isna(s.index)]
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.astype("float64")


def _code_of(v):
    if isinstance(v, dict):
        return v.get("id", v.get("value", v.get("start")))
    return v


def parse_sdmx_json(payload: dict) -> pd.Series:
    """Parse SDMX-JSON (2.0 or 1.0) into a monthly Series. Keeps the longest series returned."""
    data = payload.get("data", payload)
    structures = data.get("structures", data.get("structure"))
    structure = structures[0] if isinstance(structures, list) and structures else (structures or {})
    dims = structure.get("dimensions", {}) if isinstance(structure, dict) else {}
    obs_dims = dims.get("observation", []) or []
    if not obs_dims:
        return pd.Series(dtype="float64")
    time_pos = next((i for i, d in enumerate(obs_dims) if d.get("id") == "TIME_PERIOD"), 0)
    times = [_code_of(v) for v in (obs_dims[time_pos].get("values") or [])]

    def obs_to_records(obs: dict, pos: int) -> dict:
        rec = {}
        for k, v in (obs or {}).items():
            parts = str(k).split(":")
            try:
                t = times[int(parts[pos] if len(parts) > 1 else parts[0])]
            except (ValueError, IndexError):
                continue
            rec[t] = v[0] if isinstance(v, list) and v else (None if isinstance(v, list) else v)
        return rec

    best = pd.Series(dtype="float64")
    for ds in data.get("dataSets", []) or []:
        candidates = []
        for sobj in (ds.get("series") or {}).values():
            candidates.append(_to_series(obs_to_records(sobj.get("observations"), 0)))
        if ds.get("observations"):
            candidates.append(_to_series(obs_to_records(ds.get("observations"), time_pos)))
        for s in candidates:
            if s.notna().sum() > best.notna().sum():
                best = s
    return best


def parse_sdmx_csv(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text), dtype=str)
    if "TIME_PERIOD" not in df.columns or "OBS_VALUE" not in df.columns:
        return pd.Series(dtype="float64")
    return _to_series(dict(zip(df["TIME_PERIOD"], df["OBS_VALUE"])))


def parse_sdmx_xml(text: str) -> pd.Series:
    root = ET.fromstring(text)
    rec = {}
    for el in root.iter():
        if "TIME_PERIOD" in el.attrib and "OBS_VALUE" in el.attrib:
            rec[el.attrib["TIME_PERIOD"]] = el.attrib["OBS_VALUE"]
    return _to_series(rec)


def parse_response(text: str, content_type: str) -> pd.Series:
    stripped = text.lstrip()
    if not stripped:
        return pd.Series(dtype="float64")
    if "json" in content_type or stripped.startswith("{"):
        return parse_sdmx_json(json.loads(stripped))
    if "csv" in content_type or stripped.upper().startswith("STRUCTURE"):
        return parse_sdmx_csv(text)
    if stripped.startswith("<"):
        return parse_sdmx_xml(stripped)
    return pd.Series(dtype="float64")


def _api_key() -> str | None:
    key = os.environ.get("IMF_API_KEY")
    if key:
        return key
    try:
        return st.secrets.get("IMF_API_KEY")  # optional; Secrets can stay empty
    except Exception:
        return None


@st.cache_resource(show_spinner=False)
def _http_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3, connect=3, read=3, backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False, respect_retry_after_header=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _fetch_cached(flow: str, key: str) -> tuple[pd.Series, int, str]:
    """Fetch one IMF series. Returns (series, http_status, url). Empty series = no data."""
    url = f"{BASE_URL}/data/dataflow/{AGENCY}/{flow}/+/{key}"
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    api_key = _api_key()
    if api_key:
        headers["Ocp-Apim-Subscription-Key"] = api_key
    try:
        r = _http_session().get(url, params={"dimensionAtObservation": "TIME_PERIOD"},
                                headers=headers, timeout=60)
    except requests.RequestException as exc:
        raise TransientFetchError(f"{type(exc).__name__}: {exc}") from exc
    if r.status_code in (404, 400, 422):  # IMF answers 404 (or 400 for invalid codes) when there is no data
        return pd.Series(dtype="float64"), r.status_code, r.url
    if r.status_code != 200:
        raise TransientFetchError(f"HTTP {r.status_code} from {r.url}: {r.text[:300]}")
    try:
        series = parse_response(r.text, r.headers.get("Content-Type", "").lower())
    except Exception as exc:  # malformed payload
        raise TransientFetchError(f"Could not parse IMF response from {r.url}: {exc}") from exc
    series = series[series.index >= FIRST_MONTH]
    return series, r.status_code, r.url


def fetch(flow: str, key: str, log: list) -> pd.Series:
    """Fetch with logging; never raises. Network failures are logged as errors."""
    try:
        s, status, url = _fetch_cached(flow, key)
        ok = s.notna().sum()
        log.append({
            "dataflow": flow, "key": key, "status": status, "observations": int(ok),
            "first": s.dropna().index.min().strftime("%Y-%m") if ok else "",
            "last": s.dropna().index.max().strftime("%Y-%m") if ok else "",
            "error": "" if ok else "no data returned", "url": url,
        })
        return s
    except TransientFetchError as exc:
        log.append({"dataflow": flow, "key": key, "status": "error", "observations": 0,
                    "first": "", "last": "", "error": str(exc)[:400],
                    "url": f"{BASE_URL}/data/dataflow/{AGENCY}/{flow}/+/{key}"})
        return pd.Series(dtype="float64")


def get_cpi(code: str, log: list, notes: list) -> pd.Series:
    candidates = [("CPI", f"{code}.CPI._T.IX.M")]
    if code == EURO_AREA:
        candidates += [("CPI", f"{code}.HICP._T.IX.M"), ("CPI_WCA", f"{code}.CPI._T.IX.M"),
                       ("CPI_WCA", f"{code}.HICP._T.IX.M")]
    else:
        candidates += [("CPI", f"{code}.HICP._T.IX.M")]
    for i, (flow, key) in enumerate(candidates):
        s = fetch(flow, key, log)
        if s.notna().sum() > 0:
            if i > 0:
                notes.append(f"CPI for {COUNTRY_NAME.get(code, code)} taken from fallback series {flow}/{key}.")
            return s
    return pd.Series(dtype="float64")


def get_usd_rate(code: str, log: list, notes: list) -> tuple[pd.Series | None, bool]:
    """National currency units per USD. Returns (series, is_constant_one)."""
    name = COUNTRY_NAME.get(code, code)
    if code == "USA":
        return None, True
    s = fetch("ER", f"{code}.XDC_USD.PA_RT.M", log)
    if s.notna().sum() > 0:
        return s, False
    inv = fetch("ER", f"{code}.USD_XDC.PA_RT.M", log)
    if inv.notna().sum() > 0:
        notes.append(f"Exchange rate for {name} computed as 1 / (USD per national currency).")
        return 1.0 / inv.where(inv > 0), False
    eop = fetch("ER", f"{code}.XDC_USD.EOP_RT.M", log)
    if eop.notna().sum() > 0:
        notes.append(f"Period-average exchange rate unavailable for {name}; end-of-period rate used instead.")
        return eop, False
    if code == EURO_AREA:
        deu = fetch("ER", "DEU.XDC_USD.PA_RT.M", log)
        deu = deu[deu.index >= pd.Timestamp("1999-01-01")]
        if deu.notna().sum() > 0:
            notes.append("Euro-area exchange rate not found under G163; EUR per USD taken from the Germany "
                         "series from January 1999 onward (the euro's start), so euro-area results begin in 1999.")
            return deu, False
    if COUNTRY_CCY.get(code) == "USD":
        notes.append(f"No exchange-rate series for {name}; it uses the US dollar, so 1 USD per USD is assumed.")
        return None, True
    return pd.Series(dtype="float64"), False


def get_bilateral_trade(c1: str, c2: str, log: list) -> tuple[pd.DataFrame | None, str]:
    """Goods exports of C1 to C2 and imports of C1 from C2 (USD, as reported by the IMF)."""
    options = []
    x = fetch("IMTS", f"{c1}.XG_FOB_USD.{c2}.M", log)
    m = fetch("IMTS", f"{c1}.MG_CIF_USD.{c2}.M", log)
    if x.notna().sum() and m.notna().sum():
        d = pd.concat({"exports": x, "imports": m}, axis=1).dropna()
        options.append((len(d), f"reported by {COUNTRY_NAME.get(c1, c1)}", d))
    if not options or options[0][0] < 36:
        # Mirror data: C2's imports from C1 = C1's exports to C2, and vice versa.
        mx = fetch("IMTS", f"{c2}.MG_CIF_USD.{c1}.M", log)
        mm = fetch("IMTS", f"{c2}.XG_FOB_USD.{c1}.M", log)
        if mx.notna().sum() and mm.notna().sum():
            d = pd.concat({"exports": mx, "imports": mm}, axis=1).dropna()
            options.append((len(d), f"mirror data reported by {COUNTRY_NAME.get(c2, c2)}", d))
    if not options:
        return None, ""
    n, label, d = max(options, key=lambda o: o[0])
    if n < 12:
        return None, ""
    return d, label


def load_pair(c1: str, c2: str) -> dict:
    """Fetch and assemble every input series for the pair (each IMF series is cached individually)."""
    log: list = []
    notes: list = []
    p1 = get_cpi(c1, log, notes)
    p2 = get_cpi(c2, log, notes)
    fx1, one1 = get_usd_rate(c1, log, notes)
    fx2, one2 = get_usd_rate(c2, log, notes)
    trade, trade_src = get_bilateral_trade(c1, c2, log)

    missing = []
    if p1.notna().sum() == 0:
        missing.append(f"CPI for {COUNTRY_NAME[c1]}")
    if p2.notna().sum() == 0:
        missing.append(f"CPI for {COUNTRY_NAME[c2]}")
    if not one1 and (fx1 is None or fx1.notna().sum() == 0):
        missing.append(f"exchange rate for {COUNTRY_NAME[c1]}")
    if not one2 and (fx2 is None or fx2.notna().sum() == 0):
        missing.append(f"exchange rate for {COUNTRY_NAME[c2]}")
    if missing:
        return {"df": None, "missing": missing, "log": log, "notes": notes, "trade_src": ""}

    parts = [s.dropna().index for s in (p1, p2, fx1, fx2) if s is not None and s.notna().sum()]
    last = max(ix.max() for ix in parts)
    idx = pd.date_range(FIRST_MONTH, last, freq="MS")
    df = pd.DataFrame(index=idx)
    df.index.name = "month"
    df["fx1_per_usd"] = 1.0 if one1 else fx1.reindex(idx)
    df["fx2_per_usd"] = 1.0 if one2 else fx2.reindex(idx)
    df["p1"] = p1.reindex(idx)
    df["p2"] = p2.reindex(idx)
    df["S"] = df["fx2_per_usd"] / df["fx1_per_usd"]  # C2 per USD / C1 per USD = C2 per C1
    df["RER"] = df["S"] * df["p1"] / df["p2"]
    cols = ["fx1_per_usd", "fx2_per_usd", "p1", "p2", "S", "RER"]
    df["valid"] = np.isfinite(df[cols]).all(axis=1) & (df[cols] > 0).all(axis=1)
    df.loc[~df["valid"], "RER"] = np.nan
    if trade is not None:
        t = trade.reindex(idx)
        df["exports_c1_to_c2"] = t["exports"]
        df["imports_c1_from_c2"] = t["imports"]
        x12 = df["exports_c1_to_c2"].rolling(12, min_periods=12).sum()
        m12 = df["imports_c1_from_c2"].rolling(12, min_periods=12).sum()
        df["trade_balance_ratio_12m"] = (x12 - m12) / (x12 + m12)
    else:
        df["exports_c1_to_c2"] = np.nan
        df["imports_c1_from_c2"] = np.nan
        df["trade_balance_ratio_12m"] = np.nan

    if not df["valid"].any():
        return {"df": None, "missing": ["any month where CPI and exchange rates overlap for both countries"],
                "log": log, "notes": notes, "trade_src": trade_src}
    first_valid, last_valid = df.index[df["valid"]].min(), df.index[df["valid"]].max()
    df = df.loc[first_valid:last_valid]
    return {"df": df, "missing": [], "log": log, "notes": notes, "trade_src": trade_src}


# --------------------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------------------
def fmt_num(x: float) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.0f}"
    if ax >= 1:
        return f"{x:,.4f}"
    if ax >= 1e-4:
        return f"{x:.6f}"
    return f"{x:.4e}"


def fmt_pct(x: float, signed: bool = True) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x:+.1%}" if signed else f"{x:.1%}"


def ym(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m")


# --------------------------------------------------------------------------------------
# Sidebar: country selection
# --------------------------------------------------------------------------------------
st.sidebar.header("Currency pair")
c1 = st.sidebar.selectbox("Country 1 (base currency)", CODES_SORTED,
                          index=CODES_SORTED.index("GBR"), format_func=country_label)
c2 = st.sidebar.selectbox("Country 2 (quote currency)", CODES_SORTED,
                          index=CODES_SORTED.index("USA"), format_func=country_label)

st.title("💱 Relative PPP Explorer")
st.caption("Monthly IMF CPI and exchange-rate data (from January 1976 where available) · "
           "real exchange rate, relative-PPP index and fair-value benchmarks.")

if c1 == c2:
    st.error("Please choose two different economies.")
    st.stop()

ccy1, ccy2 = COUNTRY_CCY[c1], COUNTRY_CCY[c2]
name1, name2 = COUNTRY_NAME[c1], COUNTRY_NAME[c2]
s_unit = f"{ccy2} per {ccy1}"

with st.spinner(f"Downloading IMF data for {name1} and {name2}…"):
    bundle = load_pair(c1, c2)

full = bundle["df"]
log_df = pd.DataFrame(bundle["log"])
if not log_df.empty:
    log_df["status"] = log_df["status"].astype(str)

if full is None:
    st.error("Not enough IMF data to build this pair. Missing: " + "; ".join(bundle["missing"]) + ".")
    if not log_df.empty and (log_df["status"] == "error").any():
        st.warning("Some requests to the IMF API failed (network, rate limit or server error). "
                   "Wait a minute and reload the page; see the diagnostics below for the exact messages.")
    with st.expander("Diagnostics: IMF requests", expanded=True):
        st.dataframe(log_df, hide_index=True)
    st.stop()

for note in bundle["notes"]:
    st.info(note)

# --------------------------------------------------------------------------------------
# Sidebar: sample and benchmark settings
# --------------------------------------------------------------------------------------
months = [ym(t) for t in full.index]
st.sidebar.header("Sample period")
start_lbl, end_lbl = st.sidebar.select_slider(
    "Start and end month (RER* is the average over this window)",
    options=months, value=(months[0], months[-1]), key=f"sample_{c1}_{c2}",
)
if months.index(start_lbl) > months.index(end_lbl):
    start_lbl, end_lbl = end_lbl, start_lbl
start_ts, end_ts = pd.Timestamp(start_lbl + "-01"), pd.Timestamp(end_lbl + "-01")

st.sidebar.header("Benchmarks")
has_trade = full["trade_balance_ratio_12m"].notna().any()
with st.sidebar.expander("Balanced-trade benchmark settings", expanded=False):
    trade_threshold = st.slider("Max. bilateral imbalance counted as 'balanced' (± % of total trade)",
                                1, 20, 5, key="trade_thr") / 100.0
    trade_method = st.radio("Which balanced moment?",
                            ["Most recent balanced month", "Month with the smallest imbalance"],
                            key="trade_method")
use_anchor = st.sidebar.checkbox("Add a custom anchor month as an extra benchmark", value=not has_trade)
anchor_lbl = None
if use_anchor:
    valid_months = [ym(t) for t in full.index[full["valid"]]]
    anchor_lbl = st.sidebar.selectbox("Anchor month", valid_months, index=0, key=f"anchor_{c1}_{c2}")

# --------------------------------------------------------------------------------------
# Core calculations
# --------------------------------------------------------------------------------------
sample = full.loc[start_ts:end_ts].copy()
valid_sample = sample[sample["valid"]]
if len(valid_sample) == 0:
    st.error("No valid months (with CPI and exchange rates for both countries) in the selected sample.")
    st.stop()
if len(valid_sample) < 24:
    st.warning(f"Only {len(valid_sample)} valid months in the sample — benchmarks will be noisy.")

rer_star = float(valid_sample["RER"].mean())
full["real_index"] = 100.0 * full["RER"] / rer_star
full["ppp_implied_S"] = rer_star * full["p2"] / full["p1"]  # S at which Real Index = 100
sample = full.loc[start_ts:end_ts].copy()
valid_sample = sample[sample["valid"]]

benchmarks: list[dict] = []  # each: name, index, detail, color, dash

# 1) Sample mean (by construction 100)
benchmarks.append({"name": f"Sample average {start_lbl} to {end_lbl}", "short": "Sample average",
                   "index": 100.0, "detail": f"{len(valid_sample)} valid months", "color": "#6b7280",
                   "dash": "solid", "key": "sample", "label": "Sample avg",
                   "help": "Average real rate over the selected sample (RER*). By construction it equals 100."})

# 2) Last-30-years average (360 months ending at the latest valid month available)
last_valid = full.index[full["valid"]].max()
win30_start = last_valid - pd.DateOffset(months=359)
win30 = full.loc[win30_start:last_valid]
win30 = win30[win30["valid"]]
bench30 = None
if len(win30):
    years_cov = len(win30) / 12
    bench30 = 100.0 * float(win30["RER"].mean()) / rer_star
    detail = f"{ym(win30.index.min())} to {ym(last_valid)}"
    if years_cov < 29.5:
        detail += f" (only {years_cov:.1f} years of valid data available)"
    benchmarks.append({"name": "Average of the last 30 years", "short": "30-year average",
                       "index": bench30, "detail": detail, "color": "#d97706", "dash": "dash",
                       "key": "30y", "label": "30-yr avg",
                       "help": f"Average real rate over the last 30 years of data ({detail}), on the index scale."})

# 3) Balanced-trade moment
bench_trade = None
trade_window = None
trade_note = ""
if has_trade:
    ratio = full["trade_balance_ratio_12m"].where(full["valid"])
    ratio = ratio.dropna()
    if len(ratio):
        balanced = ratio[ratio.abs() <= trade_threshold]
        if trade_method.startswith("Most recent") and len(balanced):
            chosen = balanced.index.max()
        else:
            chosen = ratio.abs().idxmin()
            if trade_method.startswith("Most recent"):
                trade_note = (f"No month had an imbalance within ±{trade_threshold:.0%}; "
                              "the month with the smallest imbalance is used instead.")
        w = full.loc[chosen - pd.DateOffset(months=11):chosen]
        w = w[w["valid"]]
        if len(w) >= 6:
            bench_trade = 100.0 * float(w["RER"].mean()) / rer_star
            trade_window = (w.index.min(), w.index.max())
            benchmarks.append({
                "name": "Balanced bilateral trade moment", "short": "Balanced-trade moment",
                "index": bench_trade,
                "detail": (f"12 months to {ym(chosen)}; 12-month bilateral imbalance "
                           f"{fmt_pct(float(ratio.loc[chosen]))} of total trade ({bundle['trade_src']})"),
                "color": "#059669", "dash": "dot", "key": "trade", "label": "Balanced trade",
                "help": (f"Average real rate over the 12 months to {ym(chosen)}, when bilateral goods trade "
                         f"was balanced (imbalance {fmt_pct(float(ratio.loc[chosen]))} of total trade).")})
else:
    trade_note = ("IMF bilateral trade data (IMTS) were not available for this pair, so the balanced-trade "
                  "benchmark cannot be computed. A custom anchor month has been enabled instead "
                  "(choose a period you consider 'balanced' in the sidebar).")

# 4) Custom anchor month
if use_anchor and anchor_lbl:
    a_ts = pd.Timestamp(anchor_lbl + "-01")
    benchmarks.append({"name": f"Custom anchor month {anchor_lbl}", "short": "Custom anchor",
                       "index": float(full.loc[a_ts, "real_index"]), "detail": "user-selected month",
                       "color": "#7c3aed", "dash": "dashdot", "key": "anchor", "label": "Anchor month",
                       "help": f"Real Index in the anchor month you chose ({anchor_lbl})."})

latest_ts = valid_sample.index.max()
latest = full.loc[latest_ts]
latest_idx = float(latest["real_index"])

# --------------------------------------------------------------------------------------
# Header, metric help
# --------------------------------------------------------------------------------------
st.markdown(
    f"**Pair:** {name1} ({ccy1}) vs {name2} ({ccy2}) · **S(t)** = {s_unit} · "
    f"**Sample:** {start_lbl} to {end_lbl} · **RER\\*** = {fmt_num(rer_star)}"
)

with st.expander("ℹ️ What do these metrics mean? (click to expand)", expanded=False):
    st.markdown(f"""
- **S(t), nominal exchange rate** — units of {ccy2} needed to buy one {ccy1}, computed from the IMF rates
  against the US dollar: S = ({ccy2} per USD) ÷ ({ccy1} per USD). A rising S means the {ccy1} buys more {ccy2}.
- **P1(t), P2(t)** — consumer price indices of {name1} and {name2}. Their base years differ, which does not
  matter because only changes over time are used.
- **RER(t) = S × P1 / P2, real exchange rate** — how many {name2} consumption baskets one {name1}
  basket buys, up to a constant. It moves when the nominal rate moves *more or less than* the inflation gap.
- **RER\\*** — the average RER over the sample you select. Relative PPP assumes the real rate reverts to a
  stable mean; RER\\* is that assumed "normal" level. It is recalculated whenever you change the dates.
- **Real Index(t) = 100 × RER / RER\\*** — the relative-PPP curve. **100 = sample average.** Above 100: the
  {ccy1} is *expensive / overvalued* against the {ccy2} relative to the sample norm (the {ccy2} is cheap).
  Below 100: the {ccy1} is *cheap / undervalued*. 110 means about 10% overvalued.
- **PPP-implied nominal rate = RER\\* × P2 / P1** — the value of S at which the Real Index would equal 100,
  i.e. the rate that would only offset the inflation difference. Compare it with the actual S.
- **30-year average benchmark** — the average RER of the latest 360 months of data, shown on the index scale.
  It is a long-run "fair value" that does not depend on your chosen sample start.
- **Balanced-trade benchmark** — the average RER over the 12 months ending at a moment when goods trade
  between the two economies was roughly balanced (12-month exports ≈ imports, within the tolerance you set).
  Imbalance = (exports − imports) ÷ (exports + imports), from {name1}'s point of view.
- **Misalignment vs a benchmark** = Real Index ÷ benchmark − 1. Positive: {ccy1} overvalued vs that
  benchmark; negative: {ccy1} undervalued.

**Reading the chart:** use the checkboxes on the right of the chart to add or remove each curve, benchmark
and background colour; each checkbox shows its own line style or colour, and hovering the **?** icon explains it.
The solid blue line is the Real Index; horizontal lines are benchmarks. In *Nominal* view the red line is S in
actual units ({s_unit}) and the dashed red line is the PPP-implied rate: actual above PPP-implied means the
{ccy1} is overvalued relative to the sample average, and vice versa.

**Background colours** classify each month against the two relative-PPP benchmarks (30-year average and
balanced-trade moment): **light red** = {ccy1} overvalued against both, **light blue** = {ccy1} undervalued
against both, **light yellow** = the two benchmarks disagree (overvalued against one, undervalued against the
other), so the valuation is undefined.
""")

# --------------------------------------------------------------------------------------
# Headline metrics
# --------------------------------------------------------------------------------------
m1, m2, m3, m4 = st.columns(4)
m1.metric(f"Real Index, {ym(latest_ts)}", f"{latest_idx:.1f}",
          f"{latest_idx - 100:+.1f} vs sample avg", delta_color="off")
m2.metric(f"Nominal S ({s_unit})", fmt_num(float(latest["S"])))
m3.metric("PPP-implied S (sample avg)", fmt_num(float(latest["ppp_implied_S"])))
if abs(latest_idx - 100) < 5:
    lead = "near fair value (±5%)"
else:
    lead = f"{ccy1} overvalued" if latest_idx > 100 else f"{ccy1} undervalued"
m4.metric("Vs sample average", fmt_pct(latest_idx / 100 - 1), lead, delta_color="off")

# --------------------------------------------------------------------------------------
# Chart
# --------------------------------------------------------------------------------------
st.subheader("Relative PPP chart")
view = st.radio("Series to show", ["Real", "Nominal", "Both"], horizontal=True, key="view")

x = sample.index
x0, x1 = x.min(), x.max()

# ---- Valuation regimes: Real Index vs the two relative-PPP benchmarks ----------------
REGIMES = {
    "over": {"fill": "rgba(239, 68, 68, 0.16)", "edge": "rgba(239, 68, 68, 0.55)",
             "label": f"{ccy1} overvalued", "word": "overvalued vs both benchmarks (red)"},
    "mixed": {"fill": "rgba(234, 179, 8, 0.20)", "edge": "rgba(202, 138, 4, 0.60)",
              "label": "Mixed signals", "word": "in mixed territory: overvalued vs one benchmark, undervalued vs the other (yellow)"},
    "under": {"fill": "rgba(59, 130, 246, 0.16)", "edge": "rgba(59, 130, 246, 0.55)",
              "label": f"{ccy1} undervalued", "word": "undervalued vs both benchmarks (blue)"},
}
bmap = {b["key"]: b for b in benchmarks}
regime_refs = [bmap["30y"]] if "30y" in bmap else []
if "trade" in bmap:
    regime_refs.append(bmap["trade"])
elif "anchor" in bmap:
    regime_refs.append(bmap["anchor"])
regime = pd.Series(index=sample.index, dtype="object")
if len(regime_refs) == 2:
    ri_s = sample["real_index"]
    over_a, over_b = ri_s > regime_refs[0]["index"], ri_s > regime_refs[1]["index"]
    regime[over_a & over_b] = "over"
    regime[~over_a & ~over_b] = "under"
    regime[over_a ^ over_b] = "mixed"
    regime[~sample["valid"]] = np.nan
    regime_note = (f"Background shading compares the Real Index with the **{regime_refs[0]['short'].lower()}** "
                   f"({regime_refs[0]['index']:.1f}) and the **{regime_refs[1]['short'].lower()}** "
                   f"({regime_refs[1]['index']:.1f}) benchmarks, month by month. 'Overvalued' and 'undervalued' "
                   f"refer to the {ccy1} against the {ccy2}.")
else:
    regime_note = ("Background shading needs two benchmarks (30-year average and balanced trade, or a custom "
                   "anchor month when trade data are missing). Enable a custom anchor month in the sidebar.")


def regime_runs(reg: pd.Series):
    """Contiguous runs of the same regime: (regime, first month, last month)."""
    runs, cur, start, prev = [], None, None, None
    for t, r in reg.items():
        r = None if pd.isna(r) else r
        if r != cur:
            if cur is not None:
                runs.append((cur, start, prev))
            cur, start = r, t
        prev = t
    if cur is not None:
        runs.append((cur, start, prev))
    return runs


# ---- Legend swatches (inline SVG images inside the checkbox labels) -------------------
DASHES = {"solid": "", "dash": "6,4", "dot": "2,3", "dashdot": "7,3,2,3"}


def _svg_img(svg: str) -> str:
    return "![](data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode() + ")"


def sw_line(color: str, dash: str = "solid", width: float = 2.5) -> str:
    da = f' stroke-dasharray="{DASHES.get(dash, "")}"' if DASHES.get(dash) else ""
    return _svg_img(f'<svg xmlns="http://www.w3.org/2000/svg" width="30" height="12"><line x1="1" y1="6" '
                    f'x2="29" y2="6" stroke="{color}" stroke-width="{width}"{da}/></svg>')


def sw_box(fill: str, edge: str) -> str:
    return _svg_img(f'<svg xmlns="http://www.w3.org/2000/svg" width="30" height="12"><rect x="1" y="1" '
                    f'width="28" height="10" rx="2" fill="{fill}" stroke="{edge}"/></svg>')


C_REAL, C_NOM = "#2563eb", "#dc2626"
real_on_chart = view in ("Real", "Both")
nom_on_chart = view in ("Nominal", "Both")

chart_col, panel_col = st.columns([3.3, 1.2], gap="medium")
with panel_col:
    st.markdown("**Show on chart**")
    st.caption("Curves")
    show_real = st.checkbox(f"{sw_line(C_REAL)} Real Index", value=True, key="show_real",
                            disabled=not real_on_chart,
                            help="Relative-PPP curve: 100 × RER / RER*. 100 = sample average. "
                                 f"Above 100 the {ccy1} is dear vs the {ccy2}; below 100 it is cheap.")
    show_nom = st.checkbox(f"{sw_line(C_NOM)} Nominal S", value=True, key="show_nom",
                           disabled=not nom_on_chart,
                           help=f"Actual market exchange rate in {s_unit} (not normalised).")
    show_ppp = st.checkbox(f"{sw_line(C_NOM, 'dash', 2)} PPP-implied S", value=False, key="show_ppp",
                           disabled=not nom_on_chart,
                           help=f"RER* × P2 / P1, in {s_unit}: the nominal rate at which the Real Index would be "
                                f"100. Actual S above this line = {ccy1} overvalued vs the sample average.")
    st.caption("Benchmarks (Real Index scale)")
    show_bench = {}
    for b in benchmarks:
        show_bench[b["key"]] = st.checkbox(
            f"{sw_line(b['color'], b['dash'], 2)} {b['label']} · {b['index']:.1f}",
            value=b["key"] in ("30y", "trade", "anchor"), key=f"show_b_{b['key']}",
            disabled=not real_on_chart, help=b["help"])
    st.caption("Valuation background")
    show_regime = {}
    for k, r in REGIMES.items():
        show_regime[k] = st.checkbox(f"{sw_box(r['fill'], r['edge'])} {r['label']}", value=True,
                                     key=f"show_r_{k}", disabled=len(regime_refs) < 2,
                                     help=f"Months when the {ccy1} is {r['word']}.")
    show_tw = st.checkbox(f"{sw_box('rgba(5, 150, 105, 0.18)', 'rgba(5, 150, 105, 0.7)')} Trade window",
                          value=False, key="show_tw", disabled=trade_window is None,
                          help="The 12 months used for the balanced-trade benchmark.")
    log_nominal = st.checkbox("Log scale (nominal axis)", value=False, key="log_nominal",
                              disabled=not nom_on_chart,
                              help="Useful for high-inflation currencies whose rate spans orders of magnitude.")

# ---- Figure ---------------------------------------------------------------------------
real_trace = go.Scatter(x=x, y=sample["real_index"], mode="lines", name="Real Index",
                        line={"color": C_REAL, "width": 2.2}, connectgaps=False,
                        hovertemplate="Real Index: %{y:.2f}<extra></extra>")
nom_trace = go.Scatter(x=x, y=sample["S"], mode="lines", name="Nominal S",
                       line={"color": C_NOM, "width": 1.8}, connectgaps=False,
                       hovertemplate=f"Nominal S: %{{y:.6~g}} {s_unit}<extra></extra>")
ppp_trace = go.Scatter(x=x, y=sample["ppp_implied_S"], mode="lines", name="PPP-implied S",
                       line={"color": C_NOM, "width": 1.4, "dash": "dash"}, opacity=0.7, connectgaps=False,
                       hovertemplate=f"PPP-implied S: %{{y:.6~g}} {s_unit}<extra></extra>")


def bench_traces():
    for b in benchmarks:
        if show_bench.get(b["key"]):
            yield go.Scatter(x=[x0, x1], y=[b["index"], b["index"]], mode="lines", name=b["label"],
                             line={"color": b["color"], "dash": b["dash"], "width": 1.6},
                             hovertemplate=f"{b['label']}: %{{y:.1f}}<extra></extra>")


nom_axis = {"title_text": f"Nominal S ({s_unit})", "type": "log" if log_nominal else "linear"}
if view == "Both":
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if show_real:
        fig.add_trace(real_trace, secondary_y=False)
    for t in bench_traces():
        fig.add_trace(t, secondary_y=False)
    if show_nom:
        fig.add_trace(nom_trace, secondary_y=True)
    if show_ppp:
        fig.add_trace(ppp_trace, secondary_y=True)
    fig.update_yaxes(title_text="Real Index (sample average = 100)", secondary_y=False)
    fig.update_yaxes(secondary_y=True, showgrid=False, tickmode="auto", **nom_axis)
elif view == "Real":
    fig = go.Figure()
    if show_real:
        fig.add_trace(real_trace)
    for t in bench_traces():
        fig.add_trace(t)
    fig.update_yaxes(title_text="Real Index (sample average = 100)")
else:
    fig = go.Figure()
    if show_nom:
        fig.add_trace(nom_trace)
    if show_ppp:
        fig.add_trace(ppp_trace)
    fig.update_yaxes(**nom_axis)

if len(regime_refs) == 2:
    one_month = pd.DateOffset(months=1)
    for r, a, b in regime_runs(regime):
        if show_regime.get(r):
            fig.add_vrect(x0=a, x1=b + one_month, fillcolor=REGIMES[r]["fill"], opacity=1, line_width=0,
                          layer="below")
if show_tw and trade_window is not None and trade_window[1] >= x0 and trade_window[0] <= x1:
    fig.add_vrect(x0=max(trade_window[0], x0), x1=min(trade_window[1] + pd.DateOffset(months=1), x1),
                  fillcolor="rgba(5, 150, 105, 0.18)", line={"color": "rgba(5, 150, 105, 0.7)", "width": 1},
                  layer="below", annotation_text="trade window", annotation_position="top left")

fig.update_layout(height=640, hovermode="x unified", showlegend=False,
                  margin={"l": 10, "r": 10, "t": 20, "b": 10}, xaxis_title=None,
                  xaxis={"hoverformat": "%Y-%m"})
with chart_col:
    if not fig.data:
        st.info("Nothing selected — tick at least one curve or benchmark on the right.")
    st.plotly_chart(fig, config={"displaylogo": False})
    st.caption(regime_note)
    if trade_note:
        st.caption(trade_note)

# --------------------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------------------
st.subheader("Current state of the currencies")


def describe(dev: float) -> str:
    if abs(dev) < 0.05:
        return "close to"
    return "above" if dev > 0 else "below"


dev_mean = latest_idx / 100.0 - 1.0
c2_vs = 1.0 / (1.0 + dev_mean) - 1.0
if abs(dev_mean) < 0.05:
    verdict = (f"On the relative-PPP benchmark, neither currency is clearly misaligned: the {ccy1} is within "
               f"±5% of its sample-average real value against the {ccy2} ({fmt_pct(dev_mean)}).")
elif dev_mean < 0:
    verdict = (f"On the relative-PPP benchmark, the **{ccy1} is the undervalued (devalued) currency**: it is about "
               f"{abs(dev_mean):.1%} cheaper in real terms than its sample average against the {ccy2} "
               f"(equivalently, the {ccy2} is about {c2_vs:.1%} overvalued against the {ccy1}).")
else:
    verdict = (f"On the relative-PPP benchmark, the **{ccy2} is the undervalued (devalued) currency**: the {ccy1} "
               f"is about {dev_mean:.1%} more expensive in real terms than its sample average "
               f"(equivalently, the {ccy2} is about {abs(c2_vs):.1%} undervalued against the {ccy1}).")

others = []
signs = [np.sign(dev_mean) if abs(dev_mean) >= 0.05 else 0]
for b in benchmarks[1:]:
    d = latest_idx / b["index"] - 1.0
    signs.append(np.sign(d) if abs(d) >= 0.05 else 0)
    others.append(f"the {b['short'].lower()} benchmark puts the {ccy1} {describe(d)} fair value ({fmt_pct(d)})")
n_under = sum(1 for s_ in signs if s_ < 0)
n_over = sum(1 for s_ in signs if s_ > 0)
if n_under > n_over:
    consensus = f"{n_under} of {len(signs)} benchmarks point to an undervalued {ccy1} (cheap {ccy1}, strong {ccy2})."
elif n_over > n_under:
    consensus = f"{n_over} of {len(signs)} benchmarks point to an overvalued {ccy1} (cheap {ccy2})."
else:
    consensus = "The benchmarks do not agree on a clear direction, so the evidence of misalignment is weak."

paragraph = (
    f"As of **{ym(latest_ts)}** (latest valid month in the sample), the real index stands at **{latest_idx:.1f}**, "
    f"{abs(latest_idx - 100):.1f} points {'above' if latest_idx >= 100 else 'below'} its {start_lbl}–{end_lbl} "
    f"average of 100. {verdict} The PPP-consistent rate implied by the sample average is "
    f"**{fmt_num(float(latest['ppp_implied_S']))} {s_unit}**, versus an actual "
    f"**{fmt_num(float(latest['S']))}**. "
    + (("Among the other benchmarks, " + "; ".join(others) + ". ") if others else "")
    + consensus
)
if len(regime_refs) == 2 and pd.notna(regime.get(latest_ts)):
    paragraph += (f" On the chart's background classification ({regime_refs[0]['short'].lower()} and "
                  f"{regime_refs[1]['short'].lower()}), the {ccy1} is currently "
                  f"**{REGIMES[regime[latest_ts]]['word']}**.")
st.markdown(paragraph)


def chg12(col: str) -> float:
    prev_ts = latest_ts - pd.DateOffset(months=12)
    if prev_ts in full.index and np.isfinite(full.loc[prev_ts, col]) and full.loc[prev_ts, col] > 0:
        return float(full.loc[latest_ts, col] / full.loc[prev_ts, col] - 1.0)
    return float("nan")


ri = valid_sample["real_index"]
rows = [
    ("Latest valid month in sample", ym(latest_ts)),
    (f"Nominal S ({s_unit})", fmt_num(float(latest["S"]))),
    (f"PPP-implied S, sample average ({s_unit})", fmt_num(float(latest["ppp_implied_S"]))),
    ("Real Index (sample average = 100)", f"{latest_idx:.2f}"),
    ("RER* (sample average of RER)", fmt_num(rer_star)),
]
for b in benchmarks:
    if b["short"] == "Sample average":
        rows.append(("Misalignment vs sample average", fmt_pct(dev_mean)))
        continue
    rows.append((f"{b['short']} benchmark (index level)", f"{b['index']:.2f} — {b['detail']}"))
    rows.append((f"Misalignment vs {b['short'].lower()}", fmt_pct(latest_idx / b["index"] - 1.0)))
rows += [
    ("Real Index, 12-month change", fmt_pct(chg12("RER"))),
    (f"Nominal S, 12-month change ({ccy1} vs {ccy2})", fmt_pct(chg12("S"))),
    (f"CPI inflation, 12 months — {name1}", fmt_pct(chg12("p1"))),
    (f"CPI inflation, 12 months — {name2}", fmt_pct(chg12("p2"))),
    ("Percentile of latest Real Index within sample", f"{(ri <= latest_idx).mean():.0%}"),
    ("Sample minimum of Real Index", f"{ri.min():.1f} ({ym(ri.idxmin())})"),
    ("Sample maximum of Real Index", f"{ri.max():.1f} ({ym(ri.idxmax())})"),
    ("Valid months / months in sample", f"{len(valid_sample)} / {len(sample)}"),
]
if len(regime_refs) == 2:
    counts = regime.value_counts()
    n_reg = int(counts.sum())
    for k, r in REGIMES.items():
        n = int(counts.get(k, 0))
        row_label = {"over": f"Months {ccy1} overvalued vs both benchmarks (red)",
                     "mixed": "Months with mixed signals (yellow)",
                     "under": f"Months {ccy1} undervalued vs both benchmarks (blue)"}[k]
        rows.append((row_label, f"{n} ({n / n_reg:.0%})" if n_reg else "n/a"))
st.dataframe(pd.DataFrame(rows, columns=["Statistic", "Value"]), hide_index=True)

# --------------------------------------------------------------------------------------
# Trade balance detail
# --------------------------------------------------------------------------------------
with st.expander("Bilateral trade balance used for the balanced-trade benchmark", expanded=False):
    if has_trade:
        tr = full["trade_balance_ratio_12m"].dropna()
        tfig = go.Figure()
        tfig.add_trace(go.Scatter(x=tr.index, y=tr * 100, mode="lines", name="12-month imbalance",
                                  line={"color": "#059669"},
                                  hovertemplate="%{x|%Y-%m}<br>%{y:.1f}% of total trade<extra></extra>"))
        tfig.add_hrect(y0=-trade_threshold * 100, y1=trade_threshold * 100, fillcolor="#059669",
                       opacity=0.1, line_width=0)
        tfig.add_hline(y=0, line_color="#6b7280", line_width=1)
        tfig.update_layout(height=320, margin={"l": 10, "r": 10, "t": 10, "b": 10},
                           yaxis_title=f"(exports − imports) / total, % — {name1} view")
        st.plotly_chart(tfig, config={"displaylogo": False})
        st.caption(f"Source: IMF IMTS goods trade in USD, {bundle['trade_src']}. Positive values mean "
                   f"{name1} exports more goods to {name2} than it imports from it. The shaded band is the "
                   "tolerance used to call trade 'balanced'.")
    else:
        st.write("No bilateral trade data available from the IMF for this pair.")

# --------------------------------------------------------------------------------------
# CSV download
# --------------------------------------------------------------------------------------
st.subheader("Download data")
export = pd.DataFrame(index=sample.index)
export["month"] = [ym(t) for t in sample.index]
export[f"{c1}_{ccy1}_per_USD"] = sample["fx1_per_usd"]
export[f"{c2}_{ccy2}_per_USD"] = sample["fx2_per_usd"]
export[f"S_{ccy2}_per_{ccy1}"] = sample["S"]
export[f"P1_CPI_{c1}"] = sample["p1"]
export[f"P2_CPI_{c2}"] = sample["p2"]
export["valid_month"] = sample["valid"]
export["RER"] = sample["RER"]
export["RER_star_sample_mean"] = rer_star
export["real_index"] = sample["real_index"]
export[f"PPP_implied_S_{ccy2}_per_{ccy1}"] = sample["ppp_implied_S"]
for b in benchmarks:
    col = "benchmark_" + re.sub(r"[^a-z0-9]+", "_", b["short"].lower()).strip("_") + "_index"
    export[col] = b["index"]
export[f"exports_{c1}_to_{c2}_USD"] = sample["exports_c1_to_c2"]
export[f"imports_{c1}_from_{c2}_USD"] = sample["imports_c1_from_c2"]
export["trade_imbalance_ratio_12m"] = sample["trade_balance_ratio_12m"]
csv_bytes = export.to_csv(index=False).encode("utf-8")
st.download_button(
    "⬇️ Download CSV (all observations and calculations in the selected sample)",
    data=csv_bytes, file_name=f"relative_ppp_{c1}_{c2}_{start_lbl}_{end_lbl}.csv", mime="text/csv",
)
st.caption("Invalid months (a missing CPI or exchange-rate value) are kept with blank RER and index. "
           "The average of `real_index` over valid months equals 100, and RER = S × P1 / P2 row by row.")

# --------------------------------------------------------------------------------------
# Disclaimer
# --------------------------------------------------------------------------------------
st.subheader("How to interpret these results")
st.warning(f"""
**Disclaimer — read before drawing conclusions.**

- **Relative, not absolute, PPP.** The index only says whether the {ccy1} is dearer or cheaper *than it was on
  average over your chosen sample*. It assumes that average was "fair". Change the dates and the verdict can change.
- **Deviations can last for years.** Real exchange rates typically take several years to close half of a
  deviation, and some never revert: faster productivity growth (Balassa–Samuelson), terms-of-trade shocks,
  commodity cycles, capital flows and interest-rate differentials can shift the equilibrium permanently.
- **CPI is an imperfect price measure.** Baskets, weights and methods differ by country and include
  non-traded services; CPI changes can lag or misstate true price pressure.
- **The benchmarks are crude anchors.** The 30-year average assumes stationarity over three decades.
  Balanced *bilateral* goods trade ignores services, third countries, supply chains and savings–investment
  balances, and IMF trade data mix FOB exports, CIF imports and sometimes mirror statistics.
- **Data caveats.** IMF series can have gaps, revisions, redenominations, pegs, dual or parallel exchange
  rates and hyperinflation episodes; official rates may differ from market rates. Months with missing data are
  excluded, not interpolated.
- **Not financial advice.** This is an educational tool. Do not use it as the sole basis for trading,
  investment or business decisions.

Source: International Monetary Fund (CPI, ER and IMTS datasets via the IMF SDMX 3.0 API). Calculations are
by this app: S is derived from the IMF rates against the US dollar.
""")

with st.expander("Diagnostics: IMF requests used for this pair", expanded=False):
    st.dataframe(log_df, hide_index=True)
    if st.button("Clear cached IMF data and reload"):
        st.cache_data.clear()
        st.rerun()
