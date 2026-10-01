# Lighthouse user guide

Lighthouse answers three questions about a stock for **you** specifically: *should I buy it, how much, and where would I be wrong?*

## Three ways to run it

| | Best for | How |
|---|---|---|
| **Google Colab / Jupyter notebook** | the course deliverable; running end-to-end with narration | open `notebooks/Lighthouse.ipynb`, run all cells; change the inputs cell |
| **Streamlit app** | non-programmers, live demos | `pip install -r requirements.txt` then `streamlit run app.py` |
| **Command line** | power users, batch runs, saving reports | `python -m lighthouse analyze MSFT --horizon long --risk balanced --max-loss 2 --portfolio 25000 --out reports/MSFT --evaluate` |

Other commands: `python -m lighthouse screen AAPL MSFT NVDA JPM --risk conservative --csv ranking.csv` and
`python -m lighthouse backtest MSFT --years 15 --evaluate`. Add `--as-of 2020-03-20` to ask what Lighthouse would have said on a past day.

## The inputs

| Input | Meaning | Effect |
|---|---|---|
| Ticker | any US-listed symbol | |
| Horizon | **long** (6-24+ months) or **short** (weeks to a few months) | long: fundamentals 60% / technicals 40%, monthly review, exit below the 200-day line; short: 30/70, weekly review, ATR trailing stop |
| Risk tolerance | conservative / balanced / aggressive | max single position 10 / 20 / 35%; volatility target 10 / 15 / 25%; stop distance 3 / 2.5 / 2 ATR (×2 for long horizon) |
| Max acceptable loss | % of your whole portfolio you accept losing on this one position if the exit is hit | position size = max loss ÷ distance to the exit level, if that is the tightest rule |
| Portfolio value | $ | turns percentages into dollars and shares |
| Years of history | default 10 (notebook 15) | analysis window and backtest length |
| Data source | auto / yfinance / stooq / cache / sample | `auto` tries live sources then offline fallbacks; `sample` uses the bundled historical pack |

## Reading the output

* **Verdict and Lighthouse score (0-100)** — ≥72 Strong Buy · ≥60 Buy · ≥45 Hold · ≥32 Reduce · <32 Sell.
* **Beacons** — every signal with its reading, a light (🟢 bullish, 🟡 neutral, 🔴 bearish, ⚪ unavailable), its vote (−1…+1), weight and the points it added. The bar chart shows the same thing visually.
* **Market weather** — Bull/Bear × Calm/Turbulent for the S&P 500 and how it changed the score and the size.
* **Position plan** — suggested % and $ and shares, exit level, worst-case loss and *which rule limited the size* (max-loss, volatility target or concentration cap). Hold means keep what you own; Reduce means trim about half; Sell means exit.
* **Backtest** — how the rule that produced this verdict behaved historically versus buy-and-hold and the S&P 500: return, volatility, Sharpe, max drawdown, trades, win rate. With *deep evaluation*: layer attribution, regime breakdown, sensitivity grid, out-of-sample test.
* **Warnings** — data source notes (offline data, missing volume, fixture fundamentals), anything you should know before acting.

## Changing the rules without programming

All settings are in `lighthouse/config.py`: risk presets, horizon weights, beacon weights, verdict bands and the
`StrategyParams` dataclass (indicator windows, thresholds, review frequency, costs). Sector norms are in
`lighthouse/fundamentals.py` (`SECTOR_NORMS`). The CLI accepts one-off overrides: `--params '{"entry_score": 65}'`.
If a fundamentals feed is missing a field, type your own: in the app's expert settings as JSON, or on the CLI with
`--fundamentals-json my_numbers.json` (same format as `data/sample/fundamentals/MSFT.json`).

## Troubleshooting

* **"could not load prices"** — no internet, or Yahoo is rate-limiting. Retry in a minute, or use `--source stooq`, or `--source sample` for the bundled tickers (`MSFT`, `AAPL`, `JNJ`, `XOM`, `JPM`, `UNH`, `KO`, `PG`, `PFE`, `WMT`, `HD`, `GE`, `CVX`, `LLY`, `BAC`, `AMD`, `MRK`, `PEP`, `BBY`, `RRC`).
* **"Fundamental data unavailable"** — the verdict is technical-only and flagged; supply overrides or try later.
* **Volume beacon grey** — the series has no volume (index data or the offline pack's close-only files); the other beacons are re-weighted.
* **Set `LIGHTHOUSE_OFFLINE=1`** to force offline mode (used by the test-suite and CI).

Lighthouse is a decision-support tool, not investment advice.
