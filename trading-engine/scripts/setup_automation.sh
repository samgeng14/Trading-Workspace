#!/usr/bin/env bash
# setup_automation.sh — One-shot bootstrap for hands-off paper trading.
#
# What this does:
#   1. Verifies the venv Python and required packages
#   2. Installs / reloads the LaunchAgent through `bin/tradeit schedule install`
#      (Mon-Fri 15:35 ET, written in this Mac's zone: 14:35 in Central time)
#   3. Schedules a weekday wake five minutes before it via pmset (needs sudo)
#   4. Disables system sleep when on AC power
#   5. Prints checklist for the bits that need manual user action
#      (IB Gateway, Auto-login)
#
# Usage, from the repo root:
#   bash trading-engine/scripts/setup_automation.sh
#
# Re-running is safe — every step is idempotent.

set -euo pipefail

# ── Paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$PROJECT_ROOT/.." && pwd)"
VENV_PYTHON="${VENV_PYTHON:-$REPO_ROOT/.venv/bin/python}"
LAUNCHAGENT_LABEL="com.capitalfund.daily-runner"
LAUNCHAGENT_PLIST="$HOME/Library/LaunchAgents/${LAUNCHAGENT_LABEL}.plist"

# ── Helpers ────────────────────────────────────────────────────────────
green()  { printf '\033[1;32m%s\033[0m\n' "$1"; }
yellow() { printf '\033[1;33m%s\033[0m\n' "$1"; }
red()    { printf '\033[1;31m%s\033[0m\n' "$1"; }
hr()     { printf '\n──────────────────────────────────────────────────────────────\n'; }

# ── 1. Check venv + deps ───────────────────────────────────────────────
hr
green "Step 1/5 · Verify Python environment"

if [[ ! -x "$VENV_PYTHON" ]]; then
  red "✗ Cannot find venv python at $VENV_PYTHON"
  red "  Set VENV_PYTHON=/path/to/python and re-run."
  exit 1
fi
green "  ✓ Found $VENV_PYTHON"

required_pkgs=(pandas pyarrow ib_async yaml)
missing=()
for pkg in "${required_pkgs[@]}"; do
  if ! "$VENV_PYTHON" -c "import $pkg" 2>/dev/null; then
    missing+=("$pkg")
  fi
done
if (( ${#missing[@]} > 0 )); then
  yellow "  ⚠ Missing packages: ${missing[*]}"
  echo "    Installing now…"
  "$VENV_PYTHON" -m pip install --quiet "${missing[@]}"
  green "  ✓ Installed"
else
  green "  ✓ All required packages present"
fi

# ── 2. Install / reload LaunchAgent ────────────────────────────────────
hr
green "Step 2/5 · Install + load LaunchAgent (bin/tradeit schedule install)"

# tradeit writes the plist for this checkout: .venv's Python, the ports and
# optimizer from .env, 15:35 ET in this Mac's zone. It asks before installing.
"$REPO_ROOT/bin/tradeit" schedule install

if [[ ! -f "$LAUNCHAGENT_PLIST" ]]; then
  red "✗ Plist was not created"
  exit 1
fi
green "  ✓ Plist installed at $LAUNCHAGENT_PLIST"

# The run's local time (15:35 ET in this Mac's zone, or DV02_RUNNER_TIME) and a
# wake five minutes before it, read from the same code tradeit uses.
read -r RUN_HOUR RUN_MINUTE WAKE_HOUR WAKE_MINUTE < <(
  cd "$REPO_ROOT/services/api" && "$VENV_PYTHON" -c '
from datetime import datetime, timedelta
from dv02_api import runs
at = runs.runner_time()
wake = (datetime.combine(datetime.today(), at) - timedelta(minutes=5)).time()
print(at.hour, at.minute, wake.hour, wake.minute)
'
)
green "  ✓ Will fire Mon-Fri at ${RUN_HOUR}:$(printf '%02d' "$RUN_MINUTE") local (15:35 ET)"

# ── 3. Schedule a weekday wake ─────────────────────────────────────────
hr
green "Step 3/5 · Schedule weekday wake at ${WAKE_HOUR}:$(printf '%02d' "$WAKE_MINUTE") (needs sudo)"
echo "  This wakes the Mac on Mon-Fri at ${WAKE_HOUR}:$(printf '%02d' "$WAKE_MINUTE") so the"
echo "  ${RUN_HOUR}:$(printf '%02d' "$RUN_MINUTE") LaunchAgent has 5 minutes of warmup."
echo
read -rp "  Run 'sudo pmset repeat wakeorpoweron MTWRF ${WAKE_HOUR}:$(printf '%02d' "$WAKE_MINUTE"):00'? [y/N] " yn
if [[ "$yn" =~ ^[Yy]$ ]]; then
  sudo pmset repeat wakeorpoweron MTWRF "${WAKE_HOUR}:$(printf '%02d' "$WAKE_MINUTE"):00"
  green "  ✓ Wake scheduled"
  pmset -g sched
else
  yellow "  ⚠ Skipped — Mac may sleep through the LaunchAgent fire time"
fi

# ── 4. Disable sleep on AC ─────────────────────────────────────────────
hr
green "Step 4/5 · Configure power management for unattended operation (needs sudo)"
echo "  This prevents sleep when plugged in (good for desktops / always-plugged laptops)."
echo "  On battery your Mac will still sleep normally."
echo
read -rp "  Run 'sudo pmset -c sleep 0 disksleep 0 displaysleep 30'? [y/N] " yn
if [[ "$yn" =~ ^[Yy]$ ]]; then
  sudo pmset -c sleep 0 disksleep 0 displaysleep 30
  green "  ✓ AC sleep disabled (display still sleeps after 30 min)"
else
  yellow "  ⚠ Skipped — Mac may sleep on AC"
fi

# ── 5. Manual checklist ────────────────────────────────────────────────
hr
green "Step 5/5 · Manual checklist (one-time)"
cat <<'EOF'

The remaining pieces require user action — script can't do them safely:

  [ ] IB Gateway running and logged in (paper account): bin/tradeit login once,
        then bin/tradeit gateway start. Or TWS / IB Gateway by hand:
        File → Global Configuration → API → Settings:
          ☑ Enable ActiveX and Socket Clients
          Socket port: 4002 (IB Gateway paper) or 7497 (TWS paper)
          ☐ Read-Only API   (must be UNCHECKED)
        Save with File → Save Settings

  [ ] Auto-login enabled
        System Settings → Users & Groups → 'Automatically log in as'
        (without this, the LaunchAgent won't fire across reboots)

  [ ] Display sleep but not system sleep
        System Settings → Energy / Battery
        (sleep settings just changed by step 4 also help here)

  [ ] IBKR Gateway / TWS auto-restart enabled
        TWS:        File → Global Configuration → Lock and Exit → Auto restart
        IB Gateway: Configure → Settings → Lock and Exit → Auto restart

  [ ] If you want trading to fire even when no user is logged in,
      see scripts/HEADLESS.md for the IBC + LaunchDaemon path.

EOF

hr
green "Bootstrap complete."
echo
echo "Verify by opening the dashboard:"
echo "  cd $PROJECT_ROOT"
echo "  $VENV_PYTHON -m streamlit run src/dashboard/streamlit_entrypoint.py"
echo
echo "Then go to Analytics → System Audit. You want every row green."
echo "Anything yellow tells you exactly which command to run to fix it."
