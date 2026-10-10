"""DV02 Fund API: the /api/v1 contract (schemas/api/v1.yaml) over real data.

Live account state comes from one long-lived IBKR connection; history, targets,
runs and the kill switch come from what trading-engine writes to disk.
"""

__version__ = "1.0.0"
