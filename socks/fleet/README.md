# socks.fleet

Tesla Fleet API (cloud) onboarding. Owner steps: [docs/FLEET_API_SETUP.md](../../docs/FLEET_API_SETUP.md).

| Module | What |
|---|---|
| `oauth.py` | Authorization code + PKCE (S256), loopback redirect `http://localhost:<port>/callback` on the first free of 53682/53683/53684 (all registered), served on both 127.0.0.1 and ::1, constant-time `state` check, code exchange, refresh (single-use refresh tokens are persisted immediately), `access_token()` for `TeslaFleetApi(access_token=...)`, token file `$XDG_CONFIG_HOME/socks/tokens.json` (0600, dir 0700, atomic writes). |
| `partner.py` | Client-credentials partner token, `partner_accounts` register / public_key via `tesla_fleet_api`'s `Partner`, `check_public_key()` for the well-known URL, `pairing_link()`. |
| `cli.py` | `fleet_login`, `fleet_register`, `fleet_status`, `fleet_logout` and `add_fleet_parser()` for `socks-cli fleet ...`. |

Security: token and secret values never reach logs, exceptions or `repr`. The client
secret is read from a `getpass` prompt or `SOCKS_FLEET_CLIENT_SECRET` and never written to
disk. Nothing in this package runs at GUI start-up; import it lazily.
