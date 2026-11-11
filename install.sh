#!/bin/bash
# chromedrop installer. Run as root from VT-2 (Ctrl+Alt+F2):   sudo bash install.sh
# Safe to re-run (do so after every ChromeOS update).
set -uo pipefail
export PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin
REPO="${CHROMEDROP_REPO:-NellowTCS/chromedrop}"
BRANCH="${CHROMEDROP_BRANCH:-main}"
OWLY_VER="${OWLY_VERSION:-0.4.2}"
OWLY_URL="https://github.com/t4t5/owly/releases/download/v${OWLY_VER}/owly_${OWLY_VER}-1_amd64.deb"
WITH_STUB="${WITH_POLKIT_STUB:-1}"
say(){ printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die(){ printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root: sudo bash install.sh (from VT-2)"
[ "$(uname -m)" = x86_64 ] || die "x86_64 only (Owly's .deb is amd64)"
for c in python3 curl tar; do command -v $c >/dev/null || die "$c not found"; done

say "Checking that the root filesystem is writable"
mount -o remount,rw / 2>/dev/null
if ! touch /etc/.chromedrop-rw 2>/dev/null; then
  ROOTDEV=$(rootdev -s 2>/dev/null) || die "rootdev failed"
  ROOTNUM=${ROOTDEV##*[!0-9]}
  KPART=$((ROOTNUM - 1))
  say "Read-only. Removing rootfs verification (kernel partition $KPART)"
  /usr/share/vboot/bin/make_dev_ssd.sh --remove_rootfs_verification --partitions "$KPART" \
    || die "make_dev_ssd.sh failed (is developer mode on?)"
  echo; echo "Done. REBOOT now (Ctrl+D at the verification screen), then run this installer again."
  exit 0
fi
rm -f /etc/.chromedrop-rw

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
if [ ! -f "$SRC/server/chromedrop-web.py" ]; then
  say "Fetching chromedrop sources from github.com/$REPO"
  SRC=/tmp/chromedrop-src; rm -rf "$SRC"; mkdir -p "$SRC"
  curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" | tar xz -C "$SRC" --strip-components=1 \
    || die "could not download sources (set CHROMEDROP_REPO=user/repo)"
fi

say "Stopping old services"
for j in chromedrop-web polkit-stub owlyd; do stop $j 2>/dev/null; done

say "Installing Owly $OWLY_VER"
W=/tmp/chromedrop-owly; rm -rf "$W"; mkdir -p "$W"; cd "$W" || die "cd"
curl -fsSL -o owly.deb "$OWLY_URL" || die "download failed: $OWLY_URL"
python3 - owly.deb <<'PY'
import sys
d = open(sys.argv[1], 'rb').read(); i = 8        # a .deb is an ar archive
while i < len(d):
    n = d[i:i+16].decode().strip().rstrip('/'); s = int(d[i+48:i+58])
    open(n, 'wb').write(d[i+60:i+60+s]); i += 60 + s + (s & 1)
PY
tar xf data.tar.* || die "could not unpack data.tar.* (zstd? set OWLY_VERSION to an older release)"
install -d /usr/local/lib/owly
install -m 755 usr/lib/owly/owlyd /usr/local/lib/owly/owlyd || die "owlyd missing from package"
install -m 755 usr/bin/owly /usr/local/bin/owly || die "owly missing from package"
install -m 644 usr/share/dbus-1/system.d/io.github.t4t5.Owly.conf /etc/dbus-1/system.d/ || die "dbus policy missing"
cd /

say "Installing chromedrop"
install -d /usr/local/lib/chromedrop/web /var/lib/chromedrop
install -m 755 "$SRC/server/chromedrop-web.py" "$SRC/server/restore-wifi.sh" /usr/local/lib/chromedrop/
install -m 644 "$SRC"/web/* /usr/local/lib/chromedrop/web/
[ -f /etc/chromedrop.conf ] || install -m 644 "$SRC/chromedrop.conf" /etc/chromedrop.conf
install -m 644 "$SRC/init/owlyd.conf" "$SRC/init/chromedrop-web.conf" /etc/init/

say "Installing Bluetooth configuration"
if [ -f "$SRC/dbus/org.bluez.chromedrop.conf" ]; then
  install -m 644 "$SRC/dbus/org.bluez.chromedrop.conf" /etc/dbus-1/system.d/
fi

if [ "$WITH_STUB" = 1 ]; then
  say "Installing polkit stub (lets plain 'owly ...' work in crosh; the web UI doesn't need it)"
  if curl -fsSL -o /tmp/polkit-stub "https://github.com/$REPO/releases/latest/download/polkit-stub-x86_64"; then
    install -m 755 /tmp/polkit-stub /usr/local/bin/polkit-stub
    install -m 644 "$SRC/dbus/org.freedesktop.PolicyKit1.conf" /etc/dbus-1/system.d/
    install -m 644 "$SRC/init/polkit-stub.conf" /etc/init/
  else
    echo "no polkit-stub release found; skipping (create a GitHub release tag to build one)"
  fi
fi

say "Starting services"
dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1
for j in owlyd polkit-stub chromedrop-web; do
  [ -f /etc/init/$j.conf ] && { start $j >/dev/null 2>&1 || echo "warning: could not start $j"; }
done
sleep 2
owly doctor 2>&1 | head -8

PORT=$(sed -n 's/^PORT=//p' /etc/chromedrop.conf | head -1)
say "Done. Open http://localhost:${PORT:-8780} in Chrome and click the install icon in the address bar."
