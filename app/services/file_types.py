"""Attachment format detection by file signature (FR-04, SPEC-02 A5).

The uploaded name and the browser-supplied Content-Type are both attacker- and
accident-controlled, so neither decides what a file is. The container is
identified from the bytes themselves, and the declared extension only has to
*agree* with what was found.
"""

from __future__ import annotations

import io
import zipfile

from app.core.errors import AttachmentInvalidFormatError
from app.models.enums import AttachmentFormat

# Signatures, longest-first where prefixes overlap.
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_PDF = b"%PDF-"
_GZIP = b"\x1f\x8b"
_RAR4 = b"Rar!\x1a\x07\x00"
_RAR5 = b"Rar!\x1a\x07\x01\x00"
_ZIP_PREFIXES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# OLE2 stream names are stored UTF-16LE in the compound-file directory. Looking
# for them is enough to tell a Word document from an Excel workbook without
# parsing the whole container.
_OLE_WORD_STREAM = "WordDocument".encode("utf-16-le")
_OLE_EXCEL_STREAMS = ("Workbook".encode("utf-16-le"), "Book".encode("utf-16-le"))

CONTENT_TYPES: dict[AttachmentFormat, str] = {
    AttachmentFormat.PNG: "image/png",
    AttachmentFormat.JPEG: "image/jpeg",
    AttachmentFormat.PDF: "application/pdf",
    AttachmentFormat.ZIP: "application/zip",
    AttachmentFormat.GZIP: "application/gzip",
    AttachmentFormat.RAR: "application/vnd.rar",
    AttachmentFormat.DOC: "application/msword",
    AttachmentFormat.DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    AttachmentFormat.XLS: "application/vnd.ms-excel",
    AttachmentFormat.XLSX: ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}

# Extensions the user may write for each detected format. `jpg` and `jpeg` are
# the same container; `gz`/`tgz` likewise.
EXTENSIONS: dict[AttachmentFormat, tuple[str, ...]] = {
    AttachmentFormat.PNG: ("png",),
    AttachmentFormat.JPEG: ("jpeg", "jpg"),
    AttachmentFormat.PDF: ("pdf",),
    AttachmentFormat.ZIP: ("zip",),
    AttachmentFormat.GZIP: ("gz", "gzip", "tgz"),
    AttachmentFormat.RAR: ("rar",),
    AttachmentFormat.DOC: ("doc",),
    AttachmentFormat.DOCX: ("docx",),
    AttachmentFormat.XLS: ("xls",),
    AttachmentFormat.XLSX: ("xlsx",),
}

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    ext for group in EXTENSIONS.values() for ext in group
)


def extension_of(filename: str) -> str:
    _, _, extension = filename.rpartition(".")
    return extension.lower() if extension and extension != filename else ""


def _sniff_zip(content: bytes) -> AttachmentFormat:
    """A .docx and an .xlsx are both zips; the entry names separate them."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        raise AttachmentInvalidFormatError("Архив повреждён и не может быть прочитан") from None
    if "word/document.xml" in names:
        return AttachmentFormat.DOCX
    if "xl/workbook.xml" in names:
        return AttachmentFormat.XLSX
    return AttachmentFormat.ZIP


def _sniff_ole2(content: bytes) -> AttachmentFormat | None:
    if _OLE_WORD_STREAM in content:
        return AttachmentFormat.DOC
    if any(stream in content for stream in _OLE_EXCEL_STREAMS):
        return AttachmentFormat.XLS
    return None


def sniff_format(content: bytes) -> AttachmentFormat | None:
    """Identify the container from its leading bytes; None when unrecognised."""
    if content.startswith(_PNG):
        return AttachmentFormat.PNG
    if content.startswith(_JPEG):
        return AttachmentFormat.JPEG
    if content.startswith(_PDF):
        return AttachmentFormat.PDF
    if content.startswith((_RAR5, _RAR4)):
        return AttachmentFormat.RAR
    if content.startswith(_GZIP):
        return AttachmentFormat.GZIP
    if content.startswith(_ZIP_PREFIXES):
        return _sniff_zip(content)
    if content.startswith(_OLE2):
        return _sniff_ole2(content)
    return None


def detect_attachment_format(content: bytes, filename: str) -> tuple[AttachmentFormat, str]:
    """Return `(format, content_type)` or raise with a message a user can act on."""
    if not content:
        raise AttachmentInvalidFormatError("Файл пуст", details={"filename": filename})

    extension = extension_of(filename)
    detected = sniff_format(content)

    if detected is None:
        # An OLE2 container whose streams we could not read still deserves a
        # precise message rather than "unknown".
        if content.startswith(_OLE2):
            raise AttachmentInvalidFormatError(
                "Не удалось определить тип файла Microsoft Office",
                details={"filename": filename},
            )
        raise AttachmentInvalidFormatError(
            "Формат файла не поддерживается. Допустимы: " + ", ".join(sorted(ALLOWED_EXTENSIONS)),
            details={"filename": filename, "allowed": sorted(ALLOWED_EXTENSIONS)},
        )

    if extension and extension not in EXTENSIONS[detected]:
        raise AttachmentInvalidFormatError(
            f"Содержимое файла — «{detected.value}», а расширение — «{extension}». "
            "Переименуйте файл или загрузите другой.",
            details={
                "filename": filename,
                "declared_extension": extension,
                "detected_format": detected.value,
            },
        )

    return detected, CONTENT_TYPES[detected]
