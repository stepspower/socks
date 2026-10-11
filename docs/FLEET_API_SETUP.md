# Tesla Fleet API setup (owner, one time)

Optional: SOCKS works over Bluetooth without any of this. Facts checked against
developer.tesla.com on 2026-10-11; portal labels change, so follow the meaning if a label differs.

1. **Verify the hosted key.** It is served from the dedicated subdomain `auth.stepspower.net`
   (Let's Encrypt). `curl -sS https://auth.stepspower.net/.well-known/appspecific/com.tesla.3p.public-key.pem`
   must return 200 (no `-k`), and `socks-cli fleet status --check-key` must report `ok`
   (same key as your local public key). Note: a copy also exists on the apex `stepspower.net`;
   it is not used.
2. **Tesla account**: verified e-mail and multi-factor authentication switched on.
3. **developer.tesla.com → Dashboard → Create New Application.** Account type: *Individual*
   if offered, otherwise *Business* with your own legal name. Name `SOCKS`; description
   "Personal Linux desktop companion for my own car"; purpose: personal use, own vehicle.
4. **Client details** - OAuth grant type *Authorization Code and Machine-to-Machine*:
   - Allowed Origin URL: `https://auth.stepspower.net`
   - Allowed Redirect URIs, exactly these three: `http://localhost:53682/callback`,
     `http://localhost:53683/callback`, `http://localhost:53684/callback`
     (the portal accepts plain `http://` only for `localhost` with an explicit port; SOCKS
     uses the first free one and listens on both 127.0.0.1 and ::1 so no other program can
     catch the redirect)
   - Allowed Returned URL: `https://auth.stepspower.net`
5. **Scopes**: openid, offline_access, Vehicle Information (`vehicle_device_data`), Vehicle
   Commands (`vehicle_cmds`), Vehicle Charging Management (`vehicle_charging_cmds`). Vehicle
   Location only if you will log in with `--with-location` (SOCKS does not need it).
6. **Billing**: add a payment method. Every account starts with a billing limit of 0 and an app
   with no payment method is disabled. Tesla gives a **$10 monthly discount** for individual
   developers. Prices: vehicle data $0.002/request, commands $0.001, wake $0.02, streaming
   signals $0.0001 (rounded to $0.01). Rate limits per car: 60 data, 30 commands, 3 wakes /min.
   Set a low billing limit (e.g. $10) so overruns are impossible.
7. Copy the **Client ID** and **Client Secret**. The ID goes into SOCKS settings; the secret is
   only typed when asked and SOCKS never stores it.
8. Register the domain (once per region):
   `socks-cli fleet register --client-id <CLIENT_ID> --region eu` - checks the hosted key,
   asks for the secret, calls `POST /api/1/partner_accounts` with `auth.stepspower.net`.
9. **Pair the virtual key**: on the phone with the Tesla app, open
   `https://tesla.com/_ak/auth.stepspower.net` and approve "auth.stepspower.net" in the car's key list.
   (SOCKS uses the same key that is already paired over BLE, so this may show it as present.)
10. **Sign in**: `socks-cli fleet login` - the browser opens Tesla's login; tokens land in
    `~/.config/socks/tokens.json` (0600). Press Enter at the secret prompt to try pure PKCE;
    if Tesla rejects that, rerun and paste the secret. If all three ports are busy, close
    the program holding them (or register another port and pass `--port`).
11. Check: `socks-cli fleet status`. Then enable *Cloud* in Settings. Keep polling off.
