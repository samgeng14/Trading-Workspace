# IB Gateway with automatic login

`docker-compose.yml` runs IB Gateway in Docker with
[IBC](https://github.com/IbcAlpha/IBC), via the `ghcr.io/gnzsnz/ib-gateway`
image. IBC types the login for you and restarts the gateway every night, so
you don't log in to the IBKR app by hand.

## Set up once

1. Install and open Docker Desktop (or OrbStack).
2. Save the login. Run `bin/tradeit login` and enter the **paper trading**
   username and password at the prompts; the password is hidden as you type.
   Find the paper username in Client Portal → Settings → Paper Trading Account.
   - It is written only to `ibgw/.env` on this machine.
   - That file is gitignored and readable only by your user (mode 600).
   - Never commit it or paste it anywhere.

## Daily use

- `bin/tradeit up` starts the gateway if it isn't running, then the API and
  the dashboard.
- `bin/tradeit gateway start | stop | status | logs` manages the gateway on
  its own.
- The scheduled daily run (`bin/tradeit schedule install`; see
  `services/api/README.md`) trades through this gateway at 16:32 but doesn't
  start it, so leave the gateway (and Docker) running.

The API port is published on 127.0.0.1:4002 only.

## Notes

- **Use the paper login.** A gateway logged in with the paper user can't
  touch real money, and paper logins don't normally ask for IBKR Mobile
  approval.
- **Live logins** always need the phone approval (two-factor
  authentication). That tap can't be automated, by design.
- **Sessions:** IBKR allows one session per username. Keep the IBKR app or
  TWS on your *live* user (or logged out) while the gateway uses the paper
  user. `EXISTING_SESSION_DETECTED_ACTION=primary` makes the gateway take the
  session back if they collide.
- **Settings:** `ibgw/.env` holds IBC's settings, written by `tradeit login`.
  `READ_ONLY_API=no` lets the engine send paper orders.
