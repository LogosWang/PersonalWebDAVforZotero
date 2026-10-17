#!/usr/bin/env python3
"""Diagnose Zotero WebDAV file sync on this machine (macOS, Linux, Windows).

  python3 diagnose.py [--zotero-dir DIR] [--webdav-root DIR] [--log FILE]

--zotero-dir   Zotero data directory (default ~/Zotero; Windows %USERPROFILE%\\Zotero)
--webdav-root  on the server machine: the folder rclone serves (contains zotero/KEY.zip)
--log          on the server machine: the rclone log (e.g. ~/.config/webdav-zotero/webdav.log)

Reads a temporary copy of zotero.sqlite (+ WAL), so it is safe while Zotero is
running and never modifies anything. Reports:
  * attachments per sync state;
  * STUCK attachments: marked in_sync but no local file (the Zotero bug the
    webdav-missing-fix plugin repairs). With --webdav-root it says whether the
    file is on the server (download should work) or not (the uploading client
    never reached the server: a network problem on that side);
  * with --log: when each client IP last talked to the server;
  * ZeroTier status and peer paths, if zerotier-cli is installed.
"""
import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

STATES = {0: "to_upload", 1: "to_download", 2: "in_sync", 3: "force_upload",
          4: "force_download", 5: "in_conflict"}


def default_zotero_dir():
    return os.path.join(os.path.expanduser("~"), "Zotero")


def read_attachments(zdir):
    tmp = tempfile.mkdtemp(prefix="zotdiag-")
    try:
        for name in ("zotero.sqlite", "zotero.sqlite-wal"):
            src = os.path.join(zdir, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(tmp, name))
        con = sqlite3.connect(os.path.join(tmp, "zotero.sqlite"))
        rows = con.execute(
            "SELECT i.key, a.path, a.syncState, "
            "(SELECT v.value FROM itemData d JOIN itemDataValues v USING (valueID) "
            " JOIN fields f USING (fieldID) WHERE d.itemID = a.parentItemID AND f.fieldName = 'title') "
            "FROM itemAttachments a JOIN items i USING (itemID) "
            "WHERE a.linkMode IN (0, 1) AND a.path LIKE 'storage:%' "
            "AND a.itemID NOT IN (SELECT itemID FROM deletedItems) "
            "AND i.libraryID = (SELECT libraryID FROM libraries WHERE type = 'user')"
        ).fetchall()
        con.close()
        return rows
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zotero-dir", default=default_zotero_dir())
    ap.add_argument("--webdav-root")
    ap.add_argument("--log")
    args = ap.parse_args()

    rows = read_attachments(args.zotero_dir)
    counts = {}
    stuck, pending = [], []
    for key, path, state, title in rows:
        counts[state] = counts.get(state, 0) + 1
        exists = os.path.exists(os.path.join(args.zotero_dir, "storage", key, path[len("storage:"):]))
        if not exists and state == 2:
            stuck.append((key, title))
        elif not exists and state in (1, 4):
            pending.append((key, title))

    print("== Zotero attachments (user library, stored files)")
    for s in sorted(counts):
        print("  %-15s %d" % (STATES.get(s, s), counts[s]))

    def where(key):
        if not args.webdav_root:
            return ""
        z = os.path.join(args.webdav_root, "zotero", key + ".zip")
        return "  [on server]" if os.path.exists(z) else "  [NOT on server: uploader never reached it]"

    print("\n== STUCK: in_sync but no local file (%d)" % len(stuck))
    for key, title in stuck:
        print("  %s  %s%s" % (key, (title or "")[:70], where(key)))
    if stuck:
        print("  -> install/enable the webdav-missing-fix plugin, restart Zotero, sync once.")
    print("\n== Waiting for download: no local file, to_download (%d)" % len(pending))
    for key, title in pending:
        print("  %s  %s%s" % (key, (title or "")[:70], where(key)))

    if args.log and os.path.exists(args.log):
        last = {}
        pat = re.compile(r"^(\S+ \S+) INFO\s*: .*? (GET|PUT|PROPFIND|OPTIONS|DELETE|MKCOL|MOVE) from ([0-9a-fA-F.:\[\]]+?):\d+$")
        with open(args.log, errors="replace") as f:
            for line in f:
                m = pat.match(line.strip())
                if m:
                    last[m.group(3)] = (m.group(1), m.group(2))
        print("\n== Last request per client (from the rclone log)")
        for ip, (ts, verb) in sorted(last.items(), key=lambda x: x[1][0]):
            print("  %-18s %s  %s" % (ip, ts, verb))

    zt = shutil.which("zerotier-cli")
    if zt:
        print("\n== ZeroTier")
        print("  " + run([zt, "info"]).strip())
        for line in run([zt, "peers"]).splitlines()[1:]:
            if " LEAF " in line:
                print("  " + line)
        print("  (LEAF + DIRECT = direct UDP path; RELAY = no direct path, expect failures)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
