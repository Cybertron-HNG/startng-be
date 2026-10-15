"""Game covers: the picture on a game's card on the homepage and in the profile.

A cover is either one the owner uploaded (saved in the game's folder as COVER_FILE) or a
cover.png/.jpg/.webp at the top of the published game. An uploaded one wins. Uploads are
never stored as sent: they're opened, checked and saved again as a fresh WebP at card size,
so nothing hidden in the file reaches the bucket.
"""
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

COVER_FILE = "startng-cover.webp"
REPO_COVERS = ["cover.png", "cover.jpg", "cover.jpeg", "cover.webp"]
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 40_000_000  # e.g. 8000 x 5000; bigger is refused before it's decoded
SIZE = (640, 400)  # 16:10, cropped to fill like the cards do


class CoverError(Exception):
    """A problem with the uploaded picture. The message is shown to the owner."""


def make_cover(data: bytes) -> bytes:
    """A card-sized WebP from an uploaded PNG, JPEG or WebP."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "WEBP"]) as img:
                if img.width * img.height > MAX_PIXELS:
                    raise CoverError("That picture is too big, use one under 8000 x 5000")
                img = ImageOps.exif_transpose(img)  # phone photos come in the right way up
                img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info else "RGB")
                img = ImageOps.fit(img, SIZE, Image.Resampling.LANCZOS)
                out = io.BytesIO()
                img.save(out, "WEBP", quality=82)
                return out.getvalue()
    except CoverError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError, SyntaxError) as err:
        raise CoverError("Use a PNG, JPEG or WebP picture") from err


def repo_cover(paths: list[str]) -> str | None:
    """The cover a game ships at its top level, if it has one."""
    found = set(paths)
    return next((p for p in REPO_COVERS if p in found), None)
