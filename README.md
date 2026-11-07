# krbctl — Kerberos tickets for the Linux desktop

A tray icon and a native GTK4 window for MIT Kerberos. It answers the two
questions you actually have — *is my ticket still good?* and *please renew it* —
without remembering that `kinit -k -t ~/.keytab user@REALM` is a thing.

Built to run anywhere: Noctalia, Waybar, KDE Plasma, GNOME with the AppIndicator
extension. It speaks `org.kde.StatusNotifierItem` + `com.canonical.dbusmenu`
directly, so there is no GTK3/X11 dependency and no libappindicator.

The icon is Cerberus — the three-headed dog who guards the gates, which is
exactly what "Kerberos" means. It is drawn in cairo (`krbctl/mascot.py`), not
shipped as an image: one description of the face drives the tray, the window and
the launcher, it stays sharp at 16 px and 512 px, and there is no artwork file
to lose or license.

In the tray the face is a single-colour silhouette punched with holes for the
eyes, nose and mouth — the colour then carries the ticket state:

![icon states](data/icons/preview-states.png)

| green | amber | orange | red | faded |
|---|---|---|---|---|
| valid | expiring soon | cache unreadable | expired | no ticket |

At 22 px the tray drops the finer features (pupils, teeth, the mouth opening)
because a one-pixel detail is mud. The launcher icon keeps everything, and shows
three heads — or one below 32 px, where three would be a smudge.

## Why it exists

`kinit` + a systemd timer gets you a ticket. What you do not get is:

* a **countdown** you can glance at, instead of typing `klist`;
* a **renew** button for the 15 minutes before a ticket dies;
* a **sane failure** when the KDC is unreachable because the VPN is down —
  the timer just logs `Cannot contact any KDC` every hour, forever;
* somewhere to hold **more than one account**, with a password in the keyring
  for the ones that cannot use a keytab.

That is this app.

## Features

| | |
|---|---|
| **Live state** | valid / expiring / expired / no ticket / cache error, polled every 30 s |
| **Tray icon** | StatusNotifierItem with a state-coloured key, tooltip, and a working DBusMenu |
| **Native window** | GTK4 + libadwaita; accounts, ticket detail, renew, destroy |
| **Auto-renew** | renews from the keytab when the ticket is inside the warning window |
| **Backoff** | a failing renewal backs off 5 minutes instead of hammering a dead KDC |
| **Multiple accounts** | per-account cache (`KCM:`, `KEYRING:`, `FILE:`) or the session default |
| **Passwords** | stored in the Secret Service via libsecret, or asked each time |
| **Keytab creation** | builds one from a password with `ktutil` — no incantations |
| **Notifications** | expiring, expired, and renewal failure |
| **Scriptable** | `krbctl status --json` for bars, scripts and cron |
| **Zero config** | auto-detects `~/.keytab` on first run and seeds the account |

## Install

```bash
git clone https://github.com/panas78/krbhandler.git
cd krbctl
./install.sh
```

That single command creates:

| Put where | What |
|---|---|
| `~/.local/share/krbctl/` | the application |
| `~/.local/bin/krbctl` | launcher on `PATH` |
| `~/.local/share/applications/io.github.pan.Krbctl.desktop` | menu entry |
| `~/.local/share/icons/hicolor/*/apps/krbctl.png` | icon at 9 sizes |
| `~/.local/share/metainfo/` | AppStream metadata |
| `~/.config/autostart/krbctl-tray.desktop` | tray on login |

Both `.desktop` files are validated with `desktop-file-validate` during install,
because a malformed entry silently appears in no menu at all.

The GUI stack comes from the distro (`python3-gobject`, `gtk4`, `libadwaita`,
`python3-cairo`, `libsecret`, `krb5-workstation`), **not** from pip — those
packages ship typelibs that must match the system libraries, and a venv
PyGObject routinely cannot see them.

```bash
./install.sh --no-autostart   # don't start the tray on login
./install.sh --uninstall      # remove everything it installed
```

## Use

```bash
krbctl gui                   # open the window
krbctl tray                  # tray icon in the foreground
krbctl status                # one line per account
krbctl status --json         # machine readable
krbctl renew                 # renew now
krbctl destroy               # drop the ticket
krbctl doctor                # what is missing on this machine
```

`status` exits `0` when everything is valid, `1` when a ticket is expiring,
expired or missing, and `2` on a cache error — so a bar module or a cron job can
branch on it.

### JSON shape

```json
{
  "worst": "valid",
  "count": 1,
  "accounts": [
    {
      "id": "pperivolaris-diastasys-gr",
      "label": "DIASTASYS.GR",
      "principal": "pperivolaris@DIASTASYS.GR",
      "state": "valid",
      "badge": "9h59m",
      "detail": "Expires Sun 08 Nov 01:19 · renewable until Sat 14 Nov 15:19",
      "seconds_left": 35967,
      "expires": "2026-11-08T01:19:27",
      "renew_until": "2026-11-14T15:19:27",
      "cache": "KCM:1000",
      "auto_renew": true,
      "keytab": "/home/user/.keytab",
      "error": null
    }
  ]
}
```

## Status bars

**Noctalia / Waybar / KDE / GNOME AppIndicator** — nothing to configure. The app
registers an SNI item named `krbctl` with `org.kde.StatusNotifierWatcher`; the
host picks it up. Hosts that show only a few tray icons (Noctalia collapses the
rest behind an expander) may hide it, which is why `krbctl status --json` also
exists for a text badge.

**Waybar `custom` module:**

```json
"custom/krb": {
  "exec": "krbctl status --json --compact",
  "interval": 30,
  "return-type": "json",
  "format": "{}",
  "exec-if": "which krbctl"
}
```

**Anything with a script hook** — read `.accounts[0].badge` from the JSON.

## Layout

```
krbctl/
├── krbctl/
│   ├── kerberos.py   # klist/kinit/kdestroy/ktutil wrappers + state machine
│   ├── config.py     # accounts.json, first-run auto-detect
│   ├── secrets.py    # libsecret password storage
│   ├── cli.py        # krbctl status/renew/destroy/doctor/...
│   ├── icons.py      # cairo-drawn key icons (no image assets)
│   ├── tray.py       # StatusNotifierItem + DBusMenu over Gio
│   ├── window.py     # GTK4 / libadwaita window
│   └── app.py        # poller, auto-renew, notifications
├── data/
│   ├── icons/hicolor/     # generated PNG icon set, one dir per size
│   ├── *.desktop.in       # desktop entry templates
│   └── *.metainfo.xml     # AppStream metadata
├── tools/
│   ├── make-icons.py      # regenerates the icon set + preview sheet
│   └── tray-probe.py      # isolates tray/D-Bus problems
├── tests/                 # parsing + classification, no KDC needed
└── install.sh
```

Design rule: **one brain, many front-ends.** Every Kerberos operation lives in
`kerberos.py` and is reachable without GTK, so the CLI works over SSH and the
tests run without a display. The GUI is a consumer, not the owner.

## Configuration

`~/.config/krbctl/accounts.json` (mode `0600`). No passwords in it — those live
in the keyring, keyed by principal.

```json
{
  "version": 1,
  "accounts": [
    {
      "id": "user-example-com",
      "principal": "user@EXAMPLE.COM",
      "label": "Work",
      "method": "keytab",
      "keytab": "~/.keytab",
      "cache": "",
      "auto_renew": true,
      "remember_password": true,
      "warn_minutes": 15
    }
  ],
  "prefs": { "poll_seconds": 30, "notify": true, "auto_renew_global": true }
}
```

`cache: ""` means "whatever the session default is" — which respects
`KRB5CCNAME` and `/etc/krb5.conf`'s `default_ccache_name`, so a KCM or KEYRING
setup needs nothing special.

## Gotchas worth knowing

* **Locale.** `klist` prints dates through `strftime("%x")`. On a Greek desktop
  `07/11/2026` is 7 November; under `LC_ALL=C` the same ticket prints
  `11/07/2026`. Every subprocess therefore runs with `LC_ALL=C`, and the parser
  assumes month-first. Mixing the two shifts a ticket by months.
* **The TGT is not the last row.** `klist` lists service tickets after the
  `krbtgt/` row, and those expire after it. The parser takes the timestamp from
  the `krbtgt/` row specifically.
* **Passwords never touch argv.** `kinit --password-file=STDIN` and a `ktutil`
  script on stdin, so nothing sensitive appears in `ps`.
* **`ktutil addent` still needs the KDC** to derive the salt, so creating a
  keytab requires the VPN up and the correct password — it is not offline.
* **Auto-renew never spends a stored password.** Only keytab accounts renew
  unattended; password accounts renew when you press the button.

## Requirements

`krb5-workstation` (or `krb5-user` on Debian), plus GTK4, libadwaita,
PyGObject, pycairo and libsecret. Verified on Fedora 45 with GTK 4.24 /
libadwaita 1.10.

## License

MIT
