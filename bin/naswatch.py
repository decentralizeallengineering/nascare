#!/usr/bin/env python3
"""naswatch: token-free NAS health watcher, run by naswatch.timer every 30 min (through naswatch.sh).
Reads `zpool status` and `smartctl -a` of every drive over ssh and appends ALERT lines (deduplicated) to ALERT_FILE for:
pool not ONLINE or with R/W/CKSUM errors or data errors, scrub that repaired something, SMART health not OK, uncorrected
errors, a rising grown-defect count (each rise alerts once), NAS unreachable 3 runs in a row, and (optional) a stopped fan.
History goes to nas-history.csv. While the .nas-off flag exists the drives are never probed.
Configuration: nascare.conf (NAS_HOST, NAS_POOL, NAS_DRIVES, NAS_BASE_DEFECTS="sdc=16" known-old defect counts,
NAS_FAN_INPUT="fan5" hwmon fan input of the drive-bay fan, empty disables the fan check). NASWATCH_NOALERT=1 prints instead."""
import json, os, re, subprocess, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nascare_conf as C

W = C.STATE_DIR
ALERTS, RECENT = C.ALERT_FILE, f"{W}/.recent"
STATE = os.environ.get("NASWATCH_STATE", f"{W}/naswatch-state.json")
HIST = os.environ.get("NASWATCH_HIST", f"{W}/nas-history.csv")
HOST, DRIVES, POOL = C.NAS_HOST, C.DRIVES, C.POOL
BASE_DEFECTS = {k: int(v) for k, v in (x.split("=") for x in C.cfg("NAS_BASE_DEFECTS").split() if "=" in x)}
SSHO = C.SSH_OPTS
REMOTE = (f"sudo -n zpool status -x {POOL}; echo ==POOL==; sudo -n zpool status {POOL}; " +
          "".join(f"echo ==SMART {d}==; sudo -n smartctl -a /dev/{d}; " for d in DRIVES))

def parse_pool(txt):
    r = {"state": None, "rwc": 0, "scan": "", "healthy": "healthy" in txt.split("==POOL==")[0], "dataerr": None}
    pool = txt.split("==POOL==")[-1]
    m = re.search(r"state:\s*(\S+)", pool); r["state"] = m.group(1) if m else None
    m = re.search(r"scan:(.*?)(?=\nconfig:)", pool, re.S); r["scan"] = " ".join(m.group(1).split()) if m else ""
    for ln in pool.splitlines():   # name state R W C rows
        f = ln.split()
        if len(f) == 5 and f[1] in ("ONLINE", "DEGRADED", "FAULTED", "OFFLINE", "UNAVAIL", "REMOVED") and all(x.isdigit() for x in f[2:]):
            r["rwc"] += sum(int(x) for x in f[2:])
    m = re.search(r"errors:\s*(.*)", pool); r["dataerr"] = m.group(1).strip() if m else None
    return r

def parse_smart(txt):
    d = {"health": None, "defects": None, "unc": 0}
    m = re.search(r"SMART Health Status:\s*(.*)", txt) or re.search(r"overall-health.*?:\s*(\S+)", txt)
    d["health"] = m.group(1).strip() if m else None
    m = re.search(r"grown defect list:\s*(\d+)", txt); d["defects"] = int(m.group(1)) if m else None
    for ln in txt.splitlines():
        f = ln.split()
        if f and f[0] in ("read:", "write:", "verify:") and len(f) >= 8 and f[-1].isdigit():
            d["unc"] += int(f[-1])
    return d

def parse_all(out):
    parts = re.split(r"==SMART (\w+)==", out)
    res = {"pool": parse_pool(parts[0]), "drives": {}}
    for i in range(1, len(parts), 2): res["drives"][parts[i]] = parse_smart(parts[i + 1])
    return res

def decide(cur, state):
    """cur: parsed dict or None (unreachable). Returns (alerts, new_state)."""
    st = dict(state); st.setdefault("defects", dict(BASE_DEFECTS)); st.setdefault("fails", 0)
    al = []
    if cur is None:
        st["fails"] += 1
        if st["fails"] >= 3: al.append(f"NAS unreachable {st['fails']} runs in a row")
        return al, st
    st["fails"] = 0
    p = cur["pool"]
    if p["state"] != "ONLINE" or not p["healthy"]: al.append(f"NAS pool state {p['state']} (zpool -x not healthy)")
    if p["rwc"] > 0: al.append(f"NAS pool READ/WRITE/CKSUM errors total {p['rwc']}")
    if p["dataerr"] and not p["dataerr"].startswith("No known data errors"): al.append(f"NAS pool data errors: {p['dataerr']}")
    sc = p["scan"]
    if "scrub repaired" in sc:
        m = re.search(r"repaired (\S+) in .*? with (\d+) errors", sc)
        if m and (m.group(1) not in ("0B", "0") or m.group(2) != "0"):
            al.append(f"NAS scrub finished with repairs/errors: {sc[:120]}")
    for d in DRIVES:
        s = cur["drives"].get(d)
        if not s or s["health"] is None: al.append(f"NAS {d} SMART unreadable"); continue
        if s["health"] != "OK": al.append(f"NAS {d} SMART health {s['health']}")
        if s["unc"] > 0: al.append(f"NAS {d} uncorrected errors {s['unc']}")
        if s["defects"] is not None:
            base = st["defects"].get(d)
            if base is None: st["defects"][d] = s["defects"]
            elif s["defects"] > base:
                al.append(f"NAS {d} grown defects {base} -> {s['defects']}")
                st["defects"][d] = s["defects"]   # new baseline so each rise alerts once
    return al, st

SAS_FAN = os.environ.get("NASWATCH_SASFAN", C.cfg("NAS_FAN_INPUT", ""))   # hwmon fan input of the drive-bay fan, e.g. "fan5"; empty disables
TEMPS = os.environ.get("NASWATCH_TEMPS", f"{W}/nas-temps.csv")
FAN_REMOTE = ("d=; for h in /sys/class/hwmon/hwmon*; do case $(cat $h/name 2>/dev/null) in it87*|it86*|it89*) d=$h;; esac; done; "
              f"if [ -z \"$d\" ]; then echo FAN nodrv; else echo FAN ok $(cat $d/{SAS_FAN}_input 2>/dev/null); fi")

def parse_fan(txt):
    """-> ('ok', rpm|None) when the it87 hwmon exists, ('nodrv', None) when no fan sensor driver, None when unreadable."""
    for ln in txt.splitlines():
        f = ln.split()
        if f[:2] == ["FAN", "nodrv"]: return ("nodrv", None)
        if f[:2] == ["FAN", "ok"]: return ("ok", int(f[2]) if len(f) > 2 and f[2].isdigit() else None)
    return None

def fan_alerts(fan, pool_imported):
    """Alert when the SAS fan reads 0/unreadable while the pool is imported. No sensor driver (for example blocked by Secure Boot) is reported, not alerted."""
    if not SAS_FAN or not pool_imported or fan is None or fan[0] == "nodrv": return []
    if not fan[1]: return [f"NAS SAS fan ({SAS_FAN}) RPM {'0' if fan[1] == 0 else 'unreadable'} while pool imported"]
    return []

def fetch_fan():
    if not SAS_FAN: return ("nodrv", None)
    try:
        r = subprocess.run(["ssh", *SSHO, HOST, FAN_REMOTE],
                           capture_output=True, text=True, timeout=30)
        return parse_fan(r.stdout)
    except Exception:
        return None

def fan_label(fan):
    return "SAS fan=" + ("unreadable" if fan is None else "n/a(no sensor driver)" if fan[0] == "nodrv" else "unreadable" if fan[1] is None else f"{fan[1]}rpm")

def log_fan(fan):
    if not SAS_FAN: return
    with open(TEMPS, "a") as f: f.write(f"{now()},{fan_label(fan)}\n")

def fetch():
    try:
        r = subprocess.run(["ssh", *SSHO, HOST, REMOTE], capture_output=True, text=True, timeout=30)
        if r.returncode != 0 and "==POOL==" not in r.stdout: return None
        return parse_all(r.stdout)
    except Exception:
        return None

def now(): return datetime.now().strftime("%F %T")

def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__); return 0
    if not HOST:
        print("naswatch: NAS_HOST is not set (see nascare.conf.example)", file=sys.stderr); return 2
    os.makedirs(W, exist_ok=True)
    # Drives spun down: any smartctl/zpool probe can wake them. nas-health.sh watches spin-ups.
    if os.path.exists(f"{W}/.nas-off"):   # hwmon read only: never alerts, never touches drives
        fan = fetch_fan(); log_fan(fan); print(fan_label(fan)); return 0
    try: state = json.load(open(STATE))
    except Exception: state = {}
    cur = fetch()
    al, st = decide(cur, state)
    fan = fetch_fan() if cur is not None else None
    if cur is not None: log_fan(fan)
    al += fan_alerts(fan, cur is not None and cur["pool"]["state"] is not None)
    st["last"] = now()
    json.dump(st, open(STATE, "w"), indent=1)
    row = [now(), "down" if cur is None else (cur["pool"]["state"] or "?"), "" if cur is None else cur["pool"]["rwc"]]
    for d in DRIVES:
        s = (cur or {"drives": {}})["drives"].get(d) or {}
        row += [s.get("health", ""), s.get("defects", ""), s.get("unc", "")]
    row.append(len(al))
    new = not os.path.exists(HIST)
    with open(HIST, "a") as f:
        if new: f.write("time,pool,rwc," + ",".join(f"{d}_health,{d}_defects,{d}_unc" for d in DRIVES) + ",alerts\n")
        f.write(",".join(str(x) for x in row) + "\n")
    if os.environ.get("NASWATCH_NOALERT"): print("\n".join(al)); return 0
    for a in al:
        try: seen = a in open(RECENT).read()
        except Exception: seen = False
        if not seen:
            os.makedirs(os.path.dirname(ALERTS), exist_ok=True)
            open(ALERTS, "a").write(f"{now()} ALERT {a}\n"); open(RECENT, "a").write(a + "\n")
    print(fan_label(fan), "alerts:", al or "none")

if __name__ == "__main__": sys.exit(main())
