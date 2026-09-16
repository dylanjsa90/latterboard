"""Profile photo processing.

Every upload is decoded and re-encoded here, so nothing a client sends reaches other
players as-is: EXIF (including phone GPS) is dropped, and non-images are rejected.
"""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

AVATAR_SIZE = 256
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_CONTENT_TYPE = "image/webp"
# Checked against the header before decoding, so a small file can't expand into a
# huge bitmap.
_MAX_PIXELS = 40_000_000
_FORMATS = {"JPEG", "PNG", "WEBP"}
_NOT_AN_IMAGE = "Upload a JPEG, PNG, or WebP image."


class InvalidImage(ValueError):
    pass


def process_avatar(data: bytes) -> bytes:
    """A square 256px WebP of `data`, center-cropped; raises `InvalidImage`."""
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in _FORMATS:
                raise InvalidImage(_NOT_AN_IMAGE)
            if image.width * image.height > _MAX_PIXELS:
                raise InvalidImage("That photo is too large. Try a smaller one.")
            upright = ImageOps.exif_transpose(image) or image
            mode = "RGBA" if "A" in upright.getbands() else "RGB"
            square = ImageOps.fit(
                upright.convert(mode),
                (AVATAR_SIZE, AVATAR_SIZE),
                Image.Resampling.LANCZOS,
            )
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise InvalidImage(_NOT_AN_IMAGE) from exc

    out = BytesIO()
    square.save(out, "WEBP", quality=85)
    return out.getvalue()
