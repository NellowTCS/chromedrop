#!/bin/sh
# Runs when chromedrop-web stops: give Wi-Fi back to ChromeOS.
export PATH=/sbin:/usr/sbin:/usr/local/bin:/bin:/usr/bin
[ -e /sys/class/net/awdl0 ] && owly radio down >/dev/null 2>&1
for j in wpasupplicant shill; do
  status "$j" 2>/dev/null | grep -q running || start "$j"
done
exit 0
