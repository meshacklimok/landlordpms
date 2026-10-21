"""Checks on the inside of uploaded PDFs and zip-based files (Office, spreadsheets).

The callers already check that a file really is what its name says; these look for what a harmless
file does not hold: scripts and launch actions in a PDF, macros, programs and web pages in a zip, and
zip bombs. Photos need no check here: they are decoded and saved again as JPEG (inspections.photos).
"""

import posixpath
import re
import zipfile

MAX_UNPACKED_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_RATIO = 200

# Parts of a PDF that run code, open other things or send data out.
PDF_ACTIVE = re.compile(
    rb"/(JavaScript|JS|Launch|EmbeddedFiles?|RichMedia|XFA|OpenAction|AA|GoToE|GoToR|SubmitForm|ImportData)(?![A-Za-z])")
_NAME_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")

# Entries no document or spreadsheet has: macros, programs, scripts and web pages.
BLOCKED_SUFFIXES = {
    ".exe", ".dll", ".com", ".scr", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".hta",
    ".msi", ".jar", ".sh", ".php", ".py", ".html", ".htm", ".svg", ".swf", ".lnk",
}
BLOCKED_NAMES = ("vbaproject.bin", "vbadata.xml", "macrosheets/")
BLOCKED_FOLDERS = ("activex/",)


def pdf_is_active(data: bytes) -> bool:
    """True when the PDF holds JavaScript, a launch action, an embedded file or similar. Names written with
    #-escapes (/J#61vaScript) are unescaped first, as readers do."""
    plain = _NAME_ESCAPE.sub(lambda m: bytes([int(m.group(1), 16)]), data)
    return PDF_ACTIVE.search(plain) is not None


def zip_is_unsafe(upload) -> bool:
    """True when a zip-based file is too big once unpacked, has a path that climbs out of its folder, or holds
    a macro, a program, a script or a web page. The upload is rewound afterwards."""
    try:
        with zipfile.ZipFile(upload) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                return True
            unpacked = sum(e.file_size for e in entries)
            packed = sum(e.compress_size for e in entries) or 1
            if unpacked > MAX_UNPACKED_BYTES or (unpacked > 5 * 1024 * 1024 and unpacked / packed > MAX_RATIO):
                return True
            for entry in entries:
                name = entry.filename.replace("\\", "/").lower()
                if name.startswith("/") or ".." in name.split("/"):
                    return True
                if (posixpath.splitext(name)[1] in BLOCKED_SUFFIXES or name.endswith(BLOCKED_NAMES)
                        or name.startswith(BLOCKED_FOLDERS) or "/activex/" in name or "/macrosheets/" in name):
                    return True
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        return True
    finally:
        upload.seek(0)
    return False
