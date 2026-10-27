#!/bin/bash
# nas-cool: every few minutes while the .nas-off flag exists, read the drive temperatures and stop any drive that is spinning.
# All commands use /dev/sgN: opening /dev/sdX read-write makes udev rescan the disk on close, which spins it back up.
# Does nothing while the pool is imported (stopping drives under an imported pool suspends it). Appends one line per run to
# nas-temps.csv in NASCARE_STATE_DIR. The .nas-hot flag belongs to nas-heat.py; nas-cool only reads temperatures.
case ${1-} in -h|--help) echo "usage: nas-cool.sh   (configuration: nascare.conf, see README; --help exits 2 without side effects)"; exit 2;; esac
. "$(dirname "$(readlink -f "$0")")/nascare-conf.sh" || exit 2
T=$NASCARE_STATE_DIR/nas-temps.csv
[ -e "$NASCARE_STATE_DIR/.nas-off" ] || exit 0
nas_ssh "sudo -n zpool list -H -o name $NAS_POOL" >/dev/null 2>&1 && exit 0
out=$(nas_ssh "for d in $NAS_DRIVES; do g=/dev/\$(ls /sys/block/\$d/device/scsi_generic/); sudo -n sg_turs \$g >/dev/null 2>&1; r=\$?; [ \$r = 0 ] && sudo -n sg_start --stop \$g >/dev/null 2>&1; t=\$(sudo -n sg_logs -p 0xd \$g | grep -oP \"Current temperature = \\K[0-9]+\"); echo \"\$d \$t \$r\"; done") || { nas_alert "NAS: unreachable (nas-cool)"; exit 0; }
now=$(nas_ts); echo "$now,$(echo "$out" | awk '{printf "%s=%s%s ", $1, $2, ($3==0?"(was spinning, stopped)":"")}')" >> "$T"
echo "$out" | awk '$3==0{s=s" "$1} END{if(s!="")exit 1}' || echo "$now ALERT NAS: drives found spinning while off, stopped:$(echo "$out" | awk '$3==0{printf " %s", $1}')" >> "$ALERT_FILE"
