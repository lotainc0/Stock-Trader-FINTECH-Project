# Lighthouse methodology — every choice, and why

This document exists so that the team can defend every number in the product. It follows the order of the pipeline.

## 1. Data

| Item | Choice | Why |
|---|---|---|
| Prices | Daily OHLCV, dividend- and split-adjusted, from Yahoo Finance (`yfinance`), fallback Stooq CSV | Free, no API key, adjusted series avoid false "crashes" at ex-dividend/split dates |
| History | 10 years by default (15 in the notebook), plus a 1.2-year warm-up so the 200-day SMA and 52-week high are defined on day one | Covers at least one bear market; indicators need 252 days |
| Benchmark | S&P 500 index (`^GSPC`) | The market the customer compares against; used for regime detection |
| Fundamentals | Yahoo snapshot (trailing/forward P/E, PEG, P/B, EV/EBITDA, ROE, margins, D/E, current ratio, FCF, growth) plus annual and quarterly statements | One valuation ratio and one quality/growth measure are required; we use several so a missing field never breaks the score |
| Cleaning | Sort, de-duplicate dates, drop rows without a positive close, fill missing O/H/L from the close, enforce High ≥ max(O,C) and Low ≤ min(O,C), disable volume if >50% missing, flag >50% one-day moves | Each step is counted and shown to the user (`CleaningReport`) |
| Offline pack | Real data shipped in open-source packages (Microsoft OHLCV 1986-2017 from `pmdarima`, 19 S&P stocks and the index 1990-2022 from `skfolio`) | The notebook and tests must run end-to-end without internet; provenance in `scripts/build_sample_pack.py` |
| Caching | Last live download cached in `data/cache/` for 20 hours | Yahoo rate-limits; the product stays usable |

## 2. Fundamental beacons

Each beacon votes in [−1, +1]. Group score = mean of available beacons; fundamental score = 50 + 50 × (0.40 valuation + 0.35 quality + 0.25 growth), re-normalised when a group is empty.

| Beacon | Rule | Reasoning |
|---|---|---|
| P/E (trailing) | +1 at half the sector-norm P/E, −1 at 1.5×, −0.75 if earnings are negative | Relative valuation: a tech stock at 25× is normal, an energy stock at 25× is expensive |
| P/E (forward) | same vs 0.9× the sector norm | Forward multiples run lower than trailing |
| PEG | +1 at 0.5, 0 at 1.5, −1 at 2.5 | Growth-adjusted price; the classic "PEG ≈ 1 is fair" |
| P/B | +1 at 0.4× norm, −1 at 1.6× norm | Matters most for financials and asset-heavy firms; secondary for asset-light |
| EV/EBITDA | +1 at half norm, −1 at 1.5× norm; skipped for financials | Capital-structure-neutral valuation |
| Earnings stability | coefficient of variation of annual EPS: +0.5 at 0, 0 at 35%, −1 at 70% | A P/E on erratic earnings is a weak anchor |
| ROE | 0 at 10%, +1 at 25%, −1 at −5% | 10% ≈ cost of equity; >20% is a compounding business |
| Operating margin | 0 at the sector norm, ±1 at ±15 pts | Pricing power / cost advantage, sector-relative |
| Debt/Equity | +1 at 0, 0 at 1, −1 at 2; skipped for financials | Leverage amplifies losses and refinancing risk |
| FCF yield | 0 at 3%, +1 at 6%, −1 at 0% | Cash actually generated relative to price |
| Current ratio | −1 at 0, 0 at 1, +0.5 at 1.5 | Liquidity check, capped so it cannot dominate |
| Revenue growth | 0 at 5%, +1 at 20%, −1 at −10% | 5% ≈ nominal GDP growth |
| EPS growth | 0 at 8%, +1 at 28%, −1 at −12% | 8% ≈ long-run market EPS growth |

Sector norms (`SECTOR_NORMS`) are approximate long-run medians for the 11 GICS sectors and are explicitly assumptions the user can edit. Growth uses annual statements first (stable) and Yahoo's quarterly year-on-year figures as a fallback.

**Point-in-time history.** For the backtest, each annual statement becomes usable 90 days after its period end (the 10-K filing deadline for large filers is 60 days; 90 is conservative). Quality and growth are fixed for the year; the P/E is recomputed daily from price / lagged EPS. Before the first statement the score is "unknown" and no gate is applied.

## 3. Technical beacons

All indicators are causal (rolling windows, `ewm(adjust=False)`): the value on day *t* uses only data up to *t*. A unit test truncates the series and checks that nothing in the past changes.

| Beacon | Formula | Reasoning | Weight long / short |
|---|---|---|---|
| Trend | 0.5·clip((Close/SMA200 − 1)/10%) + 0.5·clip((SMA50/SMA200 − 1)/5%) | Continuous Golden/Death Cross: fewer whipsaws than a binary cross | 35% / 20% |
| RSI(14) | linear from −1 at 30 to +1 at 70, then fading (+1 → −1 from 70 to 100) | Confirms momentum; extremes signal exhaustion | 15% / 20% |
| MACD(12/26/9) | histogram / ATR, clipped | Momentum acceleration, scale-free | 10% / 20% |
| Bollinger %B (20, 2σ) | 2·(%B − 0.5) inside the bands, fading above 1 / below 0 | Where price sits in its recent range; above the band = over-extended | 10% / 15% |
| Volume | 0.5·sign(20-day return)·clip((vol20/vol60 − 1)/0.5, 0, 1) + 0.5·sign(OBV − SMA20(OBV)) | A move on expanding volume is more trustworthy; excluded (grey) when volume is unavailable | 10% / 15% |
| 52-week high | +1 at the high, 0 at 20% below, −1 at 40% below | Breakout proximity; momentum literature | 20% / 10% |

Technical score = 50 + 50 × weighted mean, with weights re-normalised over available beacons. Long-term weights lean on slow beacons (trend, breakout); short-term on oscillators.

## 4. Market weather (regime)

* Trend: S&P 500 above / below its 200-day SMA → Bull / Bear.
* Volatility: 21-day realised vol above 1.25× its trailing 252-day median → Turbulent.
* Effects: Bear −8 score points, entry threshold +10, new positions ×0.5. Turbulent −3 points. Bear & Turbulent: new entries blocked.

Reasoning: the 200-day rule is the simplest widely studied market-timing filter (Faber 2007); volatility clustering means turbulence persists, and most of the worst days happen below the 200-day line. On Microsoft 2002-2017 the regime filter raised the signal rule's CAGR from 4.4% to 5.8% and its Sharpe from 0.22 to 0.29 with fewer trades.

## 5. Decision and position plan

* Composite = 0.60·F + 0.40·T (long) or 0.30·F + 0.70·T (short) − weather penalty. Missing fundamentals → technical only, with a warning.
* Verdict bands: ≥72 Strong Buy, ≥60 Buy, ≥45 Hold, ≥32 Reduce, else Sell. Bands are symmetric around 50 with a wider Hold zone so small changes do not flip the verdict.
* Exit level: long horizon = 200-day SMA × (1 − 8%); short horizon = 2.5× ATR(14) trailing (2.0 aggressive, 3.0 conservative). The live plan and the backtest use the same exit, so what the user sees is what was tested.
* Size = min(max-loss rule, volatility rule, concentration cap) × conviction × regime multiplier:
  * Max-loss rule: position % = max loss % / distance to the exit level. The investor's number, not ours.
  * Volatility rule: target vol (10 / 15 / 25%) / 60-day realised vol.
  * Concentration cap: 10 / 20 / 35%.
  * Conviction: 0 at score 45, 1 at 80.

## 6. Backtest protocol

* Long-only, one stock, capital = the "sleeve" allocated to it.
* Review day: last trading day of the month (long) or week (short). Daily reviews were tested and rejected (more trades, lower Sharpe).
* Enter when trend beacon > 0, technical score ≥ 60 (+10 in a bear market), not Bear/Turbulent, point-in-time fundamental score ≥ 35 if known.
* Exit when score ≤ 35 (long) / 40 (short), close < 200-SMA × 0.92 (long) / 0.97 (short), or the ATR stop (short, checked daily).
* Size: volatility target / 60-day realised vol × regime multiplier, re-balanced on review days when the target moves by more than 10 points.
* Fills at the next day's open (close when no open data), 5 bps commission + 5 bps slippage per side.
* Metrics: total return, CAGR, volatility, Sharpe and Sortino (2% risk-free), max drawdown, Calmar, trades, win rate, average win/loss, profit factor, exposure; plus layer attribution, sensitivity grid (entry 50-70 × exit 25-45), walk-forward (tune on 60%, test on 40%) and regime breakdown.
* No look-ahead: unit-tested by re-running on truncated data and comparing equity curves.

## 7. What we tested and rejected (design-phase experiments, 12-16 stocks, 1990-2022)

| Variant | Result | Decision |
|---|---|---|
| 2.5× ATR trailing stop on the long-term rule | 380 trades per stock, Sharpe 0.17 vs 0.41 without | Rejected for long horizon; kept (wider, 2× factor) for short horizon as the max-loss anchor |
| Daily review | more trades, lower Sharpe than weekly; monthly best | Monthly (long) / weekly (short) |
| Exit buffer 3% vs 8% below the 200-SMA | 8% fewer whipsaws, higher win rate | 8% long / 3% short |
| Exit score 45 vs 35 | 35 better on every metric | 35 |
| "Don't chase" filter (skip entries >15% above the SMA) | lower Sharpe | Rejected |
| Death-cross exit (trend beacon < −0.5) | no improvement over the SMA buffer | Rejected |
| Position size frozen at entry with 20-day vol | large size errors after volatile entries | 60-day vol, monthly re-balance |

## 8. Results and interpretation

See `reports/examples/cross_ticker_evaluation.csv` (20 stocks, 1990-2022). Averages: buy-and-hold CAGR 13.3%, vol 33.5%, Sharpe 0.50, max DD −69%; Lighthouse balanced CAGR 6.4%, vol 12.7%, Sharpe 0.39, max DD −32%; conservative vol 8.8%, max DD −23%. Lower drawdown and volatility in 20/20 stocks; higher Sharpe in 4/20.

Interpretation: on a universe of survivors that compounded at 13% a year, a rule that is sometimes in cash cannot win on return. The product's value is that it delivers *the risk profile the investor asked for* — a balanced investor who said "15% volatility, lose at most 2% per idea" actually gets ~13% volatility and a defined worst case — while keeping most of the up-trend exposure. We state this plainly rather than hiding it behind a cherry-picked ticker.

## 9. Q&A preparation

* **Why these indicators?** Trend (200-day) is the most robust timing signal in the literature; RSI/MACD/Bollinger cover momentum, acceleration and range; volume validates moves; 52-week high is the simplest breakout measure. Fundamentals cover the three questions an analyst asks: is it cheap, is it good, is it growing.
* **Why should they predict returns?** Trend and momentum persistence are documented anomalies; quality and value premia are documented factors. We do not claim short-term prediction; we claim better risk-adjusted participation.
* **Why these thresholds?** Chosen *before* tuning for interpretability (60 = clearly positive evidence; 35 = clearly negative), then checked with a sensitivity grid: results form a plateau and in-sample tuning did not improve out-of-sample results.
* **Different market conditions?** The regime table shows the rule loses roughly half as much as buy-and-hold in Bear/Turbulent markets and keeps 60-65% exposure in Bull/Calm markets.
* **Sensitivity?** Sharpe across 20 threshold pairs on Microsoft ranged 0.05-0.20 (default 0.18); the biggest sensitivity is the review frequency and the stop design, which is why both are documented above.
* **Biggest weaknesses?** Whipsaw losses in long sideways markets; sector norms are assumptions; short fundamental history; survivorship in our test universe; no transaction-tax modelling.
* **What did GenAI contribute?** Code scaffolding, indicator implementations and the experiment harness. The team decided the customer, the signals, the weights, the exit design, what to reject, and how to present results honestly.
