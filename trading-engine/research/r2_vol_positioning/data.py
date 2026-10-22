"""
data.py — R2's free data, downloaded once into research/_data/ (git-ignored) and read
from there after: Cboe's daily index histories and the CFTC's Commitments of Traders.

    python data.py        # fetch what isn't cached yet, then print each source's coverage

Downloads are data only: archives are unzipped into their own folder and nothing in
them is run.
"""
from __future__ import annotations

import io
import time
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "_data"
CBOE_DIR = DATA / "cboe"
CFTC_DIR = DATA / "cftc"
USER_AGENT = "DV02 paper-fund research (github.com/samgeng14/Trading-Workspace; samcygeng.314@gmail.com)"

CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv"
CBOE_INDICES = ("VIX", "VIX3M", "VIX9D", "VVIX", "SKEW")
# legacy futures-only reports: one archive for 1986-2016, then one a year
CFTC_COMBINED = "https://www.cftc.gov/files/dea/history/deacot1986_2016.zip"
CFTC_YEAR = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(url)


def fetch_cboe(refresh: bool = False) -> None:
    CBOE_DIR.mkdir(parents=True, exist_ok=True)
    for name in CBOE_INDICES:
        path = CBOE_DIR / f"{name}_History.csv"
        if refresh or not path.exists():
            path.write_bytes(_get(CBOE_URL.format(name=name)))


def fetch_cftc(refresh: bool = False, through: int | None = None) -> None:
    """The legacy futures-only archives, each unzipped into its own folder."""
    CFTC_DIR.mkdir(parents=True, exist_ok=True)
    through = through or date.today().year
    archives = {"deacot1986_2016": CFTC_COMBINED}
    archives.update({f"deacot{y}": CFTC_YEAR.format(year=y) for y in range(2017, through + 1)})
    for name, url in archives.items():
        folder = CFTC_DIR / name
        if folder.exists() and any(folder.iterdir()) and not refresh and name != f"deacot{through}":
            continue
        payload = _get(url)
        folder.mkdir(exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            for member in z.namelist():
                if member.endswith("/") or ".." in member or member.startswith("/"):
                    continue  # nothing outside its own folder
                (folder / Path(member).name).write_bytes(z.read(member))


def cboe(name: str) -> pd.Series:
    """One Cboe index's daily close, by date."""
    df = pd.read_csv(CBOE_DIR / f"{name}_History.csv")
    df.columns = [c.strip().upper() for c in df.columns]
    date_col = next(c for c in df.columns if "DATE" in c)
    close_col = "CLOSE" if "CLOSE" in df.columns else next(c for c in df.columns if c != date_col)
    out = pd.Series(pd.to_numeric(df[close_col], errors="coerce").to_numpy(),
                    index=pd.to_datetime(df[date_col], format="mixed"), name=name)  # fmt: skip
    return out[~out.index.duplicated(keep="last")].sort_index().dropna()


def cot() -> pd.DataFrame:
    """Every legacy futures-only row: market, as-of Tuesday, open interest, and the
    commercial and non-commercial long and short positions."""
    frames = []
    for folder in sorted(CFTC_DIR.glob("deacot*")):
        for f in sorted(folder.glob("*.txt")):
            frames.append(pd.read_csv(f, low_memory=False))
    raw = pd.concat(frames, ignore_index=True)
    raw.columns = [c.strip() for c in raw.columns]
    out = pd.DataFrame({
        "market": raw["Market and Exchange Names"].str.strip(),
        "code": raw["CFTC Contract Market Code"].astype(str).str.strip(),
        "as_of": pd.to_datetime(raw["As of Date in Form YYYY-MM-DD"]),
        "open_interest": pd.to_numeric(raw["Open Interest (All)"], errors="coerce"),
        "comm_long": pd.to_numeric(raw["Commercial Positions-Long (All)"], errors="coerce"),
        "comm_short": pd.to_numeric(raw["Commercial Positions-Short (All)"], errors="coerce"),
        "spec_long": pd.to_numeric(raw["Noncommercial Positions-Long (All)"], errors="coerce"),
        "spec_short": pd.to_numeric(raw["Noncommercial Positions-Short (All)"], errors="coerce"),
    })  # fmt: skip
    return out.drop_duplicates(["code", "as_of"], keep="last").sort_values(["code", "as_of"]).reset_index(drop=True)


if __name__ == "__main__":
    fetch_cboe()
    fetch_cftc()
    for name in CBOE_INDICES:
        s = cboe(name)
        print(f"Cboe {name:6} {s.index[0].date()} to {s.index[-1].date()}: {len(s)} days, last {s.iloc[-1]}")
    c = cot()
    print(f"CFTC legacy futures-only: {len(c)} rows, {c['code'].nunique()} markets, {c['as_of'].min().date()} to {c['as_of'].max().date()}")
