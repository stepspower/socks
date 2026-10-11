# Roadmap (ideas not scheduled)

Owner decisions 2026-10-11. Nothing here is committed work.

- **Fleet Telemetry receiver** on a dedicated subdomain (VPS, not shared hosting): the car pushes
  position/odometer/SoC continuously (~$0.0001/signal) → fully automatic km register without
  polling. Wanted "in about 5 years", not now.
- **Anonymous app usage statistics** (version/OS/Qt/features, never VIN or location) with an
  opt-out/opt-in choice; would need a collector on a subdomain. Not now; keep the no-telemetry
  promise until then.
- Desktop notifications (charge complete, left unlocked, window open).
- Charging-cost tracking with dynamic tariffs; battery-health report export.
- Home Assistant / MQTT bridge; TeslaMate import; multi-car.
