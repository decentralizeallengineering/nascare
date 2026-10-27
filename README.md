# nascare

Watchdog scripts for a small ZFS NAS (SAS drives, a pool you may want to keep spun down) that lives somewhere hot.

Run from another machine over ssh, they alert on pool and SMART problems, spin the drives down before they overheat,
power the NAS off in an emergency, and export the pool when its error counters start growing. Plain bash and Python,
systemd timers, no daemons, no ML.

| Script | What it does |
| --- | --- |
| `nas-heat.py` | Heat policy every 5 min: at 45 C set `.nas-hot` and spin idle drives down; at 50 C export the pool and power the NAS off; clear below 42 C. |
| `nas-cool.sh` | While `.nas-off` exists, read drive temperatures via `/dev/sg*` and stop any drive that spun up. |
| `nas-health.sh` | Pool state, SMART temperature, rising grown defects, failed self-tests, unexpected spin-ups; above 55 C runs `nas-off`. |
| `naswatch.sh`, `naswatch.py` | Deeper pool, scrub, SMART and (optional) fan check with deduplicated alerts and a CSV history. |
| `nas-err-check.sh` | ZFS error-growth check: if READ/WRITE/CKSUM totals grow, export the pool to protect the data. |
| `nas-off`, `nas-on` | Export and spin down / spin up and import. Used by the others; refuse to run during a scrub or resilver. |

## Install

On the machine that watches (not the NAS): `install -m 755 bin/* ~/.local/bin/`, copy `systemd/*` to `~/.config/systemd/user/`
and `etc/nascare.conf.example` to `~/.config/nascare/nascare.conf`. The NAS needs smartmontools, sg3-utils and ZFS, ssh key login
for the configured user, and passwordless sudo for `smartctl`, `zpool`, `sg_*`, `dmesg`, `tee` and `systemctl poweroff`.

## Configure

Set `NAS_HOST` (required), then optionally `NAS_POOL`, `NAS_DRIVES` and the temperature thresholds in `nascare.conf`; every key
is listed in `etc/nascare.conf.example`. Environment variables override the file. State and alerts default to `~/.local/state/nascare/`.

## Example

```
cat > ~/.config/nascare/nascare.conf <<'CONF'
NAS_HOST=ann@nas.example.invalid
NAS_POOL=tank
NAS_DRIVES=sda sdb sdc
CONF
systemctl --user enable --now nas-heat.timer nas-health.timer naswatch.timer nas-err-check.timer
tail -f ~/.local/state/nascare/alerts.log
```

Bring the NAS back after `nas-off`: `nas-on`.

## Tests

`python3 -m pytest tests -q` (ssh, sudo, smartctl and zpool are replaced by local stubs; no NAS needed).

## License

Apache-2.0, see `LICENSE` and `NOTICE`.
