"""Smoke tests for nascare. They replace ssh/sudo/smartctl/zpool with local stubs; no NAS needed. Run: python3 -m pytest tests -q"""
import os, stat, subprocess, sys, textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin")

def tool(d, name, body):
    p = d / name
    p.write_text("#!/bin/bash\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)

def make_env(tmp_path, smart_temp=40, defects=16, cksum=0, extra=None):
    stubs = tmp_path / "stubs"; stubs.mkdir(exist_ok=True)
    tool(stubs, "ssh", 'cmd="${@: -1}"; echo "$cmd" >> "$STUB_LOG.ssh"; exec bash -c "$cmd"\n')
    tool(stubs, "sudo", '[ "$1" = -n ] && shift; echo "sudo $*" >> "$STUB_LOG.sudo"; exec "$@"\n')
    tool(stubs, "smartctl", f'''
        case "$*" in
          *selftest*) echo "# 1 Background short Completed";;
          *-A*|*-a*) echo "Current Drive Temperature:     {smart_temp} C"; echo "Elements in grown defect list: {defects}";;
        esac
    ''')
    tool(stubs, "zpool", f'''
        case "$*" in
          "status -x"*) echo "pool 'tank' is healthy";;
          "list -H -o health"*) echo ONLINE;;
          "list"*) echo tank;;
          "status -p"*|"status"*) printf 'pool: tank\\n state: ONLINE\\n\\tNAME STATE READ WRITE CKSUM\\n\\tsda ONLINE 0 0 {cksum}\\n\\tsdb ONLINE 0 0 0\\n';;
          "export"*) echo "export $*" >> "$STUB_LOG.zpool";;
        esac
    ''')
    tool(stubs, "systemctl", 'echo "systemctl $*" >> "$STUB_LOG.systemctl"\n')   # never let a test reach the real systemctl poweroff
    tool(stubs, "dmesg", "echo 'Spinning up disk'\n")
    state = tmp_path / "state"
    env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}", NASCARE_CONF="/nonexistent", NAS_HOST="ann@nas.example.invalid",
               NASCARE_STATE_DIR=str(state), STUB_LOG=str(tmp_path / "log"), NAS_POOL="tank", NAS_DRIVES="sda sdb")
    env.update(extra or {})
    return env, state

def run(cmd, env):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)

def test_help_exits_2_without_side_effects(tmp_path):
    env, state = make_env(tmp_path)
    for name in ("nas-cool.sh", "nas-health.sh", "nas-err-check.sh", "nas-off", "nas-on"):
        r = run([os.path.join(BIN, name), "--help"], env)
        assert r.returncode == 2 and "usage" in r.stdout + r.stderr, name
    for name in ("nas-heat.py", "naswatch.py"):
        r = run([sys.executable, os.path.join(BIN, name), "--help"], env)
        assert r.returncode == 0 and "NAS" in r.stdout, name
    assert not os.path.exists(tmp_path / "log.ssh")

def test_missing_nas_host_is_reported(tmp_path):
    env, _ = make_env(tmp_path); env["NAS_HOST"] = ""
    r = run([os.path.join(BIN, "nas-health.sh")], env)
    assert r.returncode == 2 and "NAS_HOST is not set" in r.stderr

def test_err_check_baseline_then_export_on_growth(tmp_path):
    env, state = make_env(tmp_path, cksum=0)
    assert run([os.path.join(BIN, "nas-err-check.sh")], env).returncode == 0
    assert (state / "base").read_text().strip() == "0"
    env, _ = make_env(tmp_path, cksum=3)
    run([os.path.join(BIN, "nas-err-check.sh")], env)
    assert "error count grew 0 -> 3" in (state / "alerts.log").read_text()
    assert "export" in (tmp_path / "log.zpool").read_text()
    assert (state / "base").read_text().strip() == "3"

def test_health_alerts_on_rising_defects_only_once(tmp_path):
    env, state = make_env(tmp_path, defects=16)
    run([os.path.join(BIN, "nas-health.sh")], env)
    assert not (state / "alerts.log").exists() or "grown defects" not in (state / "alerts.log").read_text()
    env, _ = make_env(tmp_path, defects=19)
    run([os.path.join(BIN, "nas-health.sh")], env)
    assert "sda grown defects 16 -> 19" in (state / "alerts.log").read_text()

def test_health_hot_drive_runs_nas_off(tmp_path):
    env, state = make_env(tmp_path, smart_temp=40)
    env["NAS_TEST_MAX"] = "58"
    r = run([os.path.join(BIN, "nas-health.sh")], env)
    assert "TEST: would run" in r.stdout

def test_nas_heat_spins_down_when_warm_and_idle(tmp_path):
    env, state = make_env(tmp_path, smart_temp=47)
    stubs = tmp_path / "stubs"
    tool(stubs, "fake-nas-off", f'echo called >> {tmp_path}/nasoff.calls\n')
    env["NASHEAT_NASOFF"] = str(stubs / "fake-nas-off"); env["NASHEAT_DIR"] = str(state)
    state.mkdir(exist_ok=True)
    r = run([sys.executable, os.path.join(BIN, "nas-heat.py")], env)
    assert r.returncode == 0
    assert (state / ".nas-hot").exists()
    log = (state / "nas-heat.log").read_text()
    assert "max 47 C" in log and ("nas-off rc=0" in log or "NAS busy" in log)

def test_nas_heat_shuts_down_at_limit(tmp_path):
    env, state = make_env(tmp_path, smart_temp=51)
    env["NASHEAT_DIR"] = str(state); state.mkdir(exist_ok=True)
    run([sys.executable, os.path.join(BIN, "nas-heat.py")], env)
    assert "NAS SHUTDOWN drive sd" in (state / "alerts.log").read_text()
    assert "poweroff" in (tmp_path / "log.ssh").read_text()
