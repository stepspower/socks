# SOCKS — CLAUDE.md

SOCKS (State Of Charge, Keys & Stats) is Step's open-source Linux Tesla
companion app (GPL-3, GitHub stepspower/socks, Flathub target). This folder is
the **socks** workspace; it used to be driven from `~/claude`, which is why older
notes live there. Plan in `PLAN.md`, decisions in `docs/DECISIONS.md`,
maintenance cadence in `docs/MAINTENANCE.md`, release steps in `docs/RELEASING.md`.

Session start: `periodic status --here --due` (socks-monthly, quarterly, yearly
are registered in `~/claude/PERIODIC.md`); `gh issue list -l maintenance` for the
monthly issue. Stamp with `periodic done <id>` when finished.

Rules: branch before committing, never push `main`, releases only on Step's
explicit go. The Tesla driver key (`~/.config/tesla/private_key.pem`) controls
the car: never read it into a message, never run a car command without an OK
for that command. Live reads near the car are fine when asked.

Skills: `/github-stepspower`, `/greenhost-ssh` (auth.stepspower.net), `/mail-step`.
