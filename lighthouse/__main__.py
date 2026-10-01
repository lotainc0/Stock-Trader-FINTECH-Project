"""Command-line interface.

  python -m lighthouse analyze MSFT --horizon long --risk balanced --max-loss 2 --portfolio 25000 --out reports
  python -m lighthouse screen AAPL MSFT NVDA JPM --risk conservative
  python -m lighthouse backtest MSFT --years 15 --evaluate
"""
from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from .config import InvestorProfile, StrategyParams
from .engine import analyze
from .report import format_metrics
from .screener import screen


def _profile(a) -> InvestorProfile:
    return InvestorProfile(horizon=a.horizon, risk=a.risk, max_loss_pct=a.max_loss, portfolio_value=a.portfolio)


def _params(a) -> StrategyParams:
    p = StrategyParams()
    if getattr(a, "params", None):
        p = p.copy(**json.loads(a.params))
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lighthouse", description="Lighthouse - explainable stock decisions")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--horizon", choices=["long", "short"], default="long")
        sp.add_argument("--risk", choices=["conservative", "balanced", "aggressive"], default="balanced")
        sp.add_argument("--max-loss", type=float, default=2.0, help="max loss per position, %% of portfolio")
        sp.add_argument("--portfolio", type=float, default=10_000)
        sp.add_argument("--years", type=float, default=10)
        sp.add_argument("--source", default="auto", help="auto | yfinance | stooq | cache | sample")
        sp.add_argument("--params", help='JSON overrides, e.g. \'{"entry_score": 65}\'')

    a1 = sub.add_parser("analyze", help="full analysis of one ticker"); common(a1)
    a1.add_argument("ticker"); a1.add_argument("--out", help="folder for report + charts")
    a1.add_argument("--evaluate", action="store_true", help="also run layer attribution, sensitivity and walk-forward tests")
    a1.add_argument("--as-of", help="YYYY-MM-DD: what would Lighthouse have said on that day?")
    a1.add_argument("--fundamentals-json", help="JSON fixture/override file for fundamentals")

    a2 = sub.add_parser("screen", help="rank a watchlist"); common(a2)
    a2.add_argument("tickers", nargs="+"); a2.add_argument("--csv", help="write the ranking to this file")

    a3 = sub.add_parser("backtest", help="backtest the rule on one ticker"); common(a3)
    a3.add_argument("ticker"); a3.add_argument("--evaluate", action="store_true")

    a = ap.parse_args(argv)
    pd.set_option("display.width", 160); pd.set_option("display.max_columns", 30)

    if a.cmd in {"analyze", "backtest"}:
        res = analyze(a.ticker, _profile(a), _params(a), years=a.years, source=a.source,
                      as_of=getattr(a, "as_of", None), fundamentals_fixture=getattr(a, "fundamentals_json", None))
        if a.evaluate:
            print("running layer attribution, sensitivity grid and walk-forward test ...", file=sys.stderr)
            res.evaluate()
        if a.cmd == "analyze":
            print(res.report())
            if a.out:
                for pth in res.save(a.out):
                    print(f"wrote {pth}", file=sys.stderr)
        else:
            if res.backtest is None:
                print("backtest unavailable"); return 1
            print(format_metrics(res.backtest.summary()).to_string())
            if "layers" in res.extras:
                print("\nLayer attribution\n" + format_metrics(res.extras["layers"]).to_string())
            if "walk_forward" in res.extras:
                wf = res.extras["walk_forward"]
                print(f"\nOut-of-sample after {wf['split_date'].date()}\n" + format_metrics(wf["out_of_sample"]).to_string())
        return 0

    if a.cmd == "screen":
        df = screen(a.tickers, _profile(a), _params(a), years=a.years, source=a.source)
        show = df[["ticker", "name", "verdict", "score", "fundamental", "technical", "regime", "position_pct", "price", "error"]]
        print(show.to_string())
        if a.csv:
            df.to_csv(a.csv)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
