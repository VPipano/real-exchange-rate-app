# IMF Relative PPP Explorer

A Streamlit dashboard for comparing two economies' currencies using monthly IMF consumer-price indices and period-average foreign-exchange rates, with historical valuation benchmarks and downloadable calculations.

## Deploy to Streamlit Community Cloud

1. Create a GitHub repository with **`app.py`**, **`requirements.txt`**, and **`README.md`** in its **top-level directory**.
2. Paste each file's content (without Markdown code fences) and commit the changes to the `main` branch.
3. Go to <https://share.streamlit.io>, log in with GitHub, and select **Create app → Yup, I have an app**.
4. Choose your repository, branch **`main`**, and entrypoint **`app.py`**. In Advanced settings use **Python 3.12**; leave Secrets empty.
5. Deploy. The dashboard requests IMF data as you select countries; an IMF API key is not used by the app.

To run locally:

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

## Data and calculation conventions

- **Source:** IMF SDMX 3.0 (`https://api.imf.org/external/sdmx/3.0`), dataflows `IMF.STA/CPI`, `IMF.STA/ER`, and optionally `IMF.STA/IMTS`.
- **Monthly CPI:** `{ISO3}.CPI._T.IX.M`. These are CPI **index levels**, not inflation-rate percentages.
- **Monthly FX:** `{ISO3}.XDC_USD.PA_RT.M`. IMF quotes local-currency units **per USD**, as a *monthly period average*. For the United States, USD/USD is set to 1 by identity.
- **Euro area:** special IMF code `G163`. A Euro-area series will only work if the IMF returns that series. Other selections use ISO 3166 alpha-3 codes; not every economy in the list necessarily has IMF data.
- **Requested history:** from January 1976 onward where available. The default chart starts with the earliest common available month (January 1976 if both series go back that far). No invented/interpolated observations.
- **Quote convention:** S(t) = units of Country 2 currency per 1 unit of Country 1 currency. With local-per-USD rates `FX1` and `FX2`, **S(t) = FX2(t) / FX1(t)**.
- **Real exchange rate:** `RER(t) = S(t) × CPI1(t) / CPI2(t)`.
- **Selected-period mean:** `RER* = arithmetic mean(RER(t))` across *valid* monthly data in the user-selected date window.
- **Real index:** `100 × RER(t) / RER*`; its exact, unrounded mean within that sample is **100**. The valuation snapshot always uses the **latest common available month**, even when you chart an earlier historical interval.
- **Independent 30-year reference:** arithmetic mean RER for the 360 calendar months ending at the latest overlapping observation. The number of valid observations is displayed; the sample may have fewer than 360.
- **Optional trade reference:** IMF IMTS annual bilateral merchandise series `{reporter}.XG_FOB_USD+MG_CIF_USD.{partner}.A`, where `reporter` is Country 1 or (if required) Country 2. It chooses the eligible year with the *lowest* `abs(exports-imports)/(exports+imports)` below the user's threshold, requires annual trade of at least 20% of the 90th-percentile historical trade amount, and at least six monthly RER observations in the year. Benchmark RER is the mean RER of that year. If unavailable, it does not invent a reference; the user may supply a **manual, unverified** comparison month or turn the feature off.
- **PPP-consistent nominal rate at a fixed RER benchmark `B`:** `S_ppp(t) = B × CPI2(t) / CPI1(t)`; calculated rather than observed.

## Reading the dashboard

- **Real index > 100**: Country 1 currency is historically stronger than its **selected-period** mean, not necessarily overvalued in an economic equilibrium sense. Below 100 means historically weaker.
- **Latest RER > trailing-30-year RER mean**: Currency 1 is relatively strong versus that historical reference. Below means relatively weak.
- **Nominal chart** shows actual Country-2-currency units per Country 1 currency. Never normalized. Optional PPP-implied paths use the same units.
- **Both** uses the real index on one axis and nominal rates on a different axis.
- **CSV** includes each month in the *selected* date range, raw CPI/FX observations, nominal S, RER, normalized real index, benchmark values, and PPP-implied nominal paths. Missing data remain blank, rather than being forward-filled. Formulas use unrounded data; CSV retains approximately 12 significant digits.

## Important limitations

1. This is a **historical CPI-adjusted comparison**, not a forecast, an absolute purchasing-power parity estimate, or a tradable fair-value quote.
2. Relative PPP is more informative over long horizons; real exchange rates may remain away from historical means for extended periods owing to structural factors.
3. Bilateral merchandise trade balance does **not** imply balanced services trade, the current account, capital flows, or an equilibrium exchange rate. FOB export and CIF import measurements also differ.
4. CPI methodology and base dates, currency redenominations, euro adoption and predecessor currencies, missing monthly data, revisions and changes in IMF datasets may materially affect the analysis.
5. The two country selectors are broad ISO-country choices plus `G163` and `XKX`. Selecting a country with missing IMF CPI/ER data shows a clear error instead of a fabricated result.
6. The application requires outbound access to the IMF API. The IMF may have outages, rate limits, changed response formats or unavailable series. The API is not queried from this README.

## Recommended manual checks

1. Select **United Kingdom (GBR)** as Country 1 and **United States (USA)** as Country 2. `S` should be **USD per GBP**, approximately the inverse of the IMF's GBP-per-USD series.
2. Change the date window and download the CSV. Compute the mean of the nonmissing `real_index_sample_100` column: it should be 100, apart from export rounding.
3. Pick a valid month and verify `nominal_s_currency2_per_currency1 = fx_2_lcu_per_usd / fx_1_lcu_per_usd` and `rer = nominal_s_currency2_per_currency1 × cpi_1 / cpi_2`.
4. Verify the chart modes **Real**, **Nominal**, and **Both**. Confirm the chart's nominal axis has real currency units and is not rebased to 100.
5. Test a country pair with limited data. Verify the app warns rather than filling in absent values.

## Data sources

- [IMF Data](https://data.imf.org/)
- [IMF API information](https://data.imf.org/en/Resource-Pages/IMF-API)
- [IMF CPI](https://data.imf.org/en/datasets/IMF.STA:CPI)
- [IMF exchange rates](https://data.imf.org/en/datasets/IMF.STA:ER)
- [IMF bilateral merchandise trade (IMTS)](https://data.imf.org/en/datasets/IMF.STA:IMTS)

This project has no financial-advice or forecasting capability; interpret all benchmarks cautiously.
