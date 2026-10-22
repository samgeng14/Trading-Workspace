"""The Postgres store for the trading record: runs, orders, fills, positions and NAV.

``db`` finds and opens it, ``migrate`` creates its tables, ``record`` writes to
it, ``capture`` saves each day's fills, positions and closing NAV from IBKR, and
``backfill`` loads the record the engine kept in files. The files stay the
engine's own record; the store is a copy the dashboard and research can query.
"""
