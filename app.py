"""
Lighthouse - the web app.

    streamlit run app.py

Four tabs: analyse one stock, rank and compare a watchlist, inspect the data behind a verdict,
and learn how the method works. Every chart is interactive (hover for the reason behind every
mark, zoom, range buttons, PNG download) and every number can be downloaded as CSV or Markdown.
"""
from __future__ import annotations

import json
import os
import traceback
from dataclasses import asdict
from datetime import date

import pandas as pd
import streamlit as st

from lighthouse import analyze, screen, InvestorProfile, StrategyParams
from lighthouse import interactive as ix
from lighthouse.config import VERDICT_BANDS, RISK_PRESETS, HORIZON_WEIGHTS
from lighthouse.data import sample_pack_tickers, offline_mode, load_prices, DataUnavailable
from lighthouse.decision import size_position
from lighthouse.regime import regime_adjustments
from lighthouse.report import format_metrics, LIGHT_ICON

st.set_page_config(page_title="Lighthouse · explainable stock decisions", page_icon="🔦", layout="wide",
                   menu_items={"About": "Lighthouse turns a ticker and four facts about you into an explainable Buy / Hold / Sell "
                                        "decision with a position plan and an honest backtest. Decision support, not investment advice."})

# ----------------------------------------------------------------------------- look & accessibility
st.markdown("""
<style>
/* keyboard users always see where they are */
*:focus-visible { outline: 3px solid #2a78d6 !important; outline-offset: 2px !important; }
/* st.metric values never get clipped with an ellipsis (the "Market weather" fix) */
[data-testid="stMetricValue"] { font-size: 1.55rem !important; white-space: normal !important; overflow: visible !important; line-height: 1.2 !important; }
[data-testid="stMetricLabel"] { white-space: normal !important; }
/* KPI cards: wrap-safe, themed, readable in light and dark mode */
.lh-kpi { padding: .7rem .95rem; border: 1px solid rgba(128,128,128,.28); border-radius: .7rem;
          background: var(--secondary-background-color); min-height: 5.6rem; margin-bottom: .5rem; }
.lh-kpi-label { font-size: .82rem; opacity: .75; margin-bottom: .15rem; }
.lh-kpi-value { font-size: 1.65rem; font-weight: 650; line-height: 1.15; word-break: break-word; white-space: normal; }
.lh-kpi-sub { font-size: .82rem; opacity: .8; margin-top: .2rem; }
.lh-verdict { display: inline-block; padding: .15rem .7rem; border-radius: 999px; color: white; font-weight: 700; font-size: 1.05rem; vertical-align: middle; }
.lh-muted { opacity: .72; }
h2, h3, h4 { scroll-margin-top: 4rem; }
</style>
""", unsafe_allow_html=True)

VERDICT_COLOR = ix.VERDICT_COLOR
LIGHT_WORD = ix.LIGHT_WORD
POPULAR = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM", "UNH", "KO"]
PRESET_WATCHLISTS = {
    "Mega caps": "AAPL MSFT NVDA AMZN GOOGL META",
    "Dividend & defensive": "JNJ KO PG PEP WMT UNH",
    "Energy & financials": "XOM CVX JPM BAC",
    "Sample pack (offline)": "MSFT AAPL JNJ XOM JPM UNH KO PG",
}


def _is_offline() -> bool:
    return offline_mode() or st.session_state.get("source") == "sample"


# ----------------------------------------------------------------------------- cached work
@st.cache_data(show_spinner=False, ttl=3600)
def _run(ticker, horizon, risk, max_loss, portfolio, years, source, entry, exit_, cost, overrides, as_of):
    prof = InvestorProfile(horizon=horizon, risk=risk, max_loss_pct=max_loss, portfolio_value=portfolio)
    params = StrategyParams.for_horizon(horizon, entry_score=entry, exit_score=exit_, cost_bps=cost)
    return analyze(ticker, prof, params, years=years, source=source, fundamentals_overrides=overrides or None, as_of=as_of)


@st.cache_data(show_spinner=False, ttl=3600)
def _screen(tickers, horizon, risk, max_loss, portfolio, years, source):
    prof = InvestorProfile(horizon=horizon, risk=risk, max_loss_pct=max_loss, portfolio_value=portfolio)
    return screen(list(tickers), prof, StrategyParams.for_horizon(horizon), years=years, source=source, progress=False)


@st.cache_data(show_spinner=False, ttl=3600)
def _closes(ticker, years, source) -> pd.Series:
    return load_prices(ticker, years=years, source=source)["Close"].rename(ticker)


# ----------------------------------------------------------------------------- helpers
def kpi(col, label: str, value: str, sub: str = "", color: str | None = None, help_: str | None = None) -> None:
    """A wrap-safe metric card (st.metric truncates long values such as 'Bull / Turbulent')."""
    style = f" style='color:{color}'" if color else ""
    title = f" title='{help_}'" if help_ else ""
    col.markdown(f"<div class='lh-kpi' role='group' aria-label='{label}'{title}><div class='lh-kpi-label'>{label}</div>"
                 f"<div class='lh-kpi-value'{style}>{value}</div><div class='lh-kpi-sub'>{sub}</div></div>", unsafe_allow_html=True)


def beacon_frame(beacons) -> pd.DataFrame:
    rows = [{"Light": f"{LIGHT_ICON[b.light]} {LIGHT_WORD[b.light]}", "Signal": b.name, "Reading": b.value,
             "Vote": None if pd.isna(b.score) else round(float(b.score), 2), "Weight": f"{b.weight * 100:.0f}%",
             "Points": None if pd.isna(b.score) else round(float(b.contribution), 1), "Why": b.rationale} for b in beacons]
    return pd.DataFrame(rows)


BEACON_COLUMNS = {
    "Light": st.column_config.TextColumn("Light", help="Green = bullish, amber = neutral, red = bearish, white = no data", width="small"),
    "Signal": st.column_config.TextColumn("Signal", width="medium"),
    "Reading": st.column_config.TextColumn("Reading", width="medium"),
    "Vote": st.column_config.NumberColumn("Vote", help="−1 (bearish) … +1 (bullish)", format="%+.2f", width="small"),
    "Weight": st.column_config.TextColumn("Weight", help="Share of the group score", width="small"),
    "Points": st.column_config.NumberColumn("Points", help="Points added to the group score (vote × weight × 50)", format="%+.1f", width="small"),
    "Why": st.column_config.TextColumn("Why", width="large"),
}


def resize_plan(res, max_loss: float, portfolio: float, risk: str):
    """Re-run only the position-sizing rules with a different investor profile (instant, no new analysis)."""
    d, last = res.decision, res.indicators.iloc[-1]
    prof = InvestorProfile(horizon=d.profile.horizon, risk=risk, max_loss_pct=max_loss, portfolio_value=portfolio)
    adj = regime_adjustments(d.regime.is_bear if d.regime else False, d.regime.is_turbulent if d.regime else False, res.params)
    return size_position(d.price, float(last["atr"]), float(last["sizing_vol"]), d.composite_score, d.verdict, prof,
                         adj["size_multiplier"], float(last["sma_slow"]), res.params)


def show_error(exc: Exception, ticker: str) -> None:
    if isinstance(exc, DataUnavailable):
        st.error(f"**Could not load data for {ticker}.** {exc}")
        st.markdown("Things to try:\n"
                    "- Check the ticker symbol (US-listed symbols such as `MSFT`, `BRK-B`).\n"
                    "- Yahoo Finance may be rate-limiting: wait a minute and press Analyse again, or choose the `stooq` data source.\n"
                    "- No internet? Choose the `sample` data source: it includes "
                    f"{', '.join(t for t in sample_pack_tickers() if not t.startswith('^'))}.")
    else:
        st.error(f"**Something went wrong:** {type(exc).__name__}: {exc}")
        with st.expander("Technical details"):
            st.code(traceback.format_exc())


def plotly(fig, key: str | None = None) -> None:
    st.plotly_chart(fig, width="stretch", config=ix.PLOTLY_CONFIG, key=key)


def _pick_ticker() -> None:
    v = st.session_state.get("pick")
    if v:
        st.session_state["ticker"] = v


def _typed_ticker() -> None:
    """Runs before the widgets are drawn, so the typed value can still be normalised here."""
    st.session_state["pick"] = None
    st.session_state["ticker"] = st.session_state.get("ticker", "").upper().strip()


def _open_from_watchlist(ticker: str) -> None:
    st.session_state["ticker"] = ticker
    st.session_state["pick"] = None
    st.session_state["auto_run"] = True


# ----------------------------------------------------------------------------- sidebar
st.session_state.setdefault("ticker", "MSFT")
with st.sidebar:
    st.title("🔦 Lighthouse")
    st.caption("A clear signal in a noisy market. Explainable Buy / Hold / Sell decisions for self-directed investors.")

    st.text_input("Ticker", key="ticker", help="Any US-listed symbol, e.g. MSFT, AAPL, BRK-B", on_change=_typed_ticker)
    ticker = st.session_state["ticker"].upper().strip()
    quick = [t for t in sample_pack_tickers() if not t.startswith("^")] if _is_offline() else POPULAR
    st.pills("Quick pick", quick, selection_mode="single", key="pick", on_change=_pick_ticker, label_visibility="collapsed")

    st.subheader("About you", anchor=False)
    horizon = st.radio("Investment horizon", ["long", "short"], key="horizon",
                       format_func=lambda h: "Long-term (6-24+ months)" if h == "long" else "Short-term (weeks to months)",
                       help="Long: fundamentals 60% / technicals 40%, monthly review, exit below the 200-day line. "
                            "Short: 30 / 70, weekly review, ATR trailing stop.")
    risk = st.select_slider("Risk tolerance", ["conservative", "balanced", "aggressive"], value="balanced", key="risk",
                            help="Sets the maximum single position (10 / 20 / 35%), the volatility target (10 / 15 / 25%) and the stop distance.")
    max_loss = st.slider("Max acceptable loss on this position (% of portfolio)", 0.5, 10.0, 2.0, 0.5, key="max_loss",
                         help="If the exit level is hit, you lose at most this share of your whole portfolio.")
    portfolio = st.number_input("Portfolio value ($)", min_value=1000, value=25_000, step=1000, key="portfolio")

    st.subheader("Data", anchor=False)
    years = st.slider("Years of history to analyse", 3, 25, 10, key="years")
    source = st.selectbox("Data source", ["auto", "yfinance", "stooq", "cache", "sample"], key="source",
                          help="auto = live data with offline fallbacks. 'sample' uses the bundled historical pack (no internet needed).")
    deep = st.checkbox("Run deep evaluation (layers, sensitivity, out-of-sample)", value=False, key="deep",
                       help="Adds ~30-60 seconds: strategy layer attribution, threshold sensitivity grid and a walk-forward test.")
    with st.expander("Expert settings"):
        entry = st.number_input("Entry score", 40, 90, 60, key="entry", help="Technical score needed to open a position in the backtest")
        exit_ = st.number_input("Exit score", 10, 60, 35 if horizon == "long" else 40, key="exit", help="Technical score at which an open position is closed")
        cost = st.number_input("Cost per side (bps)", 0, 50, 5, key="cost", help="Commission in basis points; slippage of 5 bps is added on top")
        as_of = st.date_input("Time machine: as-of date", value=None, min_value=date(1990, 1, 1), max_value=date.today(), key="as_of",
                              help="What would Lighthouse have said on this day? Leave empty for today.")
        f_json = st.text_area("Fundamental overrides (JSON)", value="", key="f_json",
                              help='Type your own numbers when a feed is missing a field, e.g. {"pe_trailing": 22, "roe": 0.18}')
    run = st.button("Analyse", type="primary", width="stretch", key="analyse")
    if _is_offline():
        st.info("Offline sample pack: " + ", ".join(t for t in sample_pack_tickers() if not t.startswith("^")))
    st.caption("Decision-support tool, not investment advice.")

run = run or st.session_state.pop("auto_run", False)

# ----------------------------------------------------------------------------- run an analysis
if run:
    overrides = {}
    if f_json.strip():
        try:
            overrides = json.loads(f_json)
        except json.JSONDecodeError as exc:
            st.error(f"Could not parse the fundamental overrides JSON: {exc}")
    with st.spinner(f"Collecting data and analysing {ticker} ..."):
        try:
            res = _run(ticker, horizon, risk, max_loss, portfolio, years, source, entry, exit_, cost, overrides,
                       str(as_of) if as_of else None)
            st.session_state["result"] = res
            st.session_state.pop("result_error", None)
        except Exception as exc:  # noqa: BLE001
            st.session_state.pop("result", None)
            st.session_state["result_error"] = (exc, ticker)
    res = st.session_state.get("result")
    if res is not None and deep and "layers" not in res.extras:
        with st.status("Deep evaluation: layer attribution, sensitivity grid and walk-forward test ...", expanded=False) as status:
            res.evaluate()
            status.update(label="Deep evaluation complete", state="complete")

# ----------------------------------------------------------------------------- tabs
tab_analyse, tab_screen, tab_data, tab_about = st.tabs(["📍 Analyse a stock", "📋 Rank & compare a watchlist", "🗂️ Data behind the verdict", "ℹ️ How it works"])


# ============================================================================= ANALYSE
STATUS_COLOR_FOR_REGIME = {"Bull / Calm": VERDICT_COLOR["Strong Buy"], "Bull / Turbulent": VERDICT_COLOR["Hold"],
                           "Bear / Calm": VERDICT_COLOR["Reduce"], "Bear / Turbulent": VERDICT_COLOR["Sell"]}


def render_result(res) -> None:
    d, f, pl, prof = res.decision, res.fundamentals, res.decision.plan, res.profile
    color = VERDICT_COLOR[d.verdict]

    # ---- headline ---------------------------------------------------------
    h1, h2 = st.columns([3, 1.3])
    with h1:
        st.markdown(f"<h2 style='margin:0 0 .3rem 0'>{f.name or d.ticker} <span class='lh-muted' style='font-weight:400'>({d.ticker})</span> "
                    f"<span class='lh-verdict' style='background:{color}'>{d.verdict}</span></h2>"
                    f"<p class='lh-muted' style='margin:0'>{d.as_of} · ${d.price:,.2f} · {prof.describe()}</p>", unsafe_allow_html=True)
        st.info(pl.action, icon="🧭")
        if res.warnings:
            st.warning("**Read before acting**\n\n" + "\n".join(f"- {w}" for w in res.warnings), icon="⚠️")
    with h2:
        plotly(ix.score_gauge(d), key="gauge")

    k1, k2, k3, k4 = st.columns(4)
    kpi(k1, "Lighthouse score", f"{d.composite_score:.0f} / 100", "verdict bands: ≥72 Strong Buy · ≥60 Buy · ≥45 Hold · ≥32 Reduce", color)
    kpi(k2, "Fundamental score", "n/a" if d.fundamental_score is None else f"{d.fundamental_score:.0f}",
        f"weight {prof.fundamental_weight:.0%} · {f.sector or 'sector n/a'}")
    kpi(k3, "Technical score", f"{d.technical_score:.0f}", f"weight {prof.technical_weight:.0%} · {prof.horizon}-term rule")
    if d.regime:
        kpi(k4, "Market weather", d.regime.label, f"{'−' if d.regime_penalty else ''}{d.regime_penalty:.0f} pts · S&P 500 {d.regime.benchmark_vs_sma * 100:+.1f}% vs 200-day",
            STATUS_COLOR_FOR_REGIME.get(d.regime.label))
    else:
        kpi(k4, "Market weather", "n/a", "benchmark unavailable")

    # ---- score composition + position plan ---------------------------------
    c1, c2 = st.columns([1.1, 1.4])
    with c1:
        plotly(ix.score_waterfall(d, prof), key="waterfall")
        if d.regime:
            st.caption(d.regime.describe())
    with c2:
        st.markdown("#### Position plan")
        p1, p2 = st.columns(2)
        kpi(p1, "Suggested position", f"{pl.position_pct:.1f}%", f"${pl.position_value:,.0f} · {pl.shares} shares near ${pl.entry_price:,.2f}")
        kpi(p2, "Exit level", "n/a" if pl.stop_price is None else f"${pl.stop_price:,.2f}", "" if pl.stop_pct is None else f"{pl.stop_pct * 100:.1f}% below the price")
        p3, p4 = st.columns(2)
        kpi(p3, "Worst-case loss", f"${pl.max_loss_value:,.0f}", f"your limit: {prof.max_loss_pct:g}% = ${prof.portfolio_value * prof.max_loss_pct / 100:,.0f}")
        kpi(p4, "Binding rule", pl.binding_constraint, "the tightest of max-loss, volatility target and concentration cap")
        with st.expander("How the size was computed"):
            for r_ in pl.rationale:
                st.write("• " + r_)
        with st.expander("What if I change my limits? (instant, no new analysis)"):
            w1, w2, w3 = st.columns(3)
            w_loss = w1.slider("Max loss (% of portfolio)", 0.5, 10.0, float(prof.max_loss_pct), 0.5, key="wi_loss")
            w_port = w2.number_input("Portfolio ($)", 1000, value=int(prof.portfolio_value), step=1000, key="wi_port")
            w_risk = w3.select_slider("Risk", ["conservative", "balanced", "aggressive"], value=prof.risk, key="wi_risk")
            wp = resize_plan(res, w_loss, w_port, w_risk)
            exit_txt = "n/a" if wp.stop_price is None else f"\\${wp.stop_price:,.2f}"
            st.markdown(f"**{wp.position_pct:.1f}%** of the portfolio = **\\${wp.position_value:,.0f}** · **{wp.shares} shares** · "
                        f"exit {exit_txt} · worst case **\\${wp.max_loss_value:,.0f}** · limited by the **{wp.binding_constraint}**")
            st.caption("The verdict and score do not change here; only the sizing rules are re-applied.")

    # ---- beacons ----------------------------------------------------------
    st.markdown("#### Why: the beacons")
    st.caption("Every signal votes between −1 (bearish) and +1 (bullish). Hover a bar for the reading and the reason. Filter by light to focus.")
    flt = st.segmented_control("Show", ["all", "bullish", "neutral", "bearish"], default="all", key="beacon_filter", label_visibility="collapsed")
    want = {"all": None, "bullish": "green", "neutral": "amber", "bearish": "red"}[flt or "all"]
    shown = [b for b in d.beacons if want is None or b.light == want]
    if shown:
        plotly(ix.score_breakdown(d, beacons=shown), key="breakdown")
    else:
        st.info("No beacon has that light right now.")
    colf, colt = st.columns(2)
    with colf:
        st.markdown(f"**Fundamental beacons** · {f.sector or ''} · source: {f.source} ({f.as_of})")
        st.dataframe(beacon_frame([b for b in d.fundamental_beacons if b in shown]), hide_index=True, width="stretch", column_config=BEACON_COLUMNS)
    with colt:
        st.markdown("**Technical beacons**")
        st.dataframe(beacon_frame([b for b in d.technical_beacons if b in shown]), hide_index=True, width="stretch", column_config=BEACON_COLUMNS)

    # ---- charts -----------------------------------------------------------
    st.markdown("#### Price, trend and momentum")
    look = st.segmented_control("Lookback", ["1y", "3y", "5y", "all"], default="3y", key="lookback", label_visibility="collapsed")
    last_days = {"1y": 252, "3y": 756, "5y": 1260, "all": len(res.indicators)}[look or "3y"]
    st.caption("Shaded bands show market weather (red = bear, amber = turbulent). Triangles are the backtested rule's entries and exits. Drag to zoom, double-click to reset.")
    plotly(ix.dashboard(res, last_days=last_days), key="dashboard")

    # ---- backtest ---------------------------------------------------------
    bt = res.backtest
    if bt is not None:
        st.markdown(f"#### How this rule behaved {bt.start.date()} → {bt.end.date()}")
        st.caption(f"Long-only, reviewed {res.params.decision_frequency}, filled at the {bt.options['fills']}, "
                   f"{res.params.cost_bps + res.params.slippage_bps:.0f} bps per side. Returns are for the capital allocated to this stock. "
                   "Past behaviour of a rule is evidence about its design, not a promise.")
        m, mb = bt.metrics, bt.summary().loc["Buy & hold"]
        ratio = lambda v: "n/a" if pd.isna(v) else f"{v:.2f}"            # noqa: E731
        pct = lambda v, dp=0: "n/a" if pd.isna(v) else f"{v * 100:.{dp}f}%"  # noqa: E731
        b1, b2, b3, b4 = st.columns(4)
        kpi(b1, "Strategy CAGR", pct(m["cagr"], 1), f"buy & hold {pct(mb['cagr'], 1)}")
        kpi(b2, "Max drawdown", pct(m["max_drawdown"]), f"buy & hold {pct(mb['max_drawdown'])}")
        kpi(b3, "Sharpe", ratio(m["sharpe"]), f"buy & hold {ratio(mb['sharpe'])}")
        kpi(b4, "Trades", f"{int(m['n_trades'])}", f"win rate {pct(m['win_rate'])} · invested {pct(m['exposure'])} of the time")
        st.dataframe(format_metrics(bt.summary()), width="stretch")
        plotly(ix.backtest(bt, res.regimes), key="backtest")

        if "layers" not in res.extras:
            if st.button("Run deep evaluation (layers, sensitivity, out-of-sample) · ~30-60 s", key="deep_now"):
                with st.status("Deep evaluation running ...", expanded=False) as status:
                    res.evaluate()
                    status.update(label="Deep evaluation complete", state="complete")
                st.rerun()
        else:
            st.markdown("#### Deep evaluation")
            e1, e2 = st.tabs(["What each layer contributes", "Robustness"])
            with e1:
                st.caption("Each row adds one layer of the product on top of the previous one, so you can see what every rule is for.")
                st.dataframe(format_metrics(res.extras["layers"]), width="stretch")
                plotly(ix.layers(res.extras["layers"]), key="layers")
                rb = res.extras.get("regime_breakdown")
                if rb is not None and len(rb):
                    st.markdown("**Behaviour by market regime** (annualised daily returns)")
                    st.dataframe(format_metrics(rb), width="stretch")
            with e2:
                sens = res.extras.get("sensitivity")
                if sens is not None and len(sens):
                    metric = st.selectbox("Sensitivity metric", ["sharpe", "cagr", "max_drawdown", "win_rate"], key="sens_metric")
                    plotly(ix.sensitivity(sens, metric), key="sensitivity")
                    st.caption("A robust rule shows a plateau of similar values; a single bright cell means the thresholds were fitted to noise.")
                wf = res.extras.get("walk_forward")
                if wf is not None:
                    st.markdown(f"**Out-of-sample test** (thresholds tuned before {wf['split_date'].date()}, tested after)")
                    st.dataframe(format_metrics(wf["out_of_sample"]), width="stretch")
                    plotly(ix.walk_forward(wf), key="walk_forward")
        with st.expander(f"Trade list ({len(bt.trades)} trades)"):
            tf = bt.trades_frame()
            st.dataframe(tf, width="stretch", hide_index=True, column_config={
                "entry_date": st.column_config.DateColumn("Entry"), "exit_date": st.column_config.DateColumn("Exit"),
                "entry_price": st.column_config.NumberColumn("Entry $", format="$%.2f"), "exit_price": st.column_config.NumberColumn("Exit $", format="$%.2f"),
                "size_fraction": st.column_config.NumberColumn("Size", format="percent"), "return_pct": st.column_config.NumberColumn("Return", format="percent"),
                "pnl_pct": st.column_config.NumberColumn("Sleeve P&L", format="percent"), "holding_days": st.column_config.NumberColumn("Days held"),
                "exit_reason": st.column_config.TextColumn("Exit reason")})

    # ---- downloads --------------------------------------------------------
    st.markdown("#### Take it with you")
    dl1, dl2, dl3, dl4 = st.columns(4)
    dl1.download_button("Full report (Markdown)", res.report(), file_name=f"{d.ticker}_lighthouse_report.md", width="stretch")
    dl2.download_button("Beacons (CSV)", beacon_frame(d.beacons).to_csv(index=False), file_name=f"{d.ticker}_beacons.csv", width="stretch")
    if bt is not None:
        dl3.download_button("Trades (CSV)", bt.trades_frame().to_csv(index=False), file_name=f"{d.ticker}_trades.csv", width="stretch")
    dl4.download_button("Prices & indicators (CSV)", res.indicators.to_csv(), file_name=f"{d.ticker}_indicators.csv", width="stretch")
    st.caption("Every chart also has a camera icon (top-right on hover) that saves it as a PNG.")


with tab_analyse:
    err = st.session_state.get("result_error")
    res = st.session_state.get("result")
    if err is not None:
        show_error(*err)
    if res is not None:
        if st.session_state.get("as_of"):
            st.info(f"Time machine: this is what Lighthouse would have said on {res.decision.as_of}, using only data available then.", icon="🕰️")
        render_result(res)
    elif err is None:
        st.markdown("### Tell Lighthouse who you are, pick a ticker, press **Analyse**.")
        st.markdown("You get a verdict, the *why* behind it (every signal is a beacon you can inspect), a position plan sized to your "
                    "maximum acceptable loss, and an honest backtest of the rule that produced the recommendation.")
        g1, g2, g3 = st.columns(3)
        g1.markdown("**1 · Who you are**\n\nHorizon, risk tolerance, the most you accept losing on one position, and your portfolio size (sidebar).")
        g2.markdown("**2 · Press Analyse**\n\nLighthouse collects prices and fundamentals, reads the market weather and scores 19 beacons.")
        g3.markdown("**3 · Explore**\n\nHover every chart, filter the beacons, test what-if limits, rank a watchlist, download everything.")
        st.caption("No internet? Choose the `sample` data source in the sidebar: it ships with real historical data for 20 stocks.")

# ============================================================================= WATCHLIST
with tab_screen:
    st.markdown("### Rank a watchlist")
    st.caption("Every ticker is scored with your profile; the table is sortable, and selecting a row opens a summary card.")
    preset = st.pills("Presets", list(PRESET_WATCHLISTS), selection_mode="single", key="wl_preset", label_visibility="collapsed")
    default_wl = PRESET_WATCHLISTS["Sample pack (offline)"] if _is_offline() else PRESET_WATCHLISTS["Mega caps"]
    wl = st.text_input("Tickers (space or comma separated)", value=PRESET_WATCHLISTS.get(preset, default_wl) if preset else default_wl, key="wl_text")
    if st.button("Rank watchlist", type="primary", key="rank"):
        tickers = tuple(dict.fromkeys(t.upper() for t in wl.replace(",", " ").split() if t))
        with st.spinner(f"Analysing {len(tickers)} tickers ..."):
            try:
                st.session_state["screen_df"] = _screen(tickers, horizon, risk, max_loss, portfolio, min(years, 6), source)
            except Exception as exc:  # noqa: BLE001
                show_error(exc, ", ".join(tickers))
    sdf = st.session_state.get("screen_df")
    if sdf is not None:
        ok = sdf["score"].notna().sum()
        bad = sdf[sdf["error"].astype(str) != ""]
        if len(bad):
            st.warning("Could not score: " + "; ".join(f"**{r.ticker}** ({r.error})" for r in bad.itertuples()), icon="⚠️")
        if ok:
            plotly(ix.screener(sdf), key="screener")
        show = sdf[["ticker", "name", "verdict", "score", "fundamental", "technical", "regime", "position_pct", "price", "stop", "pe", "roe", "rev_growth", "sector"]].copy()
        for c in ("score", "fundamental", "technical", "position_pct", "price", "stop", "pe", "roe", "rev_growth"):
            show[c] = pd.to_numeric(show[c], errors="coerce")   # None -> blank cell instead of the word "None"
        event = st.dataframe(show, width="stretch", on_select="rerun", selection_mode="single-row", key="wl_table", column_config={
            "ticker": "Ticker", "name": "Company", "verdict": "Verdict", "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
            "fundamental": st.column_config.NumberColumn("Fund.", format="%.0f"), "technical": st.column_config.NumberColumn("Tech.", format="%.0f"),
            "regime": "Weather", "position_pct": st.column_config.NumberColumn("Position %", format="%.1f %%"),
            "price": st.column_config.NumberColumn("Price", format="$%.2f"), "stop": st.column_config.NumberColumn("Exit level", format="$%.2f"),
            "pe": st.column_config.NumberColumn("P/E", format="%.1f"), "roe": st.column_config.NumberColumn("ROE", format="percent"),
            "rev_growth": st.column_config.NumberColumn("Rev. growth", format="percent"), "sector": "Sector"})
        rows = event.selection.rows if event and event.selection else []
        if rows:
            r = sdf.iloc[rows[0]]
            if pd.notna(r["score"]):
                vc = VERDICT_COLOR.get(r["verdict"], "#9a9892")
                st.markdown(f"<h4 style='margin:.4rem 0'>{r['name']} ({r['ticker']}) <span class='lh-verdict' style='background:{vc}'>{r['verdict']}</span> "
                            f"<span class='lh-muted' style='font-weight:400;font-size:1rem'>score {r['score']:.0f} · fundamental {r['fundamental'] if pd.notna(r['fundamental']) else 'n/a'} · "
                            f"technical {r['technical']:.0f} · weather {r['regime']} · suggested position {r['position_pct']:.1f}%</span></h4>", unsafe_allow_html=True)
                st.button(f"Open the full analysis of {r['ticker']} (in the Analyse tab)", key="open_full", on_click=_open_from_watchlist, args=(r["ticker"],))
            else:
                st.info(f"{r['ticker']} could not be scored: {r['error']}")
        st.download_button("Download ranking (CSV)", sdf.to_csv(), file_name="lighthouse_watchlist.csv")

        st.markdown("#### Compare price performance")
        scored = list(sdf.loc[sdf["score"].notna(), "ticker"])
        chosen = st.multiselect("Tickers to compare", scored + (["^GSPC"] if "^GSPC" not in scored else []), default=scored[:5], key="cmp")
        if chosen:
            series = {}
            for t in chosen:
                try:
                    series["S&P 500" if t == "^GSPC" else t] = _closes(t, min(years, 6), source)
                except Exception as exc:  # noqa: BLE001
                    st.caption(f"{t}: {exc}")
            if series:
                plotly(ix.compare_prices(series), key="compare")

# ============================================================================= DATA
with tab_data:
    res = st.session_state.get("result")
    if res is None:
        st.info("Analyse a stock first; this tab then shows every number behind its verdict.")
    else:
        d, f = res.decision, res.fundamentals
        st.markdown(f"### Data behind {d.ticker}")
        s1, s2, s3 = st.columns(3)
        kpi(s1, "Prices", f"{len(res.prices):,} trading days", f"{res.prices.index[0].date()} → {res.prices.index[-1].date()} · source: {res.prices.attrs.get('source', '?')}")
        kpi(s2, "Fundamentals", f.source, f"as of {f.as_of} · {f.sector or 'sector n/a'}")
        kpi(s3, "Benchmark", "S&P 500" if res.benchmark is not None else "unavailable", f"{len(res.benchmark):,} days" if res.benchmark is not None else "regime rules disabled")
        rep = res.prices.attrs.get("cleaning")
        if rep is not None and getattr(rep, "notes", None):
            st.markdown("**Cleaning report**")
            for n in rep.notes:
                st.write("• " + n)
        t1, t2, t3 = st.tabs(["Fundamental snapshot", "Prices & indicators", "Market weather history"])
        with t1:
            snap = pd.DataFrame({"field": list(asdict(f).keys()), "value": [str(v) for v in asdict(f).values()]})
            st.dataframe(snap[~snap["field"].isin(["notes"])], hide_index=True, width="stretch")
            if res.statements is not None and getattr(res.statements, "annual", None) is not None:
                st.markdown("**Annual statements used for the point-in-time scores**")
                st.dataframe(res.statements.annual, width="stretch")
        with t2:
            cols = st.multiselect("Columns", list(res.indicators.columns), default=["Open", "High", "Low", "Close", "Volume", "sma_fast", "sma_slow", "rsi", "macd", "atr"], key="ind_cols")
            table = res.indicators[cols].tail(500).sort_index(ascending=False)
            table.attrs = {}   # the cleaning report in attrs is not JSON-serialisable
            st.dataframe(table, width="stretch")
            st.download_button("Download all rows (CSV)", res.indicators.to_csv(), file_name=f"{d.ticker}_indicators.csv", key="dl_ind2")
        with t3:
            if res.regimes is not None:
                rg = res.regimes.dropna(subset=["regime"])
                share = rg["regime"].value_counts(normalize=True).rename("share of days").to_frame()
                share["share of days"] = share["share of days"].map(lambda v: f"{v * 100:.0f}%")
                st.dataframe(share, width="stretch")
                st.dataframe(rg[["close", "sma", "vol", "vol_median", "regime"]].tail(250).sort_index(ascending=False), width="stretch")
            else:
                st.info("No benchmark data, so no market-weather history.")

# ============================================================================= ABOUT
with tab_about:
    st.markdown("### How Lighthouse decides")
    a1, a2 = st.columns([1.2, 1])
    with a1:
        st.markdown("""
1. **Collect & clean** daily prices, the S&P 500 and fundamentals (live sources first, then cache, then the bundled sample pack).
2. **Fundamental beacons** (13): valuation, quality and growth ratios scored against sector norms.
3. **Technical beacons** (6): trend, RSI, MACD, Bollinger %B, volume confirmation, 52-week-high proximity.
4. **Market weather**: Bull/Bear × Calm/Turbulent for the S&P 500 removes points, raises the entry bar and halves sizes in bear markets.
5. **Decision**: horizon-weighted blend → verdict → position size from the *smallest* of three rules (max-loss, volatility target, concentration cap).
6. **Honest evaluation**: point-in-time backtest vs buy & hold and the S&P 500, layer attribution, sensitivity grid, walk-forward test.
""")
    with a2:
        bands = pd.DataFrame([(f"≥ {t}", v) for t, v in VERDICT_BANDS], columns=["Lighthouse score", "Verdict"])
        st.markdown("**Verdict bands**")
        st.dataframe(bands, hide_index=True, width="stretch")
        pres = pd.DataFrame(RISK_PRESETS).T.rename(columns={"max_position_pct": "max position %", "target_vol": "target volatility", "stop_atr_multiple": "stop (× ATR)"})
        st.markdown("**Risk presets**")
        st.dataframe(pres, width="stretch")
        hw = pd.DataFrame(HORIZON_WEIGHTS, index=["fundamental weight", "technical weight"]).T
        st.markdown("**Horizon weights**")
        st.dataframe(hw, width="stretch")
    guide = os.path.join(os.path.dirname(__file__), "docs", "USER_GUIDE.md")
    with st.expander("Full user guide", expanded=False):
        st.markdown(open(guide).read() if os.path.exists(guide) else "See docs/USER_GUIDE.md")
    st.caption("Lighthouse is decision support, not investment advice. Sector norms are assumptions, the backtest ignores taxes, and the evaluation universe is survivorship-biased.")
