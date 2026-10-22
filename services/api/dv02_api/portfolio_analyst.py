"""Import daily NAV from an IBKR PortfolioAnalyst CSV export.

IBKR's Flex statements can start later than the account (DUP317554's start on
2026-04-15, while the account opened on 2026-03-06). PortfolioAnalyst keeps the
whole record. Its CSV export, run with Daily frequency, has a section

    Allocation by Asset Class,Header,Date,Commodities,Equities,Fixed Income,Cash,NAV
    Allocation by Asset Class,Data,20260306,0,0,0,1000086.35,1000086.35

with the account's NAV for every day since inception. This reads those rows and
merges them into the Flex NAV cache (artifacts/api/flex_nav_<profile>.csv), so
the equity curve starts on the account's first day. Where Flex already has a
day, Flex's value is kept (the two agree to the cent).

    python -m dv02_api.portfolio_analyst <export.csv> [profile]

Only dates and NAVs are kept; the export's name and account lines are read to
check the file, never stored.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import flex

SECTION = "Allocation by Asset Class"


class ExportError(ValueError):
    pass


@dataclass(frozen=True)
class Export:
    account: str
    period: str
    navs: list[tuple[date, float]]


def parse(path: Path) -> Export:
    account = period = ""
    headers: dict[str, list[str]] = {}
    navs: dict[date, float] = {}
    monthly = False
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 3:
                continue
            section, kind, fields = row[0], row[1], row[2:]
            if kind == "Header":
                headers[section] = fields
                continue
            if kind != "Data" or section not in headers:
                continue
            named = dict(zip(headers[section], fields, strict=False))
            if section == "Introduction":
                account = named.get("Account", "").strip()
                period = named.get("AnalysisPeriod", "").strip()
            elif section == SECTION:
                raw, nav = named.get("Date", "").strip(), named.get("NAV", "").strip()
                if len(raw) == 6 and raw.isdigit():
                    monthly = True
                    continue
                try:
                    navs[datetime.strptime(raw, "%Y%m%d").date()] = float(nav)
                except ValueError:
                    continue
    if not account:
        raise ExportError("this doesn't look like a PortfolioAnalyst CSV export (no Introduction section)")
    if not navs:
        if monthly:
            raise ExportError(
                "this export is monthly. In PortfolioAnalyst, run the report again with Daily frequency"
                " (and the Allocation by Asset Class section), then export it as CSV"
            )
        raise ExportError(f"this export has no daily '{SECTION}' rows; include that section and choose Daily")
    return Export(account, period, sorted(navs.items()))


def merge(profile: str, export: Export) -> tuple[int, int, date]:
    """Adds the export's days to the Flex cache, keeping Flex's own value where both have a day.
    Returns (days in the export, days added, the cache's first date)."""
    cached = dict(flex.read_cache(profile))
    merged = {**dict(export.navs), **cached}
    flex._write_cache(profile, merged)
    return len(export.navs), len(merged) - len(cached), min(merged)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or len(args) > 2:
        print("usage: python -m dv02_api.portfolio_analyst <export.csv> [profile]", file=sys.stderr)
        return 2
    path, profile = Path(args[0]).expanduser(), (args[1] if len(args) > 1 else "paper")
    try:
        export = parse(path)
    except OSError as exc:
        print(f"Couldn't read {path}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    except ExportError as exc:
        print(f"Not imported: {exc}", file=sys.stderr)
        return 1
    if profile == "paper" and not export.account.startswith("D"):
        print(f"Not imported: {export.account} is a live account, and this is the paper profile", file=sys.stderr)
        return 1
    days, added, first = merge(profile, export)
    (start, _), (end, _) = export.navs[0], export.navs[-1]
    print(
        f"PortfolioAnalyst: {days} days of NAV for {export.account}, {start} to {end};"
        f" {added} new. The NAV history now starts {first}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
