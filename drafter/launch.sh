#!/usr/bin/env bash
# Start one named job detached on the box (setsid, own log), so an ssh or session stop cannot kill it.
# usage: launch.sh NAME CMD [ARGS...]   -> /data/drafter/logs/NAME.log, exit code in /data/drafter/logs/NAME.rc
set -euo pipefail
NAME=$1; shift
mkdir -p /data/drafter/logs
cd /data/drafter
setsid nohup bash -c '"$@"; echo $? > /data/drafter/logs/'"$NAME"'.rc' _ "$@" \
  > /data/drafter/logs/$NAME.log 2>&1 < /dev/null &
echo "launched $NAME pid $!"
