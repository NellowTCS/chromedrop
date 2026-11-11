#!/bin/bash
# Removes chromedrop + Owly. Leaves rootfs verification disabled.
export PATH=/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
[ "$(id -u)" = 0 ] || { echo "run as root (VT-2)"; exit 1; }
mount -o remount,rw / 2>/dev/null
for j in chromedrop-web polkit-stub owlyd; do stop $j 2>/dev/null; rm -f /etc/init/$j.conf; done
rm -rf /usr/local/lib/chromedrop /usr/local/lib/owly /var/lib/chromedrop
rm -f /usr/local/bin/owly /usr/local/bin/polkit-stub /etc/chromedrop.conf \
      /etc/dbus-1/system.d/io.github.t4t5.Owly.conf \
      /etc/dbus-1/system.d/org.freedesktop.PolicyKit1.conf \
      /etc/dbus-1/system.d/org.bluez.chromedrop.conf
for j in wpasupplicant shill; do status $j | grep -q running || start $j; done
echo "removed."
