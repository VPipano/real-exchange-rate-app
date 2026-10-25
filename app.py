"""IMF Relative PPP Explorer — Streamlit Community Cloud / Python 3.12.

Data: IMF SDMX 3.0 (CPI, ER, optional IMTS).
All rates are monthly period averages; no observations are fabricated or forward-filled.
"""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pycountry
import requests
import streamlit as st
from babel.numbers import get_territory_currencies
from plotly.subplots import make_subplots

API_ROOT = "https://api.imf.org/external/sdmx/3.0"
FIRST_MONTH = pd.Period("1976-01", freq="M")
CACHE_SECONDS = 12 * 60 * 60
HEADERS = {"Accept": "text/csv", "User-Agent": "IMF-Relative-PPP-Explorer/1.0"}


class IMFDataError(Exception):
    """Expected error while requesting or interpreting IMF observations."""


def country_options() -> dict[str, str]:
    """Broad ISO3 country/economy selector; the IMF may not report every code."""
    mapping = {
        f"{item.name} ({item.alpha_3})": item.alpha_3
        for item in pycountry.countries
        if hasattr(item, "alpha_3")
    }
    mapping["Euro area (G163)"] = "G163"
    mapping["Kosovo (XKX)"] = "XKX"
    return dict(sorted(mapping.items(), key=lambda item: item[0].casefold()))


def currency_code(country: str) -> str:
    if country == "G163":
        return "EUR"
    if country == "XKX":
        return "EUR"
    try:
        record = pycountry.countries.get(alpha_3=country)
        if record is not None:
            codes = get_territory_currencies(record.alpha_2, tender=True)
            if codes:
                return codes[0]
    except (LookupError, ValueError, AttributeError):
        pass
    return "local currency"


def parse_month(value: object) -> Optional[pd.Period]:
    """Accept IMF's 2025-M01 / 2025-01 (and SDMX monthly ISO) labels."""
    raw = str(value).strip().replace("-M", "-")
    if len(raw) >= 7:
        try:
            return pd.Period(raw[:7], freq="M")
        except ValueError:
            pass
    return None


def fetch_imf_csv(flow: str, key: str) -> pd.DataFrame:
    """Read the public IMF SDMX-CSV response (latest dataflow version).

    The IMF publishes SDMX3 version aliases both as '~' and '+'. Try '~'
    first, as in the published direct-CSV examples, and '+' if it fails.
    """
    problems: list[str] = []
    for version in ("~", "+"):
        url = f"{API_ROOT}/data/dataflow/IMF.STA/{flow}/{version}/{key}"
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                params={"c[TIME_PERIOD]": "ge:1976" if flow == "IMTS" else "ge:1976-M01"},
                timeout=(8, 30),
            )
            if response.status_code == 204 or not response.content.strip():
                problems.append(f"{version}: no data (HTTP {response.status_code})")
                continue
            response.raise_for_status()
            frame = pd.read_csv(io.StringIO(response.content.decode("utf-8-sig")), low_memory=False)
            frame.columns = frame.columns.astype(str).str.strip().str.upper()
            if not {"TIME_PERIOD", "OBS_VALUE"}.issubset(frame.columns):
                problems.append(f"{version}: unexpected IMF CSV fields")
                continue
            return frame
        except (requests.RequestException, UnicodeDecodeError, pd.errors.ParserError) as exc:
            problems.append(f"{version}: {str(exc)[:160]}")
    raise IMFDataError(f"IMF {flow} series {key}: " + "; ".join(problems))


def read_imf_series(flow: str, key: str, country: str) -> pd.Series:
    table = fetch_imf_csv(flow, key)
    if "COUNTRY" in table.columns:
        table = table.loc[table["COUNTRY"].astype(str) == country]
    if table.empty:
        raise IMFDataError(f"No monthly {flow} series found for {country}: {key}.")
    table = table.copy()
    table["month"] = table["TIME_PERIOD"].map(parse_month)
    table["value"] = pd.to_numeric(table["OBS_VALUE"], errors="coerce")
    # Unit multipliers, if explicitly supplied by the IMF, affect levels.
    if "SCALE" in table.columns:
        scale = pd.to_numeric(table["SCALE"], errors="coerce").fillna(0)
        table["value"] *= np.power(10.0, scale)
    elif "UNIT_MULT" in table.columns:
        multiplier = pd.to_numeric(table["UNIT_MULT"], errors="coerce").fillna(0)
        table["value"] *= np.power(10.0, multiplier)
    table = table.loc[table["month"].notna() & (table["value"] > 0)]
    if table.empty:
        raise IMFDataError(f"The IMF returned no valid positive monthly values for {key}.")
    return (
        table.sort_values("month")
        .drop_duplicates("month", keep="last")
        .set_index("month")["value"]
        .sort_index()
        .astype(float)
    )


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def fetch_country(country: str) -> pd.DataFrame:
    """Request CPI and local-currency-per-USD ER from the IMF."""
    cpi_key = f"{country}.CPI._T.IX.M"
    cpi = read_imf_series("CPI", cpi_key, country)
    if country == "USA":
        # USD per USD is 1 by identity, even if the IMF omits its ER series.
        months = pd.period_range(FIRST_MONTH, pd.Period(date.today(), freq="M"), freq="M")
        fx = pd.Series(1.0, index=months, name="fx")
    else:
        er_key = f"{country}.XDC_USD.PA_RT.M"
        fx = read_imf_series("ER", er_key, country)
    return pd.concat([cpi.rename("cpi"), fx.rename("fx")], axis=1).sort_index()


def assemble_history(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    left = a.rename(columns={"cpi": "cpi_1", "fx": "fx_1_lcu_per_usd"})
    right = b.rename(columns={"cpi": "cpi_2", "fx": "fx_2_lcu_per_usd"})
    frame = left.join(right, how="outer").sort_index()
    frame = frame.loc[frame.index >= FIRST_MONTH].copy()
    if not frame.empty:
        full_months = pd.period_range(FIRST_MONTH, frame.index.max(), freq="M")
        frame = frame.reindex(full_months)  # keep even wholly missing calendar months for CSV
    for key in ["cpi_1", "cpi_2", "fx_1_lcu_per_usd", "fx_2_lcu_per_usd"]:
        frame.loc[frame[key] <= 0, key] = np.nan
    # FX_i is local-currency units per USD. Therefore FX_2 / FX_1
    # is units of Country 2 currency per one Country 1 currency.
    frame["nominal_s_currency2_per_currency1"] = (
        frame["fx_2_lcu_per_usd"] / frame["fx_1_lcu_per_usd"]
    )
    frame["rer"] = (
        frame["nominal_s_currency2_per_currency1"] * frame["cpi_1"] / frame["cpi_2"]
    )
    frame = frame.replace([np.inf, -np.inf], np.nan)
    return frame


def get_30_year_anchor(history: pd.DataFrame) -> tuple[float, int, str]:
    good = history["rer"].dropna()
    if good.empty:
        return float("nan"), 0, "Unavailable"
    latest = good.index.max()
    first = latest - 359  # 360 calendar months inclusive; never 360 observations arbitrarily.
    within = good.loc[(good.index >= first) & (good.index <= latest)]
    return float(within.mean()), len(within), f"{first} to {latest}"


def parse_trade_table(raw: pd.DataFrame, reporter: str, partner: str) -> pd.DataFrame:
    df = raw.copy()
    if "COUNTRY" in df:
        df = df.loc[df["COUNTRY"].astype(str) == reporter]
    if "COUNTERPART_COUNTRY" in df:
        df = df.loc[df["COUNTERPART_COUNTRY"].astype(str) == partner]
    if "INDICATOR" not in df.columns:
        raise IMFDataError("IMTS response has no INDICATOR column.")
    if df.empty:
        raise IMFDataError("No bilateral goods trade records in the IMTS response.")
    df["year"] = pd.to_numeric(df["TIME_PERIOD"].astype(str).str[:4], errors="coerce")
    df["value"] = pd.to_numeric(df["OBS_VALUE"], errors="coerce")
    if "SCALE" in df.columns:
        df["value"] *= np.power(10.0, pd.to_numeric(df["SCALE"], errors="coerce").fillna(0))
    elif "UNIT_MULT" in df.columns:
        df["value"] *= np.power(10.0, pd.to_numeric(df["UNIT_MULT"], errors="coerce").fillna(0))
    df = df.loc[df["year"].notna() & df["value"].notna()]
    result = df.pivot_table(index="year", columns="INDICATOR", values="value", aggfunc="last")
    if not {"XG_FOB_USD", "MG_CIF_USD"}.issubset(result.columns):
        raise IMFDataError("Exports and imports are not both available for this pair.")
    result = result[["XG_FOB_USD", "MG_CIF_USD"]].dropna()
    result = result.loc[(result > 0).all(axis=1)].copy()
    result.index = result.index.astype(int)
    result["total_trade"] = result.sum(axis=1)
    result["imbalance_pct"] = (
        100.0 * (result["XG_FOB_USD"] - result["MG_CIF_USD"]).abs()
        / result["total_trade"]
    )
    return result


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def trade_data(country1: str, country2: str) -> tuple[pd.DataFrame, str, str]:
    """Use either reporter direction if one country's reporting is missing."""
    errors: list[str] = []
    for reporter, partner in ((country1, country2), (country2, country1)):
        key = f"{reporter}.XG_FOB_USD+MG_CIF_USD.{partner}.A"
        try:
            table = parse_trade_table(fetch_imf_csv("IMTS", key), reporter, partner)
            if not table.empty:
                return table, reporter, partner
        except IMFDataError as exc:
            errors.append(str(exc)[:180])
    raise IMFDataError("Annual bilateral merchandise trade was not available in either reporter direction. "
                       + "; ".join(errors))


def choose_trade_anchor(
    frame: pd.DataFrame, trade: pd.DataFrame, max_imbalance_pct: float
) -> Optional[dict]:
    """Use the closest-to-balanced *qualifying* annual goods-trade year.

    Ignore years with insignificant two-way trade (less than 20% of the
    historical 90th-percentile total) and years without any RER observations.
    """
    if trade.empty:
        return None
    substantial = trade["total_trade"].quantile(0.9) * 0.20
    years = trade.loc[(trade["total_trade"] >= substantial)
                      & (trade["imbalance_pct"] <= max_imbalance_pct)].copy()
    choices: list[dict] = []
    for year, row in years.iterrows():
        rr = frame.loc[frame.index.year == year, "rer"].dropna()
        if len(rr) >= 6:
            choices.append({"year": int(year), "rer": float(rr.mean()),
                            "imbalance_pct": float(row["imbalance_pct"]),
                            "valid_months": int(len(rr)),
                            "exports": float(row["XG_FOB_USD"]),
                            "imports": float(row["MG_CIF_USD"])})
    if not choices:
        return None
    return sorted(choices, key=lambda c: (c["imbalance_pct"], -c["year"]))[0]


def add_calculations(
    sample: pd.DataFrame, sample_anchor: float, thirty_anchor: float,
    trade_anchor: float,
) -> pd.DataFrame:
    out = sample.copy()
    out["real_index_sample_100"] = 100 * out["rer"] / sample_anchor
    out["real_index_sample_benchmark"] = 100.0
    out["ppp_nominal_sample"] = sample_anchor * out["cpi_2"] / out["cpi_1"]
    for name, anchor in (("30y", thirty_anchor), ("trade", trade_anchor)):
        out[f"real_index_{name}_benchmark"] = (
            100.0 * anchor / sample_anchor if np.isfinite(anchor) else np.nan
        )
        out[f"ppp_nominal_{name}"] = (
            anchor * out["cpi_2"] / out["cpi_1"] if np.isfinite(anchor) else np.nan
        )
    return out.replace([np.inf, -np.inf], np.nan)


def make_chart(data: pd.DataFrame, mode: str, code1: str, code2: str,
               reference_label: Optional[str], show_implied: bool) -> go.Figure:
    both = mode == "Both"
    fig = make_subplots(specs=[[{"secondary_y": True}]]) if both else go.Figure()
    xs = data.index.to_timestamp()
    real = mode in ("Real", "Both")
    nominal = mode in ("Nominal", "Both")

    def add_line(series: str, label: str, color: str, dash: str = "solid",
                 secondary: bool = False, width: float = 2.0):
        if series not in data or data[series].notna().sum() == 0:
            return
        trace = go.Scatter(x=xs, y=data[series], name=label, mode="lines",
                           connectgaps=False,
                           line={"color": color, "width": width, "dash": dash},
                           hovertemplate="%{x|%b %Y}<br>%{y:,.4f}<extra>%{fullData.name}</extra>")
        if both:
            fig.add_trace(trace, secondary_y=secondary)
        else:
            fig.add_trace(trace)

    if real:
        add_line("real_index_sample_100", "Real index (sample mean = 100)", "#1768ac", width=2.7)
        add_line("real_index_sample_benchmark", "Selected-period average", "#697887", "dash")
        add_line("real_index_30y_benchmark", "Last 30 years average", "#d08732", "dash")
        if reference_label is not None:
            add_line("real_index_trade_benchmark", reference_label, "#16836e", "dot")
    if nominal:
        add_line("nominal_s_currency2_per_currency1", "Actual nominal S", "#8f4a96", secondary=both, width=2.7)
        if show_implied:
            add_line("ppp_nominal_sample", "Nominal PPP path · selected mean", "#697887", "dash", secondary=both)
            add_line("ppp_nominal_30y", "Nominal PPP path · 30y", "#d08732", "dash", secondary=both)
            if reference_label is not None:
                add_line("ppp_nominal_trade", f"Nominal PPP path · {reference_label}", "#16836e", "dot", secondary=both)

    units = f"{code2} per 1 {code1}"
    if both:
        fig.update_yaxes(title_text="Real index (selected average = 100)", secondary_y=False)
        fig.update_yaxes(title_text=f"Nominal FX ({units})", secondary_y=True)
    else:
        fig.update_yaxes(title_text="Real index (selected average = 100)" if real else f"Nominal FX ({units})")
    fig.update_layout(
        height=550, margin=dict(l=15, r=15, t=25, b=25),
        legend=dict(orientation="h", yanchor="top", y=-0.18, x=0),
        hovermode="x unified", template="plotly_white",
        xaxis_title="Month", font=dict(size=12),
    )
    fig.update_xaxes(showgrid=True, gridcolor="#eff2f5")
    return fig


def fmt(number: float, digits: int = 2) -> str:
    return f"{number:,.{digits}f}" if np.isfinite(number) else "N/A"


def main() -> None:
    st.set_page_config(page_title="IMF Relative PPP Explorer", page_icon="🌍", layout="wide")
    st.title("🌍 IMF Relative PPP Explorer")
    st.caption("Monthly CPI-adjusted exchange rates • IMF SDMX 3.0 • historical benchmarks, not forecasts")

    options = country_options()
    names = list(options)
    uk_label = next((s for s in names if options[s] == "GBR"), names[0])
    us_label = next((s for s in names if options[s] == "USA"), names[1])
    c1, c2 = st.columns(2)
    with c1:
        name1 = st.selectbox("Country 1 (currency being evaluated)", names, index=names.index(uk_label))
    with c2:
        name2 = st.selectbox("Country 2 (comparison currency)", names, index=names.index(us_label))
    country1, country2 = options[name1], options[name2]
    if country1 == country2:
        st.info("Select two different economies.")
        st.stop()
    currency1, currency2 = currency_code(country1), currency_code(country2)
    st.caption(f"Exchange-rate convention: **1 {currency1} = S(t) {currency2}** "
               "(Country 2 currency per unit of Country 1 currency).")

    with st.spinner("Retrieving IMF monthly CPI and exchange-rate observations…"):
        results: dict[str, pd.DataFrame] = {}
        failures: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = {pool.submit(fetch_country, country1): country1,
                    pool.submit(fetch_country, country2): country2}
            for future in as_completed(jobs):
                code = jobs[future]
                try:
                    results[code] = future.result()
                except (IMFDataError, requests.RequestException, ValueError) as exc:
                    failures[code] = str(exc)
        if failures:
            for code, message in failures.items():
                st.error(f"IMF data unavailable for {code}: {message}")
            st.info("Try a different country pair, or try again if the IMF service is temporarily unavailable. "
                    "The app does not invent or interpolate missing IMF observations.")
            st.stop()
        history = assemble_history(results[country1], results[country2])
    valid = history["rer"].dropna()
    if valid.empty:
        st.warning("This pair has no overlapping, valid monthly CPI and FX observations since January 1976.")
        st.stop()
    if len(valid) < 12:
        st.warning("Fewer than 12 overlapping observations are available. Historical comparisons are fragile.")

    st.divider()
    first_available, last_available = valid.index.min(), valid.index.max()
    possible_months = list(pd.period_range(first_available, last_available, freq="M"))
    default_start = first_available  # show all common history by default, including 1976 if available
    d1, d2, d3 = st.columns([1, 1, 1])
    with d1:
        start = st.selectbox("Start month", possible_months,
                             index=possible_months.index(default_start), format_func=str)
    with d2:
        end = st.selectbox("End month", possible_months,
                           index=len(possible_months) - 1, format_func=str)
    with d3:
        mode = st.radio("Chart", ["Real", "Nominal", "Both"], index=2, horizontal=True)
    if start > end:
        st.error("The start month must be on or before the end month.")
        st.stop()

    sample = history.loc[start:end].copy()
    valid_sample = sample["rer"].dropna()
    if valid_sample.empty:
        st.warning("The chosen range contains no months with all four inputs present. Change the date range.")
        st.stop()
    sample_anchor = float(valid_sample.mean())
    thirty_anchor, thirty_n, thirty_window = get_30_year_anchor(history)

    with st.expander("Trade-balance benchmark settings (optional)", expanded=False):
        st.write("The automatic reference uses **annual bilateral merchandise exports and imports** "
                 "from IMF IMTS. A year qualifies only when |exports − imports| / "
                 "(exports + imports) is below your threshold, two-way trade is substantive, "
                 "and at least six months have valid RER observations. This does **not** prove "
                 "an equilibrium currency value or balanced trade in services.")
        anchor_choice = st.radio("Reference method", ["Automatic: IMF merchandise trade", "Manual month (unverified)", "None"],
                                  horizontal=False)
        threshold = st.slider("Maximum bilateral goods imbalance (%)", 1, 30, 10, 1,
                              disabled=anchor_choice != "Automatic: IMF merchandise trade")
        manual_month = st.selectbox("Manual reference month", possible_months,
                                    index=len(possible_months) // 2,
                                    format_func=str,
                                    disabled=anchor_choice != "Manual month (unverified)")

    anchor_trade = float("nan")
    anchor_year: Optional[int] = None
    trade_info = None
    trade_note = "Trade-balance benchmark disabled."
    if anchor_choice == "Automatic: IMF merchandise trade":
        if "G163" in (country1, country2):
            trade_note = "Bilateral goods-trade auto-anchor is unavailable for the euro-area aggregate (G163)."
        else:
            with st.spinner("Checking IMF annual bilateral merchandise trade…"):
                try:
                    trade_frame, reporter, partner = trade_data(country1, country2)
                    trade_info = choose_trade_anchor(history, trade_frame, float(threshold))
                    if trade_info:
                        anchor_year = trade_info["year"]
                        anchor_trade = trade_info["rer"]
                        trade_note = (f"IMF IMTS reference: {anchor_year} • reporter {reporter} → partner {partner} "
                                      f"• annual goods-trade imbalance {trade_info['imbalance_pct']:.1f}% "
                                      f"• RER averaged over {trade_info['valid_months']} months.")
                    else:
                        trade_note = ("No qualifying trade-balance year with sufficient RER observations "
                                      f"was found at a ≤{threshold}% goods-imbalance threshold. ")
                except IMFDataError as exc:
                    trade_note = f"IMF IMTS data unavailable for this pair: {str(exc)[:300]}"
    elif anchor_choice == "Manual month (unverified)":
        if pd.notna(history.loc[manual_month, "rer"]):
            anchor_trade = float(history.loc[manual_month, "rer"])
            trade_note = (f"Manual reference: {manual_month}; not verified as a year or month with "
                          "balanced bilateral trade. Treat as a user-chosen historical comparison only.")
        else:
            trade_note = f"{manual_month} has no valid RER observation; manual benchmark omitted."

    calculated = add_calculations(sample, sample_anchor, thirty_anchor, anchor_trade)
    # The valuation snapshot stays at the IMF latest common month even when
    # the user selects a historical chart window; the selected baseline changes.
    latest_month = valid.index.max()
    latest_full = add_calculations(history.loc[[latest_month]], sample_anchor,
                                   thirty_anchor, anchor_trade)
    latest = latest_full.iloc[0]
    relative30 = (float(latest["rer"]) / thirty_anchor - 1) * 100
    relative_sample = (float(latest["rer"]) / sample_anchor - 1) * 100
    relative_trade = ((float(latest["rer"]) / anchor_trade - 1) * 100
                      if np.isfinite(anchor_trade) else np.nan)

    st.subheader("Currency valuation snapshot")
    a, b, c, d = st.columns(4)
    a.metric(f"Latest nominal · {currency2}/{currency1}", fmt(float(latest["nominal_s_currency2_per_currency1"]), 4),
             help="Monthly period-average FX rate, not a live trading quote.")
    b.metric("Latest real index", fmt(float(latest["real_index_sample_100"])),
             delta=f"{relative_sample:+.1f}% vs sample mean")
    c.metric("Vs. trailing 30-year average", f"{relative30:+.1f}%")
    d.metric("Last complete observation", str(latest_month))

    if abs(relative30) < 2:
        leading = f"{currency1} appears broadly in line with its trailing 30-year CPI-adjusted historical average against {currency2}."
    elif relative30 > 0:
        leading = (f"{currency1} (Country 1) appears **relatively strong / historically overvalued** "
                   f"against {currency2} (Country 2) by {relative30:.1f}% versus the available trailing-30-year average.")
    else:
        leading = (f"{currency1} (Country 1) appears **relatively weak / historically undervalued** "
                   f"against {currency2} (Country 2) by {abs(relative30):.1f}% versus the available trailing-30-year average.")
    trade_phrase = (f" The merchandise-trade reference suggests a {relative_trade:+.1f}% real deviation "
                    "relative to its own historical anchor." if anchor_year is not None else "")
    st.markdown(f"{leading} As of **{latest_month}**, the nominal monthly average was "
                f"**{fmt(float(latest['nominal_s_currency2_per_currency1']), 4)} {currency2} per {currency1}**, "
                f"and the real index was **{fmt(float(latest['real_index_sample_100']))}** "
                f"on the selected-period scale (100 = selected-period mean). "
                f"The charted window contains **{len(valid_sample)}** valid months; the trailing-30-year "
                f"benchmark uses **{thirty_n}/360** possible months ({thirty_window}).{trade_phrase} "
                "The conclusion is descriptive rather than a prediction or evidence of an exploitable mispricing.")

    reference_rows = [
        ("Current nominal S", fmt(float(latest["nominal_s_currency2_per_currency1"]), 6), f"{currency2}/{currency1}"),
        ("Current CPI · Country 1", fmt(float(latest["cpi_1"]), 4), "IMF CPI index level"),
        ("Current CPI · Country 2", fmt(float(latest["cpi_2"]), 4), "IMF CPI index level"),
        ("Current RER = S × P1 / P2", fmt(float(latest["rer"]), 6), "CPI-dependent ratio"),
        ("Current real index", fmt(float(latest["real_index_sample_100"]), 2), "selected mean = 100"),
        ("Sample RER*", fmt(sample_anchor, 6), f"{len(valid_sample)} valid months"),
        ("Last plotted valid month", str(valid_sample.index.max()), "selected window"),
        ("Trailing-30y RER benchmark", fmt(thirty_anchor, 6), f"{thirty_n}/360 months"),
        ("Vs. trailing 30y", f"{relative30:+.2f}%", "positive = Country 1 relatively strong"),
        ("Trade / manual anchor RER", fmt(anchor_trade, 6), f"{anchor_year or 'manual / unavailable'}"),
        ("Vs. trade / manual anchor", f"{relative_trade:+.2f}%" if np.isfinite(relative_trade) else "N/A", "if available"),
    ]
    st.dataframe(pd.DataFrame(reference_rows, columns=["Statistic", "Value", "Details"]),
                 hide_index=True, use_container_width=True)

    st.subheader("Historical comparison")
    show_implied = st.checkbox("Show PPP-implied nominal exchange-rate paths", value=True,
                               disabled=mode == "Real")
    reference_label = (
        f"Bilateral goods-trade anchor ({anchor_year})" if anchor_year is not None
        else f"Manual historical anchor ({manual_month}; unverified)"
        if anchor_choice == "Manual month (unverified)" and np.isfinite(anchor_trade)
        else None
    )
    st.plotly_chart(make_chart(calculated, mode, currency1, currency2,
                               reference_label, show_implied), use_container_width=True)
    st.caption(f"**Snapshot is as of {latest_month}; chart ends {end}.** "
               f"**30-year baseline:** {thirty_window} ({thirty_n} usable months). "
               f"**Trade/manual reference:** {trade_note}")
    if thirty_n < 300:
        st.warning(f"The '30-year' baseline includes only {thirty_n} valid months within the 360-month calendar "
                   "window. It is not a full 30-year observation record.")
    if len(valid_sample) < 24:
        st.warning("Selected sample has fewer than 24 valid months; its 100-point baseline is especially sensitive to date choice.")
    if currency1 == currency2:
        st.warning("These economies currently share a currency. Older IMF exchange-rate series may refer to predecessor currencies.")

    export = calculated.copy()
    export.index = export.index.astype(str)
    export.index.name = "month"
    st.download_button("Download selected-period observations and calculations (CSV)",
                       data=export.to_csv(float_format="%.12g").encode("utf-8"),
                       file_name=f"imf_ppp_{country1}_{country2}_{start}_{end}.csv",
                       mime="text/csv")
    st.caption("Export includes every calendar month in the selected range and leaves IMF gaps blank. "
               "Underlying computations use unrounded values; round-off may affect exported precision slightly.")

    with st.expander("Metric definitions and interpretation", expanded=False):
        st.markdown(f"""
**Nominal exchange rate S(t)** — units of **{currency2} per 1 {currency1}**. A higher S
means Country 1's currency buys more of Country 2's currency (nominal appreciation).
IMF reports local-currency units per USD; **S = FX₂ / FX₁**.

**P₁(t), P₂(t)** — monthly IMF headline consumer-price *index levels* for Country 1
and Country 2. They are not inflation percentages. CPI base-year conventions differ.

**Real exchange rate (RER)** — **S × P₁ / P₂**. A rising RER corresponds to a stronger
Country 1 currency after CPI adjustment; a falling RER to a weaker one.

**Selected-period benchmark** (`RER*`) — arithmetic mean of valid monthly RERs in the
selected dates. **Real index = 100 × RER / RER-star**, so its arithmetic mean over
valid sample months is **exactly 100 before CSV rounding**.

**Trailing-30-year benchmark** — mean RER over the last 360 *calendar* months ending
with the latest valid common month; only existing valid observations count. The
horizontal line on the real-index chart is 100 × (30-year mean / sample RER-star).

**Trade-balance reference** — optional mean RER for the selected historical year
meeting a user-chosen bilateral **goods** trade-imbalance threshold. Selected using
IMF's IMTS merchandise exports (FOB) and imports (CIF); services and capital flows
are excluded. A manually chosen month is explicitly **unverified**. Goods trade
balance **does not establish fair value**.

**PPP-implied nominal paths** — nominal exchange rates consistent with a fixed
benchmark RER: **Sᴾᴾᴾ(t) = RER benchmark × P₂(t) / P₁(t)**. They are calculations,
not IMF forecasts or spot quotes. Charts with Both show separate real (left) and
nominal (right) axes.
""")

    with st.expander("Important disclaimers and data sources", expanded=False):
        st.markdown("""
1. **Not an absolute valuation model.** Index levels are relative to historical
   benchmarks, not theoretical fair value. The chosen period can materially alter
   the result; a previously strong currency need not revert to its historical mean.
2. **PPP is primarily a medium/long-run hypothesis.** Productivity, non-tradables,
   capital flows, interest rates, risk, policy interventions, and terms of trade
   can sustain real exchange-rate differences for years.
3. **Data limitations.** Monthly IMF series can have gaps, revisions, different
   release dates or a shorter history than 1976. Currency changes (including euro
   adoption and redenominations), CPI splices and structural breaks can invalidate
   naive comparisons across decades. The app never forward-fills missing observations.
4. **Trade qualifier.** Balance in *bilateral merchandise* trade is not balance in
   services, the current account or overall external payments. Exports are FOB,
   imports are generally CIF. A low measured imbalance does not establish PPP.
5. **Latest means latest common available month**, not necessarily the current
   market rate. The IMF API may be temporarily unreachable or may change its schemas.

Sources: [IMF data portal](https://data.imf.org/) ·
[IMF CPI](https://data.imf.org/en/datasets/IMF.STA:CPI) ·
[IMF ER](https://data.imf.org/en/datasets/IMF.STA:ER) ·
[IMF bilateral goods trade (IMTS)](https://data.imf.org/en/datasets/IMF.STA:IMTS)
""")


if __name__ == "__main__":
    main()
