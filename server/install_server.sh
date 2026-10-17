#!/bin/sh
# Install the Zotero WebDAV server (rclone serve webdav + auth proxy) as a
# login service: launchd on macOS, systemd --user on Linux.
#
#   sh install_server.sh --root "/path/to/zotero storage" [options]
#
# Options
#   --root DIR        folder to serve (required). Zotero creates DIR/zotero/ itself.
#   --volume DIR      mount point that must be mounted before serving (external drive)
#   --port N          listen port (default 8080)
#   --bind ADDR       listen address (default: all interfaces). Use the ZeroTier /
#                     Tailscale IP to keep the plain-HTTP service off the LAN.
#   --label NAME      service label (default com.$USER.webdav-zotero)
#   --bonjour NAME    also advertise the share on the LAN via Bonjour (macOS only)
#   --conf DIR        config dir (default ~/.config/webdav-zotero)
#
# Idempotent: re-running updates scripts and service files but never touches
# users.json (accounts) or the logs. Accounts are created afterwards by a person:
#   ~/.config/webdav-zotero/davuser add NAME
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=""; VOLUME=""; PORT=8080; BIND=""; LABEL="com.$(id -un).webdav-zotero"; BONJOUR=""
CONF="$HOME/.config/webdav-zotero"

while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT=$2; shift 2 ;;
    --volume) VOLUME=$2; shift 2 ;;
    --port) PORT=$2; shift 2 ;;
    --bind) BIND=$2; shift 2 ;;
    --label) LABEL=$2; shift 2 ;;
    --bonjour) BONJOUR=$2; shift 2 ;;
    --conf) CONF=$2; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -n "$ROOT" ] || { echo "--root is required" >&2; exit 2; }
[ -d "$ROOT" ] || { echo "storage folder does not exist: $ROOT" >&2; exit 1; }
RCLONE=$(command -v rclone || true)
[ -n "$RCLONE" ] || { echo "rclone not found. macOS: brew install rclone; Linux: https://rclone.org/install/" >&2; exit 1; }
# Prefer the system interpreter: a conda/pyenv python can move or vanish.
if [ -x /usr/bin/python3 ]; then PY=/usr/bin/python3; else PY=$(command -v python3 || true); fi
[ -n "$PY" ] || { echo "python3 not found" >&2; exit 1; }

mkdir -p "$CONF"
chmod 700 "$CONF"
cp "$HERE/auth_proxy.py" "$HERE/davuser" "$CONF/"
chmod 700 "$CONF/auth_proxy.py" "$CONF/davuser"
# Use an absolute interpreter in the shebang: launchd/systemd have a minimal PATH.
for f in auth_proxy.py davuser; do
  sed "1s|.*|#!$PY|" "$CONF/$f" > "$CONF/$f.tmp" && mv "$CONF/$f.tmp" "$CONF/$f" && chmod 700 "$CONF/$f"
done
[ -f "$CONF/users.json" ] || { echo '{}' > "$CONF/users.json"; chmod 600 "$CONF/users.json"; }

"$PY" - "$CONF/config.json" "$ROOT" "$VOLUME" "$LABEL" <<'EOF'
import json, sys
path, root, volume, label = sys.argv[1:]
cfg = {"root": root, "label": label}
if volume:
    cfg["volume"] = volume
json.dump(cfg, open(path, "w"), indent=1)
EOF

cat > "$CONF/start.sh" <<EOF
#!/bin/sh
# WebDAV server for Zotero file sync (started by the login service).
# Which folder is served and who may log in is decided by auth_proxy.py.
exec "$RCLONE" serve webdav \\
  --addr "$BIND:$PORT" \\
  --auth-proxy "$CONF/auth_proxy.py" \\
  --realm "zotero storage" \\
  --log-level INFO
EOF
chmod 700 "$CONF/start.sh"

case "$(uname -s)" in
Darwin)
  LA="$HOME/Library/LaunchAgents"; mkdir -p "$LA"
  cat > "$LA/$LABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>$LABEL</string>
	<key>ProgramArguments</key>
	<array><string>/bin/sh</string><string>$CONF/start.sh</string></array>
	<key>RunAtLoad</key><true/>
	<key>KeepAlive</key><true/>
	<key>ThrottleInterval</key><integer>30</integer>
	<key>StandardOutPath</key><string>$CONF/webdav.log</string>
	<key>StandardErrorPath</key><string>$CONF/webdav.log</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$LA/$LABEL.plist"
  if [ -n "$BONJOUR" ]; then
    cat > "$LA/$LABEL-bonjour.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>$LABEL-bonjour</string>
	<key>ProgramArguments</key>
	<array><string>/usr/bin/dns-sd</string><string>-R</string><string>$BONJOUR</string>
	<string>_webdav._tcp</string><string>local</string><string>$PORT</string><string>path=/</string></array>
	<key>RunAtLoad</key><true/>
	<key>KeepAlive</key><true/>
</dict>
</plist>
EOF
    launchctl bootout "gui/$(id -u)/$LABEL-bonjour" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$LA/$LABEL-bonjour.plist"
  fi
  ;;
Linux)
  UD="$HOME/.config/systemd/user"; mkdir -p "$UD"
  cat > "$UD/$LABEL.service" <<EOF
[Unit]
Description=WebDAV server for Zotero file sync
After=network-online.target

[Service]
ExecStart=/bin/sh $CONF/start.sh
Restart=always
RestartSec=30
StandardOutput=append:$CONF/webdav.log
StandardError=append:$CONF/webdav.log

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now "$LABEL.service"
  echo "To keep it running while logged out: sudo loginctl enable-linger $(id -un)"
  ;;
*)
  echo "unsupported OS for the service install: $(uname -s)" >&2; exit 1 ;;
esac

sleep 2
code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" || true)
[ -n "$BIND" ] && code=$(curl -s -o /dev/null -w '%{http_code}' "http://$BIND:$PORT/" || true)
echo "server answers HTTP $code (401 = running and asking for a login, which is correct)"
echo "next: create an account (a person types the password): $CONF/davuser add NAME"
