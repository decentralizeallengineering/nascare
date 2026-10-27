#!/bin/bash
# naswatch: low-priority wrapper for naswatch.py (pool, SMART and fan health watcher), run by naswatch.timer every 30 min.
exec nice -n 10 python3 "$(dirname "$(readlink -f "$0")")/naswatch.py" "$@"
