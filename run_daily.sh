#!/bin/bash
# One unattended run, start to finish. This is what the scheduler calls.
#
#   preflight  — says in the log what would block publishing
#   pipeline   — draw, script, gate, art, voice, render, publish
#   --resume   — finish any script that passed the gate but never rendered
#   retry      — re-attempt publishes that are due in the queue
#
# Locking is a mkdir, not flock: macOS has no /usr/bin/flock, so the previous
# crontab's flock line could never have run at all.
cd "$(dirname "$0")" || exit 2
mkdir -p logs

LOCK="logs/daily.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date -u +%FT%TZ) another run holds $LOCK — exiting" >> logs/cron_daily.log
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

PY="./venv/bin/python"
[ -x "$PY" ] || PY="python3"

# Keep the log from growing without bound.
if [ -f logs/cron_daily.log ] && [ "$(wc -c < logs/cron_daily.log)" -gt 5000000 ]; then
  mv logs/cron_daily.log logs/cron_daily.log.1
fi

exec >> logs/cron_daily.log 2>&1
echo
echo "================ $(date -u +%FT%TZ) daily run ================"

"$PY" preflight.py || echo "[run_daily] preflight reported blockers — continuing so that whatever CAN run, does"

"$PY" core/sync_references.py

"$PY" pipeline_daily.py
status=$?
case $status in
  0) echo "[run_daily] published or rendered" ;;
  3) echo "[run_daily] out of model credit — backlog untouched, nothing else to try today" ;;
  *) echo "[run_daily] pipeline exit $status" ;;
esac

# A topic is spent once its script passes the gate, so an unrendered script is
# a topic already paid for. Finish it before drawing anything new tomorrow.
[ $status -ne 3 ] && "$PY" pipeline_daily.py --resume

"$PY" retry_publish.py

date -u +%FT%TZ > logs/.last_daily
echo "[run_daily] done $(date -u +%FT%TZ)"
exit $status
