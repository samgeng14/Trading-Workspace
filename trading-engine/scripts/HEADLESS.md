# Truly Headless Trading on macOS

`setup_automation.sh` gets you 95% of the way to hands-off operation. The
remaining 5% — running while **no user is logged in at all** (e.g. after a
power cut and reboot, with no auto-login configured) — needs a different
architecture, because:

1. **macOS LaunchAgents need a user GUI session.** Even with auto-login
   disabled, `~/Library/LaunchAgents` only fire while a user is logged in.
2. **TWS and IB Gateway are GUI apps.** They can't run without a window
   server, which means they can't run on the login screen.

There are two viable paths to true headlessness. Pick whichever matches your
risk tolerance.

## Path A — Auto-login + LaunchAgent (recommended)

This is what `setup_automation.sh` configures. Your Mac auto-logs you in on
boot, your TWS/IB Gateway auto-launches on login, the LaunchAgent fires daily
at 15:35 ET (in the Mac's zone). The user session persists even when the screen is locked.

**Pros:** Simple. No third-party software. Survives reboots if auto-login is
enabled.

**Cons:** Anyone with physical access to the Mac is logged in as you. Not
suitable if the machine is in a shared space.

**Setup:**

```bash
cd /Users/tradingworkspace/TradingWorkspace/trading-engine
bash scripts/setup_automation.sh
```

Then complete the manual checklist printed at the end (auto-login, TWS API
settings, IBKR auto-restart).

## Path B — IBC + LaunchDaemon (true headless)

[IBC (Interactive Brokers Controller)](https://github.com/IbcAlpha/IBC) is an
OSS wrapper that runs IB Gateway in a headless, automated mode. You point a
LaunchDaemon at IBC, and the Gateway runs without ever needing a GUI session.

**Pros:** True headlessness. No auto-login needed. Survives reboots cleanly.
LaunchDaemon runs as `root` so it fires even on the login screen.

**Cons:** Considerably more setup. IBC stores your IBKR password in a config
file (encrypt it). LaunchDaemons are harder to debug than LaunchAgents.

**High-level setup:**

1. **Install IBC** (don't use brew — pull a pinned release from GitHub).

   ```bash
   curl -L -o /tmp/ibc.zip \
     https://github.com/IbcAlpha/IBC/releases/download/3.20.0/IBCMacos-3.20.0.zip
   sudo mkdir -p /opt/ibc
   sudo unzip /tmp/ibc.zip -d /opt/ibc
   sudo chmod 755 /opt/ibc/*.sh
   ```

2. **Configure** `/opt/ibc/config.ini` with your IBKR paper account
   credentials, the IB Gateway path, and the API port:

   ```ini
   IbLoginId=your_paper_username
   IbPassword=your_paper_password
   TradingMode=paper
   FIX=no
   IbDir=/Applications/IB\ Gateway\ Latest
   OverrideTwsApiPort=4002
   ReadOnlyApi=no
   ```

   Then lock it down:

   ```bash
   sudo chmod 600 /opt/ibc/config.ini
   sudo chown root:wheel /opt/ibc/config.ini
   ```

3. **Write a LaunchDaemon plist** at
   `/Library/LaunchDaemons/com.capitalfund.ibc.plist`:

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
       "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
   <plist version="1.0">
   <dict>
       <key>Label</key>
       <string>com.capitalfund.ibc</string>
       <key>ProgramArguments</key>
       <array>
           <string>/opt/ibc/scripts/ibcstart.sh</string>
           <string>1019</string>           <!-- Gateway version -->
           <string>--gateway</string>
           <string>--mode=paper</string>
       </array>
       <key>EnvironmentVariables</key>
       <dict>
           <key>IBC_PATH</key><string>/opt/ibc</string>
           <key>TWS_PATH</key><string>/Applications/IB Gateway Latest</string>
           <key>LOG_PATH</key><string>/var/log/ibc</string>
       </dict>
       <key>RunAtLoad</key><true/>
       <key>KeepAlive</key><true/>
       <key>StandardOutPath</key>
       <string>/var/log/ibc/stdout.log</string>
       <key>StandardErrorPath</key>
       <string>/var/log/ibc/stderr.log</string>
   </dict>
   </plist>
   ```

   ```bash
   sudo mkdir -p /var/log/ibc
   sudo chmod 644 /Library/LaunchDaemons/com.capitalfund.ibc.plist
   sudo chown root:wheel /Library/LaunchDaemons/com.capitalfund.ibc.plist
   sudo launchctl bootstrap system /Library/LaunchDaemons/com.capitalfund.ibc.plist
   ```

4. **Convert the daily runner LaunchAgent to a LaunchDaemon** so it also
   fires without a user session. Move
   `~/Library/LaunchAgents/com.capitalfund.daily-runner.plist` to
   `/Library/LaunchDaemons/com.capitalfund.daily-runner.plist`, change the
   `UserName` key to your username (so it has access to your venv), and
   bootstrap into the `system` domain:

   ```bash
   sudo cp ~/Library/LaunchAgents/com.capitalfund.daily-runner.plist \
           /Library/LaunchDaemons/
   sudo chown root:wheel /Library/LaunchDaemons/com.capitalfund.daily-runner.plist
   ```

   Edit the plist to add a `<key>UserName</key><string>tradingworkspace</string>`
   entry inside the top-level `<dict>`. Then:

   ```bash
   launchctl bootout gui/$(id -u)/com.capitalfund.daily-runner 2>/dev/null
   sudo launchctl bootstrap system \
     /Library/LaunchDaemons/com.capitalfund.daily-runner.plist
   ```

5. **Reboot to test.** Don't log in. SSH into the Mac (or wait until
   15:35 ET). Verify:

   ```bash
   ssh you@mac-mini
   sudo launchctl print system/com.capitalfund.ibc
   sudo launchctl print system/com.capitalfund.daily-runner
   ls -lt /var/log/ibc/
   ls -lt /Users/tradingworkspace/TradingWorkspace/trading-engine/data/logs/runtime/
   ```

   You should see fresh IBC logs and (eventually) fresh daily_runner logs.

## Verifying either path works

After setup, the **System Audit** screen in the dashboard runs the same
checks both paths require:

- LaunchAgent loaded (Path A) or LaunchDaemon loaded (Path B)
- Wake schedule (only Path A — LaunchDaemons don't need this)
- Auto-login (only Path A)
- Runner Python deps (both paths)
- Pyarrow / ib_async present (both)
- IBKR API listener on the right port (both)
- Daily runner log shows recent successful run (both)

If every row is green, the next trading day's 15:35 ET run goes unattended.

## Common pitfalls

- **Lid closed on a laptop on battery → sleep.** Period. Apple's clamshell
  mode only stays awake on AC with an external display *attached*. If you
  want true unattended operation on a laptop, plug it into AC and an external
  display, OR keep the lid open.
- **IBKR's daily 23:45 ET disconnect.** Live accounts force a daily session
  reset. Paper accounts can disable it under Lock and Exit → Auto restart.
- **macOS updates auto-restart.** Disable auto-update of macOS itself; let
  application updates happen but defer OS updates manually.
- **Time-of-day drift.** macOS NTP usually keeps clock within seconds, but
  if you've ever manually set the clock, run
  `sudo systemsetup -setusingnetworktime on`.
