#!/bin/bash
# nas-err-check: ZFS error-growth check, run every 30 min by nas-err-check.timer. Sums the READ, WRITE and CKSUM error counters
# of the pool's devices. The first run stores the sum as the baseline; if a later sum is larger, the pool is exported to
# protect the data and an alarm line is written (ALERT_FILE; NAS_ALARM_HOOK, if set, is called with the line). No scrub, no writes
# otherwise. A pool that is not imported (exported / NAS off) is fine and skipped.
case ${1-} in -h|--help) echo "usage: nas-err-check.sh   (configuration: nascare.conf, see README; --help exits 2 without side effects)"; exit 2;; esac
. "$(dirname "$(readlink -f "$0")")/nascare-conf.sh" || exit 2
: "${NAS_ALARM_HOOK:=}"
D=$NASCARE_STATE_DIR
out=$(nas_ssh "sudo -n zpool status -p $NAS_POOL" 2>&1) || exit 0
echo "$out" | grep -q 'no such pool' && exit 0
sum=$(echo "$out" | awk '$1 ~ /^(wwn|sd|nvme|ata|scsi)/ {s+=$3+$4+$5} END{print s+0}')
[ -f "$D/base" ] || { echo "$sum" > "$D/base"; exit 0; }
base=$(cat "$D/base")
if [ "$sum" -gt "$base" ]; then
  m="$(date '+%F %H:%M') nascare ALARM zpool $NAS_POOL error count grew $base -> $sum while idle; exporting pool to protect data"
  echo "$m" >> "$ALERT_FILE"; [ -n "$NAS_ALARM_HOOK" ] && $NAS_ALARM_HOOK "$m"
  nas_ssh "sudo -n zpool export $NAS_POOL" >> "$D/export.log" 2>&1
  echo "$sum" > "$D/base"
fi
