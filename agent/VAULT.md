# Vault mirror (optional): a read-only copy of the radar in the person's notes

Only when `agent/config.yaml` in the data repo has a `vault` section. GitHub is the single source of truth:
settings, wishlist and profile are changed in the data repo (ask Claude), never in the vault. The vault is a
copy that catches up whenever a run can reach it: a scheduled run bound to the person's computer with the vault
folder attached, or any session on that computer (Claude Code, the desktop app).

1. Run `PYTHONPATH=<framework> python -m radar.vault --data <data repo>`. It finds the vault from
   `vault.roots` (first that exists), then writes, in one direction only:
   - the settings note's yaml block = `settings.yaml`, under a read-only notice
   - the report note = `report/report.md`, with a link to the live report page
   - new Keep/Stretch roles into the alerts notes (local and remote), skipping roles already there
   - new `scan/snoopy_log.md` entries into the log note
   It prints "not reachable, skipped" and changes nothing when no root exists: that's normal, not an error.
2. Then any per-person steps in `vault.extra_steps` (e.g. opportunity briefs), only if step 1 found the vault.
3. Commit `scan/vault_sync.json` with the rest of the run.

Never edit vault notes from truncated output, and never copy vault notes into the repo.
