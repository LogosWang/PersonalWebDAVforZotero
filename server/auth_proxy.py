#!/usr/bin/env python3
"""rclone --auth-proxy for a Zotero WebDAV share.

rclone runs this once per new login and passes {"user", "pass", "client_ip"}
on stdin. On success it prints a backend config rooted at the storage folder;
on failure it exits non-zero and rclone answers 401.

Settings come from config.json next to this file:
  root    folder that is served (Zotero creates a "zotero" subfolder in it)
  volume  optional mount point that must be mounted before anything is served
          (protects against serving an empty folder when an external drive is
          unplugged: Zotero would then think every file is gone)
Accounts live in users.json (managed by `davuser`). Repeated failures lock the
account and the client IP for a while.
"""
import fcntl
import hashlib
import hmac
import json
import os
import sys
import time

CONF = os.path.dirname(os.path.abspath(__file__))
USERS = os.path.join(CONF, "users.json")
STATE = os.path.join(CONF, "authfail.json")
LOG = os.path.join(CONF, "auth.log")
SETTINGS = os.path.join(CONF, "config.json")

USER_MAX_FAILS = 5        # per account
IP_MAX_FAILS = 10         # per client IP
WINDOW = 15 * 60          # seconds failures are counted over
LOCK_TIME = 15 * 60       # seconds a lock lasts

DUMMY = {"salt": "00" * 16, "iter": 200000, "hash": "00" * 32}


def log(ip, user, result):
    with open(LOG, "a") as f:
        f.write("%s  %-15s  %-20s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), ip, user, result))


def check_password(rec, password):
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(rec["salt"]), rec["iter"])
    return hmac.compare_digest(digest.hex(), rec["hash"])


def fail(ip, user, reason):
    log(ip, user, reason)
    sys.stderr.write("auth failed: %s\n" % reason)
    sys.exit(1)


def main():
    req = json.load(sys.stdin)
    user = str(req.get("user", ""))[:64]
    password = str(req.get("pass", ""))
    ip = str(req.get("client_ip", "?"))
    now = time.time()

    with open(SETTINGS) as f:
        settings = json.load(f)
    root = settings["root"]
    volume = settings.get("volume")

    # Never hand out a backend unless the storage is really there.
    if (volume and not os.path.ismount(volume)) or not os.path.isdir(root):
        fail(ip, user, "REFUSED (storage not mounted)")

    with open(STATE + ".lock", "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            with open(STATE) as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = {}
        for key in list(state):
            entry = state[key]
            entry["fails"] = [t for t in entry.get("fails", []) if now - t < WINDOW]
            if not entry["fails"] and entry.get("locked_until", 0) < now:
                del state[key]

        ukey, ikey = "user:" + user, "ip:" + ip
        for key in (ukey, ikey):
            if state.get(key, {}).get("locked_until", 0) > now:
                fail(ip, user, "LOCKED (%s)" % key)

        try:
            with open(USERS) as f:
                users = json.load(f)
        except (OSError, ValueError):
            users = {}
        rec = users.get(user)
        ok = check_password(rec or DUMMY, password) and rec is not None

        if not ok:
            for key, limit in ((ukey, USER_MAX_FAILS), (ikey, IP_MAX_FAILS)):
                entry = state.setdefault(key, {"fails": []})
                entry["fails"].append(now)
                if len(entry["fails"]) >= limit:
                    entry["locked_until"] = now + LOCK_TIME
                    entry["fails"] = []
            with open(STATE, "w") as f:
                json.dump(state, f)
            fail(ip, user, "FAIL")

        state.pop(ukey, None)
        state.pop(ikey, None)
        with open(STATE, "w") as f:
            json.dump(state, f)

    log(ip, user, "OK")
    # skip_links: symlinks inside the share are ignored, so nothing outside
    # the storage folder can be reached through one.
    json.dump({"type": "local", "_root": root, "skip_links": "true"}, sys.stdout)


if __name__ == "__main__":
    main()
