# Relative PPP Explorer (IMF data)

A Streamlit app that compares any two IMF economies (or the Euro area) and shows whether one currency is
over- or undervalued against the other according to **relative purchasing power parity (PPP)**.

## What it does

1. **Pick two economies.** Country 1 is the base currency, Country 2 the quote currency.
2. **Downloads monthly IMF data** from January 1976 where available:
   - CPI: dataflow `IMF.STA/CPI`, key `{ISO3}.CPI._T.IX.M`
   - Exchange rates: dataflow `IMF.STA/ER`, key `{ISO3}.XDC_USD.PA_RT.M` (national currency per USD, period average)
   - Bilateral goods trade (for the balanced-trade benchmark): dataflow `IMF.STA/IMTS`,
     keys `{REPORTER}.XG_FOB_USD.{PARTNER}.M` and `{REPORTER}.MG_CIF_USD.{PARTNER}.M`
   - The Euro area uses the IMF code `G163`.
3. **Calculates**
   - `S(t)` = units of Country 2 currency per 1 unit of Country 1 currency = (C2 per USD) / (C1 per USD)
   - `RER(t) = S(t) × P1(t) / P2(t)`
   - `RER*` = average of RER over the valid months of the selected sample (recalculated whenever the dates change)
   - `Real Index(t) = 100 × RER(t) / RER*`
   - PPP-implied nominal rate = `RER* × P2 / P1` (the value of S at which the index would be 100)
4. **Benchmarks** drawn on the chart:
   - Sample average (= 100 by construction)
   - Average of the last 30 years (360 months ending at the latest available month)
   - Balanced-trade moment: average RER over the 12 months ending when 12-month bilateral goods trade
     was balanced within a tolerance you choose (default ±5%). If the reporting country does not publish
     bilateral trade, mirror data from the partner are used.
   - Optional custom anchor month (switched on automatically when no trade data exist)
5. **Interactive chart** with Real, Nominal, or Both (separate axes). Nominal S is shown in actual currency
   units, never normalised.
   - A **"Show on chart" panel** to the right of the chart has one checkbox per curve (Real Index, Nominal S,
     PPP-implied S), per benchmark and per background colour. Each checkbox shows its own line style or colour
     swatch (it doubles as the legend), and its **?** icon explains what it is. Options that do not apply to the
     current view are greyed out. Log scale for the nominal axis is there too.
   - **Valuation background:** each month is compared with the two relative-PPP benchmarks (30-year average and
     balanced-trade moment; the custom anchor month replaces the balanced-trade benchmark when trade data are
     missing). **Light red** = Country 1 currency overvalued vs both, **light yellow** = overvalued vs one and
     undervalued vs the other (undefined), **light blue** = undervalued vs both. Months with missing data stay white.
6. **Summary** paragraph and statistics table: which currency is undervalued, by how much, against each benchmark,
   the current background classification, and how many months of the sample fall in each colour.
7. **CSV download** of every observation and calculation in the selected sample.
8. **Collapsible metric guide** and a **disclaimer** on interpretation.

## Files

| File | Purpose |
|---|---|
| `app.py` | The Streamlit application |
| `requirements.txt` | Python packages (compatible with Python 3.12) |
| `README.md` | This file |

All three files sit at the top level of the repository.

## Deploy on Streamlit Community Cloud

1. In the GitHub repository: **Add file → Create new file**, name it `app.py`, paste the code, **Commit changes**.
   Repeat for `requirements.txt`, then edit `README.md`.
2. Sign in at [share.streamlit.io](https://share.streamlit.io) with GitHub → **Create app → Yup, I have an app**.
3. Choose the repository, branch `main`, main file `app.py`.
4. **Advanced settings:** Python **3.12**. Secrets can stay empty.
5. **Deploy** and save the `*.streamlit.app` link.

### Optional IMF API key

The IMF API works without a key. If you later get rate-limited, register at the IMF API portal and add this in
Streamlit **Settings → Secrets**:

```toml
IMF_API_KEY = "your-key-here"
```

## Checks to run after deploying

- **Direction of S:** Country 1 = United Kingdom, Country 2 = United States. The page should say
  `S(t) = USD per GBP`, with values around 1.2–1.4 in recent years (around 2.0+ in the early 1980s).
- **Average = 100:** change the sample dates, download the CSV, and average the `real_index` column
  (blank rows are months with missing data and are excluded). The result should be 100.
- **One RER by hand:** pick a row in the CSV and check
  `S_USD_per_GBP = 1 / GBR_GBP_per_USD` and `RER = S_USD_per_GBP × P1_CPI_GBR / P2_CPI_USA`,
  then `real_index = 100 × RER / RER_star_sample_mean`.
- **Views:** switch between Real, Nominal and Both, and tick/untick items in the "Show on chart" panel.
- **Colours:** in red months the Real Index sits above both the 30-year and balanced-trade lines, in blue months
  below both, and in yellow months between them.

## Troubleshooting

- **"Not enough IMF data"**: the IMF does not publish monthly CPI or exchange rates for that economy
  (or only for short periods). Open **Diagnostics: IMF requests** at the bottom of the page to see every
  URL called and its result.
- **Request errors (HTTP 429, 5xx, timeouts)**: the IMF API is busy or rate-limiting. Wait a minute and reload,
  or use the **Clear cached IMF data and reload** button in the diagnostics panel. Failed requests are never cached.
- **Euro area:** if the IMF has no euro-area exchange rate under `G163`, the app uses EUR per USD from the
  Germany series from January 1999 onward and says so on the page.
- When reporting a problem, copy the exact error and the diagnostics table.

## Notes on interpretation

Relative PPP measures misalignment only relative to the chosen sample average or benchmark, real exchange rates
can deviate for many years, CPI baskets differ across countries, and IMF data may have gaps, revisions and
redenominations. The app is an educational tool, not financial advice.

Data source: International Monetary Fund, via the IMF SDMX 3.0 API (`https://api.imf.org/external/sdmx/3.0`).
