/*
 * WebDAV Missing-File Fix
 *
 * Zotero's WebDAV downloadFile() marks an attachment "in_sync" when the server has no
 * .prop for it ("Remote file not found"), even if no local file exists
 * (xpcom/storage/webdav.js, downloadFile). If another client uploads the file a moment
 * later, this client never retries: the attachment is no longer TO_DOWNLOAD, so
 * checkForPendingDownloads() is false and the storage engine skips downloads whenever
 * storageVersion == libraryVersion.
 *
 * This plugin:
 *  1. wraps downloadFile(): if the file was missing locally before and after the call
 *     and Zotero just marked it in_sync, put it back to TO_DOWNLOAD so every sync retries;
 *  2. on startup, re-queues attachments already stuck as in_sync with no local file.
 */

var WMF;

function log(msg) {
  Zotero.debug("[webdav-missing-fix] " + msg);
}

function install() {}
function uninstall() {}

async function repairStuck() {
  const Local = Zotero.Sync.Storage.Local;
  const libraryID = Zotero.Libraries.userLibraryID;
  if (Local.getModeForLibrary(libraryID) != 'webdav') return 0;
  const ids = await Zotero.DB.columnQueryAsync(
    "SELECT itemID FROM itemAttachments JOIN items USING (itemID) "
    + "WHERE libraryID=? AND syncState=? AND linkMode IN (?,?) "
    + "AND path LIKE 'storage:%' AND path NOT LIKE 'storage:.zotero%' "
    + "AND itemID NOT IN (SELECT itemID FROM deletedItems)",
    [libraryID, Local.SYNC_STATE_IN_SYNC,
      Zotero.Attachments.LINK_MODE_IMPORTED_FILE, Zotero.Attachments.LINK_MODE_IMPORTED_URL]
  );
  const items = await Zotero.Items.getAsync(ids);
  const stuck = [];
  for (const item of items) {
    if (!(await item.fileExists())) stuck.push(item);
  }
  if (stuck.length) {
    await Zotero.DB.executeTransaction(async () => {
      await Local.updateSyncStates(stuck, Local.SYNC_STATE_TO_DOWNLOAD);
    });
    log(`re-queued ${stuck.length} in_sync attachment(s) with no local file: `
      + stuck.map(i => i.key).join(', '));
  }
  return stuck.length;
}

async function startup({ id, version, rootURI }, reason) {
  await Zotero.initializationPromise;
  const Local = Zotero.Sync.Storage.Local;
  const proto = Zotero.Sync.Storage.Mode.WebDAV.prototype;
  const orig = proto.downloadFile;

  proto.downloadFile = async function (request) {
    let item = null;
    let hadFile = true;
    try {
      item = Zotero.Sync.Storage.Utilities.getItemFromRequest(request);
      if (item) hadFile = await item.fileExists();
    }
    catch (e) {
      item = null;
    }
    const result = await orig.apply(this, arguments);
    try {
      if (item && !hadFile && !(await item.fileExists())
          && item.attachmentSyncState == Local.SYNC_STATE_IN_SYNC) {
        item.attachmentSyncState = Local.SYNC_STATE_TO_DOWNLOAD;
        await item.saveTx({ skipAll: true });
        log(`${item.libraryKey}: not on WebDAV server yet and no local file `
          + `-- kept marked for download (Zotero would have set in_sync)`);
      }
    }
    catch (e) {
      Zotero.logError(e);
    }
    return result;
  };

  WMF = { proto, orig, repairStuck };
  Zotero.WebDAVMissingFix = WMF;
  log(`v${version} active`);
  try {
    await repairStuck();
  }
  catch (e) {
    Zotero.logError(e);
  }
}

function shutdown(data, reason) {
  if (WMF) {
    WMF.proto.downloadFile = WMF.orig;
    delete Zotero.WebDAVMissingFix;
    WMF = null;
  }
}
