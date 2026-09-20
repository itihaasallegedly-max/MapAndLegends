#!/bin/bash
# Install the schedule as macOS LaunchAgents.
#
# Why not cron: cron does not run a job that was due while the Mac was asleep,
# and this Mac is asleep at 04:00 most nights. launchd runs a missed
# StartCalendarInterval job as soon as the machine wakes, which is the whole
# difference between "scheduled" and "actually happens".
#
#   ./install_schedule.sh            install / reinstall
#   ./install_schedule.sh --remove   uninstall
set -e
PROJECT="$(cd "$(dirname "$0")" && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LABELS=(com.mapandlegend.daily com.mapandlegend.publishretry com.mapandlegend.weekly)

mkdir -p "$AGENTS"

unload_all() {
  for label in "${LABELS[@]}"; do
    launchctl bootout "gui/$UID/$label" 2>/dev/null || true
  done
}

if [ "$1" = "--remove" ]; then
  unload_all
  for label in "${LABELS[@]}"; do rm -f "$AGENTS/$label.plist"; done
  echo "removed."
  exit 0
fi

chmod +x "$PROJECT/run_daily.sh"

write_plist() {
  label="$1"; shift
  cat > "$AGENTS/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
$(for arg in "$@"; do echo "    <string>$arg</string>"; done)
  </array>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>StandardOutPath</key><string>$PROJECT/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/logs/launchd.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><false/>
PLIST
}

close_plist() { cat >> "$AGENTS/$1.plist" <<'PLIST'
</dict>
</plist>
PLIST
}

# --- daily, 04:00 local
write_plist com.mapandlegend.daily "$PROJECT/run_daily.sh"
cat >> "$AGENTS/com.mapandlegend.daily.plist" <<'PLIST'
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>0</integer></dict>
PLIST
close_plist com.mapandlegend.daily

# --- publish retries, every 2 hours
write_plist com.mapandlegend.publishretry "$PROJECT/venv/bin/python" "$PROJECT/retry_publish.py"
cat >> "$AGENTS/com.mapandlegend.publishretry.plist" <<'PLIST'
  <key>StartInterval</key><integer>7200</integer>
PLIST
close_plist com.mapandlegend.publishretry

# --- weekly stats refresh, Monday 03:00
write_plist com.mapandlegend.weekly "$PROJECT/venv/bin/python" "$PROJECT/stage1_refresh_stats.py"
cat >> "$AGENTS/com.mapandlegend.weekly.plist" <<'PLIST'
  <key>StartCalendarInterval</key>
  <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer></dict>
PLIST
close_plist com.mapandlegend.weekly

unload_all
for label in "${LABELS[@]}"; do
  launchctl bootstrap "gui/$UID" "$AGENTS/$label.plist"
  echo "loaded $label"
done

echo
echo "Installed. Check with:  launchctl list | grep mapandlegend"
echo "Run one now with:       launchctl kickstart -k gui/$UID/com.mapandlegend.daily"
echo "Watch it with:          tail -f $PROJECT/logs/cron_daily.log"
