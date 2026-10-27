#!/bin/bash
# nas-health: NAS hardware watcher. Token-free, run by nas-health.timer. Appends ALERT lines to ALERT_FILE on: pool not ONLINE
# or with errors, disk at or above NAS_ALERT_C (55), growing grown-defect count, failed self-test, allow_restart != 1, NAS unreachable.
# While the drives are spun down (.nas-off flag) it never touches them (any smartctl/zpool access spins them up) and only
# watches the NAS kernel log for unexpected spin-ups. Above NAS_ALERT_C it stops NAS_PAUSE_UNITS and runs NAS_OFF_CMD.
# Test hook: NAS_TEST_MAX=<temp> replaces the measured maximum temperature and prints instead of running NAS_OFF_CMD.
case ${1-} in -h|--help) echo "usage: nas-health.sh   (configuration: nascare.conf, see README; --help exits 2 without side effects)"; exit 2;; esac
. "$(dirname "$(readlink -f "$0")")/nascare-conf.sh" || exit 2
LOG=$ALERT_FILE; ST=$NASCARE_STATE_DIR/nas-health.state; OUT=$NASCARE_STATE_DIR/nas-health.last; OFFF=$NASCARE_STATE_DIR/.nas-off
ts() { nas_ts; }
# allow_restart must be 1 on every NAS disk (sysfs read, does not wake drives); 0 makes reads after a stop-unit count as ZFS checksum errors.
ar=$(nas_ssh 'cat /sys/block/sd*/device/scsi_disk/*/allow_restart' 2>/dev/null | grep -vcx 1)
[ "${ar:-0}" -gt 0 ] && echo "$(ts) ALERT NAS: allow_restart != 1 on $ar disk(s)" >> "$LOG"
if [ -e "$OFFF" ]; then
  n=$(nas_ssh 'sudo -n dmesg | grep -c "Spinning up"') || { echo "$(ts) ALERT NAS: unreachable" >> "$LOG"; exit 0; }
  b=$(cat "$OFFF"); [ "${n:-0}" -gt "${b:-0}" ] && { echo "$(ts) ALERT NAS: drives spun up while off ($b -> $n)" >> "$LOG"; echo "$n" > "$OFFF"; }
  echo "NAS-OFF drives spun down; spin-ups logged $n" > "$OUT"; exit 0
fi
if ! nas_ssh "
  sudo -n zpool status -x $NAS_POOL | head -1
  sudo -n zpool list -H -o health $NAS_POOL
  for d in $NAS_DRIVES; do
    a=\$(sudo -n smartctl -a /dev/\$d)
    t=\$(echo \"\$a\" | awk -F: '/Current Drive Temperature/{print \$2+0}')
    g=\$(echo \"\$a\" | awk -F: '/grown defect list/{print \$2+0}')
    f=\$(sudo -n smartctl -l selftest /dev/\$d | grep -ciE 'fail')
    echo \"DISK \$d \$t \$g \$f\"
  done" > "$OUT" 2>&1; then
  echo "$(ts) ALERT NAS: unreachable or ssh failed" >> "$LOG"; exit 0
fi
grep -q "is healthy" "$OUT" || echo "$(ts) ALERT NAS: pool $(head -1 "$OUT")" >> "$LOG"
sed -n 2p "$OUT" | grep -qx ONLINE || echo "$(ts) ALERT NAS: pool health $(sed -n 2p "$OUT")" >> "$LOG"
touch "$ST"
while read -r _ d t g f; do
  oldt=$(awk -v d="$d" '$1==d{print $3}' "$ST")
  [ "${t:-0}" -ge "$NAS_ALERT_C" ] && [ "${t:-0}" -ge $(( ${oldt:-0} + 2 )) ] && echo "$(ts) ALERT NAS: $d at ${t} C" >> "$LOG"
  [ "${t:-0}" -ge "$NAS_ALERT_C" ] && [ "${oldt:-0}" -gt "$t" ] && t=$oldt
  [ "${f:-0}" -gt 0 ] && echo "$(ts) ALERT NAS: $d self-test failed" >> "$LOG"
  old=$(awk -v d="$d" '$1==d{print $2}' "$ST")
  [ -n "$old" ] && [ "$g" -gt "$old" ] && echo "$(ts) ALERT NAS: $d grown defects $old -> $g" >> "$LOG"
  echo "$d $g $t"
done < <(grep '^DISK' "$OUT") > "$ST.new" && mv "$ST.new" "$ST"
# Heat guard: SAS drives are typically rated 60 C. Above NAS_ALERT_C, stop host->NAS transfers (NAS_PAUSE_UNITS) and run nas-off
# (export pool, spin down, .nas-off flag); resume with nas-on.
max=$(awk '/^DISK/{if($3>m)m=$3} END{print m+0}' "$OUT"); max=${NAS_TEST_MAX:-$max}
if [ "$max" -gt "$NAS_ALERT_C" ]; then
  for u in $NAS_PAUSE_UNITS; do
    systemctl --user is-active -q "$u" && { systemctl --user stop "$u"; echo "$(ts) ALERT NAS: disk at ${max} C, $u stopped" >> "$LOG"; }
  done
  if [ -n "$NAS_TEST_MAX" ]; then echo "TEST: would run $NAS_OFF_CMD"; else
    $NAS_OFF_CMD >/dev/null 2>&1 && [ -e "$OFFF" ] \
      && echo "$(ts) ALERT NAS: disk at ${max} C (>$NAS_ALERT_C), nas-off ran: pool exported, disks spun down" >> "$LOG" \
      || echo "$(ts) ALERT NAS: disk at ${max} C (>$NAS_ALERT_C), nas-off FAILED" >> "$LOG"; fi
fi
