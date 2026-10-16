# `src/trading_engine/optimizers/` — risk-aware portfolio optimization

Optional layer that runs **after** the aggregator. Takes the desired
portfolio weights from the aggregator and refines them using an
asset-level risk model (covariance) — typically to balance expected
return against portfolio variance.

## Layout

```
optimizers/
├── catalogue/
│   ├── mean_variance.py        ← classic Markowitz mean-variance
│   └── miqp_mean_variance.py   ← MVO with mixed-integer constraints (round lots, cardinality)
└── registry.py                  ← name → callable mapping
```

## The contract

```python
def MeanVariance(
    risk_aversion: float = 1.0,
    risk_model: str = "ledoit_wolf",
    ...
) -> Callable[[DataFrame, DataFrame, Optional[dict]], LazyFrame]:
    def run(prices, desired_weights, config):
        # prices: wide DataFrame (date × tickers)
        # desired_weights: aggregator output (date × tickers)
        # config: optional optimizer-specific overrides
        return optimized_weights_lazy_frame
    return run
```

## When to skip the optimizer

If your aggregator already produces a reasonable portfolio (e.g.
`equal_weight` of well-curated models), an optimizer can over-fit
in-sample and underperform out-of-sample. The pipeline supports
`optimizers=None` for exactly this reason.

## Registry shape

```python
OPTIMIZERS = {
    "mvo_lw_ra1": {
        "function": MeanVariance(risk_aversion=1.0, risk_model="ledoit_wolf"),
        "lookback": 252,
    },
    ...
}
```

The risk model name (`ledoit_wolf`, `naive_dcc`, `sample_with_ridge`)
must reference an entry in [`risk/registry.py`](../risk/README.md).

## Adding an optimizer

1. Drop a file in `catalogue/`. Use `cvxpy` or `scipy.optimize` for
   convex programs. Pass `solver=pick_solver()` from
   [`solvers.py`](../solvers.py) to every cvxpy `solve()`, or
   `pick_solver(mixed_integer=True)` for MIQP (cardinality / round-lot
   constraints), which needs licensed MOSEK or SCIP.
2. Register parameterizations in `registry.py`.
3. Reference by name in YAML configs.

## Reference

The orchestrator post-processes optimizer output: clamp to [-1, 1] then
enforce L1 budget. Don't replicate that logic in your optimizer —
focus on the math.
