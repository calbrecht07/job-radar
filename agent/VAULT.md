# Vault sync (optional): keep the person's notes in step

Only when `agent/config.yaml` in the data repo has a `vault` section, and the run has access to the person's computer. All paths are relative to `vault.root`.

**Vault → repo** (first, so this run uses the latest settings)
1. `vault.settings_note`: take the ```yaml block, validate it (`PYTHONPATH=<framework> python -c "import yaml; from radar.filters import Filters; Filters(yaml.safe_load(open('c.yaml')))"`), and if valid and changed, replace `settings.yaml` in the data repo. If invalid: keep the old file and log "Settings note has an error: <message>".
2. `vault.profile_files`: copy each listed note to its `profile/` target when changed.
3. `vault.wishlist_note`: companies struck through or marked removed → prefix their `wishlist.csv` row name with `#`. Companies in the note but not in `wishlist.csv` → add a row.

**Repo → vault**
4. `report/report.md` → `vault.report_note` (overwrite).
5. New Keep/Stretch verdicts since the last sync (track the timestamp in `scan/vault_sync.json`) → rows in `vault.alerts_note` (local roles) and `vault.remote_alerts_note` (remote roles), under a dated heading, in the table format those notes already use.
6. New `scan/snoopy_log.md` entries → top of `vault.log_note`.
7. Any extra per-person steps listed under `vault.extra_steps` (e.g. opportunity briefs).

Edit notes in place (read-modify-write with python); never rebuild a note from truncated output.
