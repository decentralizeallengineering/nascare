"""Shared nascare configuration for the Python scripts: environment, then nascare.conf, then default."""
import os, re

HOME = os.path.expanduser("~")
CONF_FILE = os.environ.get("NASCARE_CONF", os.path.join(os.environ.get("XDG_CONFIG_HOME", HOME + "/.config"), "nascare/nascare.conf"))

def _file_conf():
    res = {}
    try:
        for ln in open(CONF_FILE):
            a, sep, b = ln.split("#")[0].partition("=")
            a = a.strip()
            if sep and re.fullmatch(r"[A-Z][A-Z0-9_]*", a): res[a] = b.strip().strip("\"'")
    except OSError: pass
    return res

_FC = _file_conf()

def cfg(key, default=""):
    return os.environ.get(key, _FC.get(key, default))

def cfg_int(key, default):
    try: return int(cfg(key, str(default)))
    except ValueError: return default

STATE_DIR = cfg("NASCARE_STATE_DIR", HOME + "/.local/state/nascare")
ALERT_FILE = cfg("ALERT_FILE", STATE_DIR + "/alerts.log")
NAS_HOST = cfg("NAS_HOST")
SSH_OPTS = cfg("NAS_SSH_OPTS", "-o BatchMode=yes -o ConnectTimeout=10").split()
POOL = cfg("NAS_POOL", "tank")
DRIVES = cfg("NAS_DRIVES", "sda sdb sdc").split()
