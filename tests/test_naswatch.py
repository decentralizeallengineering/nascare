import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
os.environ.setdefault("NAS_HOST", "ann@nas.example.invalid")
os.environ["NASCARE_CONF"] = "/nonexistent"
import naswatch as n
n.BASE_DEFECTS = {"sdc": 16}   # a known-old defect count on sdc; the tests below rely on it
n.SAS_FAN = "fan5"             # enable the drive-bay fan check
POOL_OK = "pool 'nas' is healthy\n==POOL==\n  pool: nas\n state: ONLINE\n  scan: scrub repaired 0B in 00:20:00 with 0 errors on Sun\nconfig:\n\tnas ONLINE 0 0 0\n\t  raidz1-0 ONLINE 0 0 0\n\t    a ONLINE 0 0 0\nerrors: No known data errors\n"
def smart(h="OK", d=16, u=0):
    return f"SMART Health Status: {h}\nElements in grown defect list: {d}\nread:    1 2 0 3 4 5.0 {u}\nwrite:   0 0 0 0 4 5.0 0\nverify:  1 2 0 3 4 5.0 0\n"
def build(pool=POOL_OK, **kw):
    return n.parse_all(pool + "".join(f"==SMART {d}==\n" + kw.get(d, smart()) for d in n.DRIVES))
def run(cur, st=None): return n.decide(cur, st or {})
def test_healthy():
    assert run(build())[0] == []
def test_degraded():
    p = POOL_OK.replace("healthy", "DEGRADED").replace("state: ONLINE", "state: DEGRADED")
    assert any("pool state" in a for a in run(build(p))[0])
def test_cksum():
    p = POOL_OK.replace("a ONLINE 0 0 0", "a ONLINE 0 0 2")
    assert any("CKSUM" in a for a in run(build(p))[0])
def test_defects_rise_then_once():
    al, st = run(build(sdc=smart(d=17)))
    assert any("sdc grown defects 16 -> 17" in a for a in al)
    assert run(build(sdc=smart(d=17)), st)[0] == []
def test_other_drive_baseline_learned():
    al, st = run(build(sda=smart(d=3)))
    assert al == [] and run(build(sda=smart(d=4)), st)[0]
def test_smart_and_uncorrected():
    al = run(build(sdb=smart(h="FAILURE PREDICTION", u=2)))[0]
    assert any("SMART health" in a for a in al) and any("uncorrected" in a for a in al)
def test_scrub_repairs():
    p = POOL_OK.replace("repaired 0B in 00:20:00 with 0 errors", "repaired 4K in 00:20:00 with 0 errors")
    assert any("scrub" in a for a in run(build(p))[0])
def test_unreachable_three():
    st = {}
    for i in range(2):
        al, st = run(None, st); assert al == []
    al, st = run(None, st); assert any("unreachable" in a for a in al)
    assert run(build(), st)[1]["fails"] == 0

def test_fan_parse_and_alert():
    assert n.parse_fan("FAN ok 2100\n") == ("ok", 2100) and n.parse_fan("FAN nodrv\n") == ("nodrv", None)
    assert n.parse_fan("FAN ok \n") == ("ok", None) and n.parse_fan("") is None
    assert n.fan_alerts(("ok", 2100), True) == []
    assert any("RPM 0" in a for a in n.fan_alerts(("ok", 0), True))
    assert any("unreadable" in a for a in n.fan_alerts(("ok", None), True))
    assert n.fan_alerts(("ok", 0), False) == [] and n.fan_alerts(("nodrv", None), True) == []
    assert n.fan_label(("ok", 1500)) == "SAS fan=1500rpm"
