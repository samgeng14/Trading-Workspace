# `src/strategies/etf/` — ETF cross-sectional signals

Four canonical factor models implementing the `Signal` protocol from
[`src/strategies/__init__.py`](../__init__.py).

## The signals

| File | Signal | What it sees |
|---|---|---|
| `momentum.py` | 12-1 (skip-5) total-return momentum | "Persistence — long-term winners keep winning." |
| `natr_mean_reversion.py` | NATR-normalized fade-the-extreme | "Stretched away from short MA + high vol → revert." |
| `inverse_momentum_mean_reversion.py` | Long-term winners that just dipped | "6-month winner that's down this week is a buy." |
| `amma.py` | Kaufman's KAMA adaptive trend | "Price above adaptive trend = bullish, scaled by efficiency." |
| `ensemble.py` | Weighted combiner of the above | One composite z-score per symbol. |
| `_panel.py` | Internal helpers | `to_wide`, `slice_history`, `eligible_columns`, `safe_returns`, `safe_log_returns`. |

## Using the registry

```python
from src.strategies.etf import REGISTRY, list_signals

print(list_signals())
# ['amma', 'inverse_momentum_mean_reversion', 'momentum', 'natr_mean_reversion']

momentum = REGISTRY["momentum"]
df = momentum.compute(prices, as_of=pd.Timestamp("2026-04-24"))
print(df.head())
#    symbol       raw     score
# 0     SLV  0.382734  2.617621
# 1     USO  0.354011  2.481053
# ...
```

## Using the ensemble

```python
from src.strategies.etf.ensemble import default_ensemble
ens = default_ensemble()           # equal weight on all 4 signals
df = ens.compute(prices, as_of=pd.Timestamp.today())
# top 5 = best composite z-score across all 4 signals
```

Three pre-built ensembles:
- `default_ensemble()` — equal weight, most diversified
- `momentum_heavy_ensemble()` — 2× momentum + AMMA, leans into trends
- `reversion_heavy_ensemble()` — 2× MR signals, more contrarian

## How a signal score becomes a portfolio weight

Inside `src/production/generate_targets.py`, the optimizer
`ensemble_default` (and friends) takes the composite score and runs a
softmax with temperature 3.0, then floors at 2% per asset. The result:
all 21 model ETFs held, top weight ~10-12%, min ~2.6-2.9%.

To see what each signal favors today:

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
import pandas as pd
prices = pd.read_parquet('data/market/cleaned/prices/etf_prices_master.parquet')
from src.strategies.etf import REGISTRY
for name, sig in REGISTRY.items():
    df = sig.compute(prices, pd.Timestamp.today())
    print(f'{name}: top 3 = {df.sort_values(\"score\", ascending=False).head(3).symbol.tolist()}')
"
```

## Adding your own signal

1. **Pick a name.** Use snake_case, descriptive: `low_volatility`,
   `trend_strength`, `mean_reversion_short`. Don't re-use names of
   existing signals.

2. **Create `my_signal.py`** with a class:

   ```python
   from src.strategies import zscore
   from src.strategies.etf._panel import to_wide, slice_history, eligible_columns

   class MyLowVolSignal:
       name = "low_volatility"
       lookback_days_required = 63

       def compute(self, prices, as_of):
           wide = to_wide(prices)
           hist = slice_history(wide, as_of, lookback=63)
           syms = eligible_columns(hist, min_history=63)
           if not syms:
               return pd.DataFrame(columns=["symbol", "raw", "score"])
           h = hist[syms].ffill()
           daily_vol = h.pct_change().std()
           # We want to BUY low-vol assets, so invert.
           raw = 1.0 / (daily_vol + 1e-9)
           out = pd.DataFrame({"symbol": raw.index, "raw": raw.values})
           out["score"] = zscore(out["raw"])
           return out
   ```

3. **Register it** in `src/strategies/etf/__init__.py`:

   ```python
   from src.strategies.etf.my_signal import MyLowVolSignal
   _register("low_volatility", MyLowVolSignal())
   ```

4. **Optionally add to ensembles** in `src/strategies/etf/ensemble.py`.

5. **Smoke-test it:**

   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '.'); import pandas as pd
   prices = pd.read_parquet('data/market/cleaned/prices/etf_prices_master.parquet')
   from src.strategies.etf import REGISTRY
   df = REGISTRY['low_volatility'].compute(prices, pd.Timestamp.today())
   print(df.sort_values('score', ascending=False).head())
   "
   ```

## Notes on z-scoring

Every signal **must** return a `score` column that's been
cross-sectionally z-scored (mean 0, stdev 1 within each as-of date).
This is what makes the ensemble layer simple: the weighted sum of
z-scores is a meaningful composite.

If your raw value is monotonic-but-skewed (e.g. NATR), z-scoring fixes
it. If your raw value is bounded (e.g. RSI ∈ [0, 100]), z-score it
anyway — the ensemble math assumes comparable scales.
