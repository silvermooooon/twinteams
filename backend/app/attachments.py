"""Immutable chat uploads. Only verified image bytes may cross the model boundary."""
import hashlib
import warnings
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS = 4
MAX_IMAGE_PIXELS = 25_000_000
IMAGE_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def inspect_upload(name: str, declared_type: str, data: bytes) -> dict:
    if not data or len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, "Attachments must contain 1 byte to 20 MiB")
    if not name.strip() or len(name) > 200 or Path(name).name != name or name in {".", ".."} or "\\" in name or any(ord(char) < 32 for char in name):
        raise HTTPException(400, "Attachment name must be a simple file name")
    declared_type = declared_type.partition(";")[0].strip().lower()
    looks_supported = (declared_type in IMAGE_FORMATS.values() or Path(name).suffix.lower() in IMAGE_SUFFIXES
                       or data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a"))
                       or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))
    result = {"kind": "unsupported", "mime_type": "application/octet-stream", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "width": None, "height": None}
    if not looks_supported:
        return result  # Do not parse arbitrary documents, archives, SVG, or their filenames.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in IMAGE_FORMATS or image.width * image.height > MAX_IMAGE_PIXELS or getattr(image, "is_animated", False):
                    raise ValueError("Only static JPEG, PNG, WEBP and GIF images up to 25 megapixels are supported")
                result.update(kind="image", mime_type=IMAGE_FORMATS[image.format], width=image.width, height=image.height)
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()  # Verify the pixel data too; a valid header is insufficient.
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise HTTPException(415, "The attachment is not a valid supported static image") from error
    return result
