"""
Lighthouse - Streamlit app for non-programmers.

    streamlit run app.py
"""
from __future__ import annotations

import os
import traceback

import pandas as pd
import streamlit as st

from lighthouse import analyze, screen, InvestorProfile, StrategyParams
from lighthouse import charts as ch
from lighthouse.data import sample_pack_tickers, offline_mode
from lighthouse.report import format_metrics, LIGHT_ICON

st.set_page_config(page_title="Lighthouse · explainable stock decisions", page_icon="🔦", layout="wide")

VERDICT_COLOR = {"Strong Buy": "#0ca30c", "Buy": "#1baf7a", "Hold": "#fab219", "Reduce": "#ec835a", "Sell": "#d03b3b"}

# ----------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("🔦 Lighthouse")
    st.caption("A clear signal in a noisy market. Explainable Buy / Hold / Sell decisions for self-directed investors.")
    ticker = st.text_input("Ticker", value="MSFT").upper().strip()
    horizon = st.radio("Investment horizon", ["long", "short"], format_func=lambda h: "Long-term (6-24+ months)" if h == "long" else "Short-term (weeks to months)")
    risk = st.select_slider("Risk tolerance", ["conservative", "balanced", "aggressive"], value="balanced")
    max_loss = st.slider("Max acceptable loss on this position (% of portfolio)", 0.5, 10.0, 2.0, 0.5)
    portfolio = st.number_input("Portfolio value ($)", min_value=1000, value=25_000, step=1000)
    years = st.slider("Years of history to analyse", 3, 25, 10)
    source = st.selectbox("Data source", ["auto", "yfinance", "stooq", "cache", "sample"],
                          help="auto = live data with offline fallbacks. 'sample' uses the bundled historical pack.")
    deep = st.checkbox("Run deep evaluation (layers, sensitivity, out-of-sample)", value=False,
                       help="Adds ~30-60 seconds: strategy layer attribution, threshold sensitivity grid and a walk-forward test.")
    with st.expander("Expert settings"):
        entry = st.number_input("Entry score", 40, 90, 60)
        exit_ = st.number_input("Exit score", 10, 60, 35 if horizon == "long" else 40)
        cost = st.number_input("Cost per side (bps)", 0, 50, 5)
        f_json = st.text_area("Fundamental overrides (JSON)", value="", help='e.g. {"pe_trailing": 22, "roe": 0.18}')
    run = st.button("Analyse", type="primary", use_container_width=True)
    if offline_mode() or source == "sample":
        st.info(f"Offline sample pack: {', '.join(t for t in sample_pack_tickers() if not t.startswith('^'))}")
    st.caption("Decision-support tool, not investment advice.")

# ----------------------------------------------------------------------------- helpers
@st.cache_data(show_spinner=False, ttl=3600)
def _run(ticker, horizon, risk, max_loss, portfolio, years, source, entry, exit_, cost, overrides, deep):
    prof = InvestorProfile(horizon=horizon, risk=risk, max_loss_pct=max_loss, portfolio_value=portfolio)
    params = StrategyParams.for_horizon(horizon, entry_score=entry, exit_score=exit_, cost_bps=cost)
    res = analyze(ticker, prof, params, years=years, source=source, fundamentals_overrides=overrides or None)
    if deep:
        res.evaluate()
    return res


def beacon_frame(beacons) -> pd.DataFrame:
    rows = [{"": LIGHT_ICON[b.light], "Signal": b.name, "Reading": b.value,
             "Vote": "n/a" if pd.isna(b.score) else f"{b.score:+.2f}", "Weight": f"{b.weight * 100:.0f}%",
             "Points": "n/a" if pd.isna(b.score) else f"{b.contribution:+.1f}", "Why": b.rationale} for b in beacons]
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- main
tab_analyse, tab_screen, tab_about = st.tabs(["📍 Analyse a stock", "📋 Rank a watchlist", "ℹ️ How it works"])

with tab_analyse:
    if not run and "result" not in st.session_state:
        st.markdown("### Tell Lighthouse who you are, pick a ticker, press **Analyse**.")
        st.markdown("You get a verdict, the *why* behind it (every signal is a beacon you can inspect), a position plan sized to "
                    "your maximum acceptable loss, and an honest backtest of the rule that produced the recommendation.")
    if run:
        overrides = {}
        if f_json.strip():
            import json
            try:
                overrides = json.loads(f_json)
            except json.JSONDecodeError as exc:
                st.error(f"Could not parse overrides JSON: {exc}")
        with st.spinner(f"Collecting data and analysing {ticker} ..."):
            try:
                st.session_state["result"] = _run(ticker, horizon, risk, max_loss, portfolio, years, source, entry, exit_, cost, overrides, deep)
            except Exception as exc:  # noqa: BLE001
                st.error(f"{type(exc).__name__}: {exc}")
                st.code(traceback.format_exc())
    res = st.session_state.get("result")
    if res is not None:
        d, f, pl = res.decision, res.fundamentals, res.decision.plan
        c1, c2, c3, c4, c5 = st.columns([2, 1, 1, 1, 1])
        c1.markdown(f"<h2 style='margin:0'>{f.name or d.ticker} <span style='color:{VERDICT_COLOR[d.verdict]}'>· {d.verdict}</span></h2>"
                    f"<p style='color:#52514e;margin:0'>{d.as_of} · ${d.price:,.2f} · {d.profile.describe()}</p>", unsafe_allow_html=True)
        c2.metric("Lighthouse score", f"{d.composite_score:.0f} / 100")
        c3.metric("Fundamental", "n/a" if d.fundamental_score is None else f"{d.fundamental_score:.0f}")
        c4.metric("Technical", f"{d.technical_score:.0f}")
        c5.metric("Market weather", d.regime.label if d.regime else "n/a", delta=f"{-d.regime_penalty:+.0f} pts" if d.regime else None, delta_color="off")
        st.info(pl.action)
        for w in res.warnings:
            st.warning(w)

        st.markdown("#### Position plan")
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Suggested position", f"{pl.position_pct:.1f}%", f"${pl.position_value:,.0f} · {pl.shares} shares", delta_color="off")
        p2.metric("Exit level", "n/a" if pl.stop_price is None else f"${pl.stop_price:,.2f}", None if pl.stop_pct is None else f"{pl.stop_pct * 100:.1f}% below", delta_color="off")
        p3.metric("Worst-case loss", f"${pl.max_loss_value:,.0f}", f"limit {d.profile.max_loss_pct:g}% = ${d.profile.portfolio_value * d.profile.max_loss_pct / 100:,.0f}", delta_color="off")
        p4.metric("Binding rule", pl.binding_constraint)
        with st.expander("How the size was computed"):
            for r_ in pl.rationale:
                st.write("• " + r_)

        st.markdown("#### Why: the beacons")
        st.pyplot(ch.plot_score_breakdown(d), use_container_width=True)
        colf, colt = st.columns(2)
        with colf:
            st.markdown(f"**Fundamental beacons** · {f.sector or ''} · source: {f.source} ({f.as_of})")
            st.dataframe(beacon_frame(d.fundamental_beacons), hide_index=True, use_container_width=True)
        with colt:
            st.markdown("**Technical beacons**")
            st.dataframe(beacon_frame(d.technical_beacons), hide_index=True, use_container_width=True)
        if d.regime:
            st.caption(d.regime.describe())

        st.markdown("#### Charts")
        st.pyplot(ch.plot_dashboard(res), use_container_width=True)

        if res.backtest is not None:
            bt = res.backtest
            st.markdown(f"#### How this rule behaved {bt.start.date()} → {bt.end.date()}")
            st.caption(f"Long-only, reviewed {res.params.decision_frequency}, filled at the {bt.options['fills']}, "
                       f"{res.params.cost_bps + res.params.slippage_bps:.0f} bps per side. Returns are for the capital allocated to this stock.")
            st.dataframe(format_metrics(bt.summary()), use_container_width=True)
            st.pyplot(ch.plot_backtest(bt, res.regimes), use_container_width=True)
            if "layers" in res.extras:
                st.markdown("**What each layer contributes**")
                st.dataframe(format_metrics(res.extras["layers"]), use_container_width=True)
                st.pyplot(ch.plot_layers(res.extras["layers"]), use_container_width=True)
            if "regime_breakdown" in res.extras and len(res.extras["regime_breakdown"]):
                st.markdown("**Behaviour by market regime**")
                st.dataframe(format_metrics(res.extras["regime_breakdown"]), use_container_width=True)
            if "sensitivity" in res.extras:
                st.markdown("**Sensitivity to thresholds**")
                st.pyplot(ch.plot_sensitivity(res.extras["sensitivity"]), use_container_width=True)
            if "walk_forward" in res.extras:
                wf = res.extras["walk_forward"]
                st.markdown(f"**Out-of-sample test** (tuned before {wf['split_date'].date()}, tested after)")
                st.dataframe(format_metrics(wf["out_of_sample"]), use_container_width=True)
                st.pyplot(ch.plot_walk_forward(wf), use_container_width=True)
            with st.expander("Trade list"):
                st.dataframe(bt.trades_frame(), use_container_width=True)

        st.download_button("Download full report (Markdown)", res.report(), file_name=f"{d.ticker}_lighthouse_report.md")

with tab_screen:
    st.markdown("### Rank a watchlist")
    default = "MSFT AAPL JNJ XOM JPM UNH" if (offline_mode() or source == "sample") else "AAPL MSFT NVDA AMZN GOOGL JPM XOM UNH"
    wl = st.text_input("Tickers (space or comma separated)", value=default)
    if st.button("Rank watchlist"):
        tickers = [t for t in wl.replace(",", " ").split() if t]
        prof = InvestorProfile(horizon=horizon, risk=risk, max_loss_pct=max_loss, portfolio_value=portfolio)
        with st.spinner("Analysing watchlist ..."):
            df = screen(tickers, prof, StrategyParams.for_horizon(horizon), years=min(years, 6), source=source, progress=False)
        st.dataframe(df, use_container_width=True)
        if df["score"].notna().any():
            st.pyplot(ch.plot_screener(df), use_container_width=True)
        st.download_button("Download ranking (CSV)", df.to_csv(), file_name="lighthouse_watchlist.csv")

with tab_about:
    st.markdown(open(os.path.join(os.path.dirname(__file__), "docs", "USER_GUIDE.md")).read()
                if os.path.exists(os.path.join(os.path.dirname(__file__), "docs", "USER_GUIDE.md")) else "See docs/USER_GUIDE.md")
