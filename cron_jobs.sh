# Daily Geography Content Pipeline — crontab
#
# Install with:   crontab cron_jobs.sh
# Verify with:    crontab -l
#
# Fixed here: the previous version redirected into logs/, which did not
# exist, so the shell failed on the redirect and neither job ever ran —
# silently, because the failure was in the redirect itself.
#
# Times are the system's local timezone. CRON_TZ makes that explicit
# instead of depending on how the daemon was started.
CRON_TZ=Asia/Kolkata
MAILTO=""
PROJECT=/Users/maheshwaripoul/Claude/Projects/DailyGeoMap

# Weekly stats refresh — Monday 03:00 IST
0 3 * * 1 cd $PROJECT && mkdir -p logs && /usr/bin/flock -n logs/weekly.lock ./venv/bin/python stage1_refresh_stats.py >> logs/cron_weekly.log 2>&1

# Daily generate & publish — 04:00 IST
# flock -n stops a slow run from overlapping with the next one.
# The trailing date write is a heartbeat: if logs/.last_daily is stale,
# cron is not firing, whatever the log says.
0 4 * * * cd $PROJECT && mkdir -p logs && /usr/bin/flock -n logs/daily.lock ./venv/bin/python pipeline_daily.py >> logs/cron_daily.log 2>&1; date -u +\%FT\%TZ > $PROJECT/logs/.last_daily
