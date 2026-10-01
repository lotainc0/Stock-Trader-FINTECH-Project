"""
Provenance of data/sample (the offline sample pack).

All price files are REAL historical data that ships inside open-source Python packages,
so the product, tests and notebook run with no internet connection:

  * MSFT.csv   - Microsoft daily OHLCV 1986-03-13 .. 2017-11-10 (split adjusted),
                 from `pmdarima.datasets.load_msft()` (pmdarima/datasets/data/msft.tar.gz)
  * 19 others  - daily *adjusted close only* 1990-01-02 .. 2022-12-28 for AAPL, AMD, BAC, BBY, CVX, GE, HD,
                 JNJ, JPM, KO, LLY, MRK, PEP, PFE, PG, RRC, UNH, WMT, XOM from `skfolio.datasets.load_sp500_dataset()`
                 (Open/High/Low are set to the close and Volume is empty - volume signals are disabled for these)
  * ^GSPC.csv  - S&P 500 index level 1990-01-02 .. 2022-12-28 from `skfolio.datasets.load_sp500_index()`

The fundamentals fixture (data/sample/fundamentals/MSFT.json) is NOT a data feed: it is an approximate,
hand-compiled snapshot (see its _README field) used only for offline demonstration and tests.

Run:  pip download --no-deps pmdarima skfolio -d wheels && python scripts/build_sample_pack.py wheels
"""
import gzip
import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "data" / "sample" / "prices"


def main(wheel_dir: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    wheels = {p.name.split("-")[0]: p for p in Path(wheel_dir).glob("*.whl")}
    with zipfile.ZipFile(wheels["pmdarima"]) as z:
        with tarfile.open(fileobj=io.BytesIO(z.read("pmdarima/datasets/data/msft.tar.gz"))) as tar:
            member = next(m for m in tar.getmembers() if m.name.endswith("msft.csv"))
            msft = pd.read_csv(tar.extractfile(member), parse_dates=["Date"]).drop(columns=["OpenInt"])
    msft.sort_values("Date").drop_duplicates("Date").to_csv(OUT / "MSFT.csv", index=False)
    with zipfile.ZipFile(wheels["skfolio"]) as z:
        sp = pd.read_csv(gzip.open(io.BytesIO(z.read("skfolio/datasets/data/sp500_dataset.csv.gz"))), parse_dates=["Date"])
        ix = pd.read_csv(gzip.open(io.BytesIO(z.read("skfolio/datasets/data/sp500_index.csv.gz"))), parse_dates=["Date"])
    for t in sp.columns[1:]:
        if t == "MSFT":
            continue
        s = sp[["Date", t]].dropna().sort_values("Date")
        pd.DataFrame({"Date": s["Date"], "Open": s[t], "High": s[t], "Low": s[t], "Close": s[t], "Volume": pd.NA}).to_csv(OUT / f"{t}.csv", index=False)
    ix = ix.sort_values("Date")
    pd.DataFrame({"Date": ix["Date"], "Open": ix["SP500"], "High": ix["SP500"], "Low": ix["SP500"], "Close": ix["SP500"], "Volume": pd.NA}).to_csv(OUT / "^GSPC.csv", index=False)
    print("sample pack written to", OUT)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "wheels")
