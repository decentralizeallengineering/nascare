#!/usr/bin/env python3
"""nas-heat: NAS heat policy for a NAS in a hot room or garage. Token-free, run by nas-heat.timer every 5 min.
>= NAS_SPINDOWN_C (45): set the .nas-hot flag (your backup job should skip while it exists) and, when the drives are idle,
                        run NAS_OFF_CMD (export the pool, spin the drives down); busy -> retry next run (logged, no alert).
>= NAS_SHUTDOWN_C (50): export the pool, power the NAS off, ONE alert.
<  NAS_CLEAR_C (42):    clear .nas-hot (hysteresis). Standby drives count as cool.
While .nas-off exists the drives are never probed: temperatures come from the last nas-cool line in nas-temps.csv.
Configuration: nascare.conf, see README. Test hooks: NASHEAT_DIR (state dir), NASHEAT_SSH (ssh command), NASHEAT_NASOFF."""
import os, re, subprocess, sys, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nascare_conf as C

W = os.environ.get("NASHEAT_DIR", C.STATE_DIR)
HOST = C.NAS_HOST
SSH = os.environ.get("NASHEAT_SSH", "ssh").split()
NASOFF = os.environ.get("NASHEAT_NASOFF", C.cfg("NAS_OFF_CMD", os.path.join(os.path.dirname(os.path.abspath(__file__)), "nas-off")))
ALERTS, LOGF = C.ALERT_FILE, f"{W}/nas-heat.log"
HOT, SHUT, OFF = f"{W}/.nas-hot", f"{W}/.nas-shutdown", f"{W}/.nas-off"
TEMPS = f"{W}/nas-temps.csv"
SPIN_DOWN, SHUTDOWN, CLEAR = C.cfg_int("NAS_SPINDOWN_C", 45), C.cfg_int("NAS_SHUTDOWN_C", 50), C.cfg_int("NAS_CLEAR_C", 42)
POOL, DRIVES = C.POOL, C.DRIVES
_dre = "|".join(DRIVES)
REMOTE = (f'''for d in {" ".join(DRIVES)}; do o=$(sudo -n smartctl -n standby -A /dev/$d 2>&1); if echo "$o" | grep -qi standby; then echo "T $d standby"; else echo "T $d $(echo "$o" | awk -F: '/Current Drive Temperature/{{print $2+0}}')"; fi; done
sudo -n zpool list -H -o name {POOL} >/dev/null 2>&1 && echo POOL up || echo POOL none
f() {{ awk '$3~/^({_dre})$/{{s+=$6+$10;i+=$12}}END{{print s+0, i+0}}' /proc/diskstats; }}
a=$(f); sleep 3; b=$(f); set -- $a $b
if [ "$1" = "$3" ] && [ "$4" = 0 ] && [ "$2" = 0 ]; then echo IO idle; else echo IO busy; fi
echo UP $(cut -d. -f1 /proc/uptime)''')

def now(): return datetime.now().strftime("%F %T")
def log(m):
    os.makedirs(W, exist_ok=True); open(LOGF, "a").write(f"{now()} {m}\n")
def alert(m):
    os.makedirs(os.path.dirname(ALERTS), exist_ok=True); open(ALERTS, "a").write(f"{now()} ALERT {m}\n")
def ssh(cmd, timeout=90):
    return subprocess.run(SSH + C.SSH_OPTS + [HOST, cmd], capture_output=True, text=True, timeout=timeout)

def parse(txt):
    """-> dict(temps={dev: int|None}, pool_up, idle, up)."""
    r = {"temps": {}, "pool_up": False, "idle": False, "up": None}
    for ln in txt.splitlines():
        f = ln.split()
        if len(f) == 3 and f[0] == "T": r["temps"][f[1]] = int(f[2]) if f[2].isdigit() else None   # standby/unreadable -> None = cool
        elif f[:2] == ["POOL", "up"]: r["pool_up"] = True
        elif f[:2] == ["IO", "idle"]: r["idle"] = True
        elif len(f) == 2 and f[0] == "UP" and f[1].isdigit(): r["up"] = int(f[1])
    return r

def csv_temps():
    """Last nas-cool line (drives are off): 'ts,sda=33 sdb=36 ' -> {dev: temp}."""
    try: last = open(TEMPS).read().strip().splitlines()
    except Exception: return {}
    for ln in reversed(last):
        t = dict((k, int(v)) for k, v in re.findall(r"(sd[a-z]+)=(\d+)", ln))
        if t: return t
    return {}

def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__); return 0
    if not HOST:
        print("nas-heat: NAS_HOST is not set (see nascare.conf.example)", file=sys.stderr); return 2
    off = os.path.exists(OFF)
    info = None
    if off:
        temps = csv_temps()
    else:
        try: r = ssh(REMOTE)
        except Exception as e: log(f"ssh failed: {e}"); return 0
        info = parse(r.stdout)
        if not info["temps"]: log("no temps (NAS unreachable?)"); return 0
        temps = info["temps"]
        if os.path.exists(SHUT) and info["up"] is not None and os.path.getmtime(SHUT) < time.time() - info["up"]:
            os.remove(SHUT); log("NAS rebooted since shutdown, shutdown flag cleared")
    vals = [v for v in temps.values() if v is not None]
    hi = max(vals) if vals else 0
    dev = max((d for d, v in temps.items() if v == hi), default="?") if vals else "?"
    log(f"max {hi} C ({dev}) off={int(off)}")
    if hi < CLEAR:
        if os.path.exists(HOT): os.remove(HOT); log(f"below {CLEAR} C, .nas-hot cleared")
        if os.path.exists(SHUT) and not off: os.remove(SHUT)
        return 0
    if hi < SPIN_DOWN: return 0
    open(HOT, "a").close()
    if hi >= SHUTDOWN:
        if os.path.exists(SHUT): return 0
        open(SHUT, "w").write(f"{now()} {dev} {hi}\n")
        if not off:
            ok = False
            for _ in range(2):
                try: ok = ssh(f"sudo -n zpool export {POOL} || ! sudo -n zpool list {POOL} >/dev/null 2>&1").returncode == 0
                except Exception: ok = False
                if ok: break
            if not ok:
                log("plain export failed twice, forcing")
                try: ssh(f"sudo -n zpool export -f {POOL}")
                except Exception: pass
        try: ssh("sudo -n systemctl poweroff", 30)
        except Exception: pass
        alert(f"NAS SHUTDOWN drive {dev} {hi}C, power it on when it has cooled")
        log(f"shutdown at {hi} C")
        return 0
    if off: return 0   # already spun down
    if not info["idle"]: log(f"{hi} C but NAS busy, retry next run"); return 0
    r = subprocess.run(NASOFF.split(), capture_output=True, text=True)
    log(f"{hi} C idle, nas-off rc={r.returncode}")
    return 0

if __name__ == "__main__": sys.exit(main())
