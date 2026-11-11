# chromedrop

AirDrop on ChromeOS! 
A thin installer + local web UI (installable PWA) around [Owly](https://github.com/t4t5/owly).

```
Chrome PWA (http://localhost:8780)  ->  chromedrop-web (root, Upstart)  ->  owly / owlyd  ->  awdl0  ->  iPhone
```

## Requirements
- x86_64 Chromebook with an Intel `iwlwifi` card (see Owly's supported hardware) and **developer mode on**
  (enabling it wipes the device).

## Install
1. In Chrome press Ctrl+Alt+T, type `shell`, then:
   `curl -fsSL https://raw.githubusercontent.com/NellowTCS/chromedrop/main/install.sh -o /tmp/i.sh`
2. Press Ctrl+Alt+F2 (VT-2), log in as `chronos`, run `sudo bash /tmp/i.sh`.
3. If it says **REBOOT**, reboot (Ctrl+D at the verification screen) and run step 2 again.
4. Back in Chrome (Ctrl+Alt+F1) open `http://localhost:8780` and click the install icon in the address bar.

Change the repo with `CHROMEDROP_REPO=user/repo sudo -E bash /tmp/i.sh`.
**Re-run the installer after every ChromeOS update**: updates overwrite `/etc`.

## Use
Turn AirDrop on, set the iPhone to **Everyone for 10 minutes**, and send. Accept/decline appears in the app.
While AirDrop is on Wi-Fi is off (Intel cards run in Exclusive mode); turning it off brings Wi-Fi back.
Files go in `MyFiles/Downloads`. Keep the app window open (minimised is fine) to get notifications.

## Notes
- Transfers are slow (channel 6, ~10-17 s per photo).
- The service runs as root and binds only to `127.0.0.1`. It rejects other `Host`/`Origin` headers and requires an `X-Chromedrop` header on POSTs.
- Sending parses `owly browse` output heuristically; if your device list looks odd, type the device name.
- `polkit-stub/` is optional: it lets plain `owly ...` run as `chronos` in crosh. Tag a release (`v0.1.0`) and CI builds `polkit-stub-x86_64`, which the installer downloads. The web UI runs as root and doesn't need it.
- Uninstall: `sudo bash uninstall.sh` (rootfs verification stays off).
