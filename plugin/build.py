#!/usr/bin/env python3
"""Package src/ into webdav-missing-fix.xpi (works on macOS, Linux and Windows)."""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
OUT = os.path.join(HERE, "webdav-missing-fix.xpi")

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for name in ("manifest.json", "bootstrap.js"):
        z.write(os.path.join(SRC, name), name)
print("wrote", OUT)
