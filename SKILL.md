---
name: personal-webdav-for-zotero
description: Deploy, repair and troubleshoot a self-hosted WebDAV server for Zotero attachment sync (rclone serve webdav plus a password auth proxy, run as a login service on macOS or Linux), reachable from anywhere through ZeroTier with router port forwarding, plus a Zotero plugin that fixes Zotero's "file never downloads" WebDAV bug. Use when someone wants Zotero file sync without paying for Zotero storage, wants to host Zotero PDFs on their own disk or NAS, or reports that Zotero sync looks successful but PDFs are missing on another computer, that the sync icon spins briefly and stops without downloading, that a second computer does not upload, or that sync fails on a campus, guest or hotel Wi-Fi. Also use when setting up port forwarding through one or two NAT layers for ZeroTier, or deciding between ZeroTier and Tailscale. Triggers include 「Zotero WebDAV」「Zotero 同步不下载」「附件没同步」「坚果云替代」「自建 WebDAV」「ZeroTier 端口转发」.
---

# Personal WebDAV for Zotero

This skill sets up and maintains a working Zotero file-sync stack:

```
Zotero (client A) ─┐                       ┌─ rclone serve webdav :8080
Zotero (client B) ─┼─ ZeroTier (encrypted) ─┤    └─ auth_proxy.py (PBKDF2 accounts, lockout,
Zotero (server)  ──┘   UDP 9993, forwarded  │       refuses when the storage disk is not mounted)
                       on the home routers   └─ serves  <root>/zotero/KEY.zip + KEY.prop
Item metadata still syncs through zotero.org (free); only attachment files go to WebDAV.
```

The stack has four parts:

| Part | Folder | Who does it |
|---|---|---|
| WebDAV server: rclone, auth proxy, login service | `server/` | agent (scripted); a person types account passwords |
| Overlay network and port forwarding | `network/connectivity.md` | person (account sign-in, router admin); agent detects and verifies |
| Zotero client settings | below | person (types the password into Zotero) |
| Bug-fix plugin | `plugin/` | person (installs it in Zotero); agent builds and verifies |
| Diagnosis | `tools/diagnose.py` | agent |

**The agent never types passwords, router credentials or account logins, and never changes router settings itself.**
For those steps it tells the person exactly what to click and enter, then verifies the result from the machine.
It also never writes to `zotero.sqlite` while Zotero is running. Zotero's state changes go through Zotero, via the plugin or its UI.

## Workflow

### 0. Assess
- Find which machine is the server: always on, owns the disk with the files.
- Record each machine's OS and where every client will connect from (home, campus, travel).
- Check whether a stack is already there: `~/.config/webdav-zotero/`, `launchctl list | grep webdav` (macOS), `systemctl --user status '*webdav*'` (Linux).
- If something is broken rather than new, start with `python3 tools/diagnose.py` (see Troubleshooting).

### 1. Server (macOS or Linux): agent

1. Install rclone: `brew install rclone` (macOS) or the package / https://rclone.org/install/ (Linux).
2. Choose the storage folder. Zotero will create a `zotero/` subfolder in it. If the folder is on an external drive, pass `--volume <mountpoint>`. The proxy then refuses logins while the drive is unplugged, instead of serving an empty folder that would make clients think every file is gone.
3. Run the installer:
   ```
   sh server/install_server.sh --root "<storage folder>" [--volume "<mountpoint>"] [--port 8080] \
      [--bind <ZeroTier IP>] [--bonjour "<name>"]
   ```
   - `--bind <ZeroTier IP>` keeps the plain-HTTP port off the LAN. Use it once ZeroTier is up (step 2).
   - The installer is idempotent and never touches existing accounts.
   - It prints `server answers HTTP 401`. 401 means the server is running and asking for a login, which is correct.
4. **Human step:** ask the person to run `~/.config/webdav-zotero/davuser add <name>` in their own terminal and type a password. Other commands: `davuser list | passwd | del | unlock | log`.

The server cannot run on Windows as is, because `auth_proxy.py` uses POSIX `fcntl`. A Windows-only household should use WSL or another WebDAV server. **Windows clients are fully supported.**

### 2. Network: person, guided by the agent

Follow `network/connectivity.md`:

- **§1 ZeroTier:** create the network, join each machine, authorize the members.
- **§3 Port forwarding:**
  1. The agent detects the NAT layers with `traceroute -i <iface>`, identifies the routers, and checks for carrier-grade NAT.
  2. The person adds **UDP 9993 → server** on *every* NAT layer, and reserves the server's IP on each layer.
  3. The agent verifies that `surfaceAddresses` contains `<public IP>/9993`.
- **Final check, from the roaming client on the restrictive network:** `zerotier-cli peers` should show `DIRECT <public IP>/9993`. If it shows `RELAY`, see §4 (use another Wi-Fi, or Tailscale).

### 3. Zotero clients: person

The settings are the same on Windows, macOS and Linux. Open Zotero → Settings → Sync (设置 → 同步) → File Syncing (文件同步):

- Tick "Sync attachment files in My Library using **WebDAV**".
- URL: `http://` + `<server ZeroTier IP>:8080`. Do not add a path; Zotero appends `/zotero/` itself.
- Username and password: the `davuser` account.
- Click **Verify Server** (验证服务器). On first use Zotero offers to create the `zotero` folder; accept.
- Leave "Download files" at **at sync time** (同步时).

### 4. Plugin: person installs, agent verifies

Build with `python3 plugin/build.py`, or use the prebuilt `plugin/webdav-missing-fix.xpi`.
The person installs it on **every** client: Tools → Plugins → gear → Install Plugin From File. No restart is needed.

Why it is needed: Zotero marks an attachment "in sync" when the server does not have the file yet, even though no local copy exists, and then never downloads it. The source lines, timeline and design are in `plugin/README.md`.
The plugin works on Windows, macOS and Linux, Zotero 7–10.

### 5. Verify (agent)

```
python3 tools/diagnose.py --webdav-root "<storage folder>" --log ~/.config/webdav-zotero/webdav.log
```

The check passes when:
- **STUCK** shows 0 attachments;
- every client IP appears under "Last request per client" after it syncs;
- `davuser log` shows `OK` logins.

For an end-to-end test, add a small PDF on client A and sync. The server log should show `KEY.zip: PUT` from A, then `KEY.zip: GET` from B after B syncs.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Sync icon spins briefly and stops; files never download; `diagnose.py` lists STUCK items | Zotero bug: a 404 on `.prop` set the attachment to `in_sync` | Install the plugin, sync once. If items are still STUCK, restart Zotero (the plugin re-queues them on startup). A one-off alternative without the plugin: Settings → Sync → Reset → "Reset File Sync History" (重置文件同步历史) |
| Items appear on B but their files never arrive; the server log has **no requests from A** for hours | A cannot reach the server; typically a guest or campus Wi-Fi blocking UDP, so ZeroTier is down. Metadata still syncs via zotero.org, so A *looks* synced | Port forwarding (connectivity.md §3); otherwise another Wi-Fi or Tailscale (§4). Once A reconnects it uploads by itself. Uploads are never lost; they wait in `to_upload` |
| `.zip` present on the server, B still has no file | STUCK (above) | Plugin |
| Verify Server fails with 401 | Wrong account, or locked after 5 failures | `davuser log`, `davuser unlock all`, `davuser passwd NAME` (the person types) |
| Verify Server fails, `auth.log` says `REFUSED (storage not mounted)` | External drive unplugged or renamed | Mount it; check `config.json` `root` and `volume` |
| Cannot connect at all | Server not running, or ZeroTier not joined or authorized | `launchctl print gui/$(id -u)/<label>` or `systemctl --user status <label>`; `curl -I http://<ZT IP>:8080/` should give 401; `zerotier-cli listnetworks` |
| `zerotier-cli peers` shows RELAY from campus | Client network blocks UDP, or forwarding is missing on one NAT layer | connectivity.md §3a: count the layers; §4 fallbacks |
| Rows in the knowledge-base or check scripts say "no PDF on disk" for new items | Same as the first two rows | Run `diagnose.py` to tell which one |

Reading the server log (`~/.config/webdav-zotero/webdav.log`):
- `KEY.prop: GET` then `KEY.zip: GET` is a download.
- `KEY.prop: GET` then `KEY.zip: PUT` and `KEY.prop: PUT` is an upload.
- A `.prop: GET` with no `.zip: GET` afterwards is the stuck-bug signature.
- `OPTIONS`, `PROPFIND`, `nonexistent.prop`, `zotero-test-file.prop` is someone pressing Verify Server.

## Files

- `server/install_server.sh`: installs the service (launchd or systemd --user), writes `config.json` and `start.sh`.
- `server/auth_proxy.py`: rclone `--auth-proxy`. PBKDF2-SHA256 accounts; lockout after 5 failures per user or 10 per IP within 15 min, lasting 15 min; ignores symlinks; mount guard.
- `server/davuser`: account management; restarts the service so that old logins are dropped.
- `plugin/src/{manifest.json,bootstrap.js}`, `plugin/build.py`, `plugin/webdav-missing-fix.xpi`, `plugin/README.md`.
- `network/connectivity.md`: ZeroTier setup, why sync can look successful while failing, NAT detection, port forwarding (GL.iNet, Spectrum, generic), verification, ZeroTier IP → node ID mapping, UDP-blocked fallbacks.
- `tools/diagnose.py`: read-only diagnosis; works on Windows, macOS and Linux.

## Reference deployment (2026-10-16, where the procedure was proven)

- **Server:** macOS, rclone v1.75.1, launchd. Storage on an external HFS+ drive (`--volume` guard). Port 8080. Bonjour advertisement.
- **Clients:** this Mac plus a laptop. Both run Zotero 10.0.6 and ZeroTier 1.16.2.
- **Network:** two NAT layers: GL.iNet, then the Spectrum router. UDP 9993 is forwarded on both, and ZeroTier's surface shows `<public IP>/9993`.
- **Plugin:** fixed 5 stuck attachments on install. Zotero downloaded them in the next sync.
- **Known limit:** UCLA_WEB (guest network) blocked the laptop's uploads; UCLA_WIFI worked. Whether the forwarding fixes UCLA_WEB has to be checked from that network with `zerotier-cli peers`.
