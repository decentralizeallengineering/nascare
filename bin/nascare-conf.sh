# nascare-conf.sh: shared configuration for every nascare shell script (sourced, never executed).
# Precedence, highest first: environment variable, KEY=VALUE line in the config file, default below.
# Config file: $NASCARE_CONF, default ${XDG_CONFIG_HOME:-$HOME/.config}/nascare/nascare.conf (plain KEY=VALUE, '#' comments).

NASCARE_CONF=${NASCARE_CONF:-${XDG_CONFIG_HOME:-$HOME/.config}/nascare/nascare.conf}
if [ -r "$NASCARE_CONF" ]; then
  while IFS= read -r _l || [ -n "$_l" ]; do
    _l=${_l%%#*}; _k=${_l%%=*}; _v=${_l#*=}
    [ "$_k" != "$_l" ] || continue
    _k=${_k//[[:space:]]/}
    [[ $_k =~ ^[A-Z][A-Z0-9_]*$ ]] || continue
    _v=${_v#"${_v%%[![:space:]]*}"}; _v=${_v%"${_v##*[![:space:]]}"}
    _v=${_v#\"}; _v=${_v%\"}; _v=${_v#\'}; _v=${_v%\'}
    [ -z "${!_k+x}" ] && printf -v "$_k" '%s' "$_v"
  done < "$NASCARE_CONF"
fi
unset _l _k _v

: "${NAS_HOST:=}"                              # user@host of the NAS (ssh key login, passwordless sudo for smartctl, zpool, sg3_utils)
: "${NAS_SSH_OPTS:=-o BatchMode=yes -o ConnectTimeout=10}"
: "${NAS_POOL:=tank}"                          # zpool name on the NAS
: "${NAS_DRIVES:=sda sdb sdc}"                 # SAS drives of the pool
: "${NASCARE_STATE_DIR:=$HOME/.local/state/nascare}"
: "${ALERT_FILE:=$NASCARE_STATE_DIR/alerts.log}"
: "${NAS_SPINDOWN_C:=45}"                      # nas-heat: pause the backup flag and spin down idle drives
: "${NAS_SHUTDOWN_C:=50}"                      # nas-heat: export the pool and power the NAS off
: "${NAS_CLEAR_C:=42}"                         # nas-heat: clear the hot flag below this
: "${NAS_ALERT_C:=55}"                         # nas-health: alert at this temperature; above it run nas-off
: "${NAS_OFF_CMD:=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/nas-off}"
: "${NAS_PAUSE_UNITS:=}"                       # user systemd units (timers) to stop while the NAS is hot or off, space separated

[ -n "$NAS_HOST" ] || { echo "nascare: NAS_HOST is not set (see nascare.conf.example)" >&2; return 2 2>/dev/null || exit 2; }
mkdir -p "$NASCARE_STATE_DIR" 2>/dev/null
nas_ssh() { ssh $NAS_SSH_OPTS "$NAS_HOST" "$@"; }
nas_ts() { date '+%F %T'; }
nas_alert() { mkdir -p "$(dirname "$ALERT_FILE")"; echo "$(nas_ts) ALERT $*" >> "$ALERT_FILE"; }
