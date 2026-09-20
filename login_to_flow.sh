#!/bin/bash
echo "Opening Google Chrome with the automation profile..."
echo "Please log in to your Google account."
echo "Keep the Chrome window OPEN. The automation script will attach to it."
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --user-data-dir=$HOME/.dailygeomap_chrome_profile --remote-debugging-port=9222 "https://flow.google.com"
echo "Chrome closed."
