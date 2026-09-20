# Daily Geography Content Pipeline — crontab (fallback scheduler)
#
# PREFER ./install_schedule.sh. It installs LaunchAgents, and launchd runs a
# job that came due while the Mac was asleep; cron silently skips it. On a
# laptop that sleeps at night, cron means most days produce nothing.
#
# Use this only where launchd is not an option (a Linux box, a server):
#   crontab cron_jobs.sh      install
#   crontab -l                verify
#
# Also fixed here: the previous version called /usr/bin/flock, which does not
# exist on macOS, so the daily line failed before it ever reached python.
# run_daily.sh now takes its own lock with mkdir, which is portable.
CRON_TZ=Asia/Kolkata
MAILTO=""
PROJECT=/Users/maheshwaripoul/Claude/Projects/DailyGeoMap

# Daily: preflight, generate, publish, resume anything unrendered, retry the
# publish queue, write the heartbeat. run_daily.sh does all of it and logs.
0 4 * * * $PROJECT/run_daily.sh

# Publish queue: a failed upload is retried on a backoff, not lost.
17 */2 * * * cd $PROJECT && ./venv/bin/python retry_publish.py >> logs/cron_publish.log 2>&1

# Weekly stats refresh — Monday 03:00 IST
0 3 * * 1 cd $PROJECT && ./venv/bin/python stage1_refresh_stats.py >> logs/cron_weekly.log 2>&1
