"""Game covers: the picture on a game's card on the homepage and in the profile.

A cover is either one the owner uploaded (saved in the game's folder as COVER_FILE) or a
cover.png/.jpg/.webp at the top of the published game, served from its card-sized copy CARD_FILE. An uploaded one
wins. Link previews use a 1200 x 630 JPEG made from the cover (GET /games/{name}/share.jpg). Uploads are
never stored as sent: they're opened, checked and saved again as a fresh WebP at card size,
so nothing hidden in the file reaches the bucket.
"""
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

COVER_FILE = "startng-cover.webp"
# Ziki #74: a game's own cover.png is often 1-2 MB. At publish it gets a card-sized copy (the same 640 x 400 WebP an upload
# gets), which cards use; the original stays as it is and is the fallback if the copy can't be made.
CARD_FILE = "startng-card.webp"
OURS = {COVER_FILE, CARD_FILE}  # files start.ng writes into a game's folder: a repo file can never replace them
REPO_COVERS = ["cover.png", "cover.jpg", "cover.jpeg", "cover.webp"]
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 40_000_000  # e.g. 8000 x 5000; bigger is refused before it's decoded
SIZE = (640, 400)  # 16:10, cropped to fill like the cards do
SHARE_SIZE = (1200, 630)  # link previews (og:image): the size WhatsApp, X and others expect


class CoverError(Exception):
    """A problem with the uploaded picture. The message is shown to the owner."""


def make_cover(data: bytes) -> bytes:
    """A card-sized WebP from an uploaded PNG, JPEG or WebP."""
    return _make(data, SIZE, "WEBP", quality=82)


def make_share(data: bytes) -> bytes:
    """A 1200 x 630 JPEG for link previews: small enough for WhatsApp (well under 300 KB). Transparency goes on white."""
    return _make(data, SHARE_SIZE, "JPEG", quality=80, optimize=True, progressive=True)


def _make(data: bytes, size: tuple[int, int], fmt: str, **save) -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "WEBP"]) as img:
                if img.width * img.height > MAX_PIXELS:
                    raise CoverError("That picture is too big, use one under 8000 x 5000")
                img = ImageOps.exif_transpose(img)  # phone photos come in the right way up
                img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info else "RGB")
                if fmt == "JPEG" and img.mode == "RGBA":  # JPEG has no transparency
                    img = Image.alpha_composite(Image.new("RGBA", img.size, "white"), img).convert("RGB")
                img = ImageOps.fit(img, size, Image.Resampling.LANCZOS)
                out = io.BytesIO()
                img.save(out, fmt, **save)
                return out.getvalue()
    except CoverError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError, SyntaxError) as err:
        raise CoverError("Use a PNG, JPEG or WebP picture") from err


def repo_cover(paths: list[str]) -> str | None:
    """The cover a game ships at its top level, if it has one."""
    found = set(paths)
    return next((p for p in REPO_COVERS if p in found), None)
