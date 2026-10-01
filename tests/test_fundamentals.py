import numpy as np
import pandas as pd

from lighthouse.fundamentals import (FundamentalSnapshot, StatementHistory, fundamental_beacons, fundamental_score,
                                     point_in_time_scores, score_multiple_vs_norm, score_band)
from lighthouse.config import StrategyParams


def snap(**kw):
    base = dict(ticker="X", name="X Corp", sector="Technology", price=100, market_cap=1e10, pe_trailing=20, pe_forward=18,
                peg=1.0, pb=5, ev_ebitda=15, roe=0.25, operating_margin=0.30, debt_to_equity=0.5, current_ratio=2,
                fcf_yield=0.05, revenue_growth=0.15, eps_growth=0.20)
    base.update(kw)
    return FundamentalSnapshot(**base)


def test_scoring_helpers():
    assert score_multiple_vs_norm(10, 20) == 1.0          # half the norm -> cheap
    assert score_multiple_vs_norm(30, 20) == -1.0         # 1.5x the norm -> expensive
    assert score_multiple_vs_norm(20, 20) == 0.0
    assert score_multiple_vs_norm(-5, 20) == -0.75        # negative earnings
    assert score_multiple_vs_norm(None, 20) is None
    assert score_band(0.25, 0.10, 0.15) == 1.0
    assert score_band(-0.05, 0.10, 0.15) == -1.0


def test_strong_company_scores_high_and_weak_scores_low():
    strong = fundamental_score(fundamental_beacons(snap()))
    weak = fundamental_score(fundamental_beacons(snap(pe_trailing=60, peg=4, pb=15, ev_ebitda=40, roe=-0.1,
                                                      operating_margin=-0.05, debt_to_equity=3, fcf_yield=-0.02,
                                                      revenue_growth=-0.2, eps_growth=-0.5)))
    assert strong > 75 and weak < 25


def test_group_weights_sum_to_one_and_missing_groups_renormalise():
    beacons = fundamental_beacons(snap())
    assert abs(sum(b.weight for b in beacons) - 1.0) < 1e-9
    only_quality = fundamental_beacons(snap(pe_trailing=None, pe_forward=None, peg=None, pb=None, ev_ebitda=None,
                                            revenue_growth=None, eps_growth=None))
    assert all(b.key.startswith("quality") for b in only_quality)
    assert fundamental_score(only_quality) is not None


def test_unavailable_snapshot_gives_none():
    assert fundamental_score(fundamental_beacons(FundamentalSnapshot(ticker="Z"))) is None
    assert not FundamentalSnapshot(ticker="Z").available


def test_financials_skip_leverage_beacon():
    b = fundamental_beacons(snap(sector="Financial Services", debt_to_equity=5))
    assert not any(x.key.endswith("leverage") for x in b)


def test_point_in_time_scores_respect_reporting_lag(prices):
    annual = pd.DataFrame({"revenue": [100, 120], "net_income": [10, 15], "eps": [1.0, 1.5], "operating_income": [20, 28],
                           "equity": [50, 60], "total_debt": [20, 20]},
                          index=pd.to_datetime(["2016-12-31", "2017-12-31"]))
    hist = StatementHistory(annual=annual)
    p = StrategyParams(reporting_lag_days=90)
    s = point_in_time_scores(prices, hist, "Technology", p)
    first_known = pd.Timestamp("2016-12-31") + pd.Timedelta(days=90)
    assert s[s.index < first_known].isna().all()
    assert s[s.index >= first_known].notna().all()
    assert s.dropna().between(0, 100).all()
    # valuation moves daily with price: scores are not constant within a statement window
    window = s[(s.index >= first_known) & (s.index < pd.Timestamp("2017-12-31") + pd.Timedelta(days=90))]
    assert window.std() > 0
