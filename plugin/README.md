# webdav-missing-fix: a Zotero plugin

It fixes a Zotero WebDAV bug that can leave attachment files undownloaded indefinitely.

## The bug (Zotero 10.0.6; same code in Zotero 7–10)

`chrome/content/zotero/xpcom/storage/webdav.js`, `downloadFile()`:

```js
var metadata = await this._getStorageFileMetadata(item, request);   // GET KEY.prop
...
if (!metadata) {
  Zotero.debug("Remote file not found for item " + item.libraryKey);
  item.attachmentSyncState = "in_sync";                               // <-- here
  await item.saveTx({ skipAll: true });
  return new Zotero.Sync.Storage.Result();
}
```

Here is how it goes wrong:

1. Client A adds a PDF.
2. The item data reaches the Zotero server and then client B.
3. Client B marks the attachment *to_download* and runs a file sync.
4. Client A hasn't uploaded `KEY.zip` and `KEY.prop` to WebDAV yet, for one of two reasons: either it is a second slower, or it sits on a network where WebDAV is unreachable.
5. Client B's GET on `KEY.prop` returns 404, and Zotero marks the attachment **in_sync** even though B has no local file.

After step 5, every later sync on B skips the file, because:

- `storageEngine.js`: `if (storageVersion == libraryVersion && !checkForPendingDownloads()) → "No remote storage changes -- skipping file downloads"`. The sync icon spins briefly and stops.
- `storageLocal.js` `getFilesToDownload()` only selects `to_download`/`force_download` items.

The file stays missing until A changes the attachment again, if A ever does. Zotero's own source acknowledges this case with a comment in `storageEngine.js` (`// TODO: If files are persistently missing, don't try to download them each time`).

The bug was observed in practice:

- 2026-10-14: B read the `.prop` files at 13:46:31 and A uploaded them at 13:46:31–33. Four files stayed stuck for two days.
- 2026-10-16: A was on a guest Wi-Fi with UDP blocked, so ZeroTier was down. Twelve files were stuck for seven hours, and one stayed stuck after A uploaded.

## What the plugin does

1. **Wraps `Zotero.Sync.Storage.Mode.WebDAV.prototype.downloadFile`.** If the file was missing locally both before and after the call, and Zotero has just set the state to `in_sync`, the plugin puts it back to `to_download`. Every later sync then rechecks it with one `GET KEY.prop` until the file appears.
2. **On startup, re-queues attachments that are already stuck.** These are stored-file attachments in the user library that are `in_sync`, have no local file and are not in the trash.
3. **Shutting down (disabling or uninstalling) restores the original function.**

The plugin only touches the WebDAV mode of the personal library. Groups and Zotero File Storage go through `zfs.js` and are not affected. The plugin never deletes anything, and it changes only the `syncState` column through Zotero's own APIs.

Cost: an attachment whose file is genuinely absent from the server is rechecked once per sync. That is one small HTTP request.

Debug lines are prefixed `[webdav-missing-fix]`; turn on Help → Debug Output Logging to see them.

## Compatibility

| | |
|---|---|
| Zotero | 7, 8, 9, 10 (`strict_min_version 6.999`, `strict_max_version 10.*`). Zotero 6 uses a different plugin system and is not supported. When Zotero 11 ships, check that `downloadFile` still has the same 404 → `in_sync` branch, then raise `strict_max_version`. |
| OS | **Windows, macOS and Linux.** The code is plain JavaScript on Zotero's internal API, with no file paths, shell calls or native code, so the same `.xpi` works everywhere. Tested live on macOS (Zotero 10.0.6); the Windows code path is identical, because Zotero ships the same `omni.ja` JavaScript on all platforms. |
| Server | Any WebDAV server: rclone, nginx, Nextcloud, Synology, Koofr, and so on. |

## Build and install

```
python3 build.py            # → webdav-missing-fix.xpi (a prebuilt copy is included)
```

To install, open Zotero → Tools → Plugins (工具 → 插件). Click the gear icon, choose "Install Plugin From File…" (从文件安装附加组件…) and pick the `.xpi`. It takes effect immediately, with no restart. **Install it on every computer that syncs the library.**

## Verify

1. Run `python3 ../tools/diagnose.py` and check that the **STUCK** count is 0.
2. If files were stuck, sync once and check the WebDAV server log for `KEY.zip: GET` lines from this client.

In the 2026-10-16 deployment, five stuck files were downloaded by Zotero itself within one sync of installing the plugin.
