"""Lightweight text recognition for Discord Activity images."""

from io import BytesIO
from typing import Final

from loguru import logger
from PIL import Image

WORDLE_DIGITS: Final[dict[str, tuple[int, str]]] = {
    "0": (
        7,
        "1563dbf7db60154bfc7e2b83fc43b5ba151515c1aee3831515158adef67115151579f2e5821515158ae1c1b1151515b8bd4ff247154cf44c1563dbf7da5d15",
    ),
    "1": (
        5,
        "1558dc8a15b4cfeb8a153215d38a151515d38a151515d38a151515d38a151515d38a151515d38a15d3ffffffff",
    ),
    "2": (
        7,
        "156fd3f7d36e1582ef703285fc639a71151515cca01515151516f17c1515151587ed2f1515157dea4115151fa6ce39151536d38715151515deffffffffffcd",
    ),
    "3": (
        7,
        "158ad3f7cf631586f77b348dfd4c635c151515e7801515153093e7321515fcfffc70151515152c79f47a7429151515b4bbc8d8632d7ced7b25a5dcf6cd7215",
    ),
    "4": (
        7,
        "15151524e1b915151515b1ecb915151574d091b9151542e83490b9151fdc5f1590b915a995151590b915ffffffffffffff1515151594b9151515151594b915",
    ),
    "5": (
        6,
        "ffffffffff85eb5e5e5e5e32ce1515151515d0bef4e07e15dc61286be8701515151594d0471515159bd0ec701d4bf17494d5f7d15d15",
    ),
    "6": (
        7,
        "1536c7f4dc9a1a21e89b3541d29e8ccb1515152f1dc0a8b5f2e6a526d7e1792b62dda6ca901515157ceba6c615151586dc42ff8f2639e67b1563d5f7de7915",
    ),
    "7": (
        7,
        "d9fffffffffff4565e5e5e5ebe8d1515151558dc1a15151522e34c151515159ab01515151536fa421515151591d31515151515d796151515152fff69151515",
    ),
    "8": (
        7,
        "1584daf7d5771583e44f2251e969a8cf151515c59f4decb65673ee481893eff5ff8615a7c92b2072f29bf16a1515158ce8d3b125152ad2b52b9bdcf8da9622",
    ),
    "9": (
        7,
        "157adef7d35f157fe0612d93fd48e280151515cdaaf47015151598c7c3d6302870e9cf2ca8e9e9bab0b61f2c151515d382a6cf401587e41f1c9edef3c53215",
    ),
}
"""
Reference glyphs (width, hex-encoded 8-bit grayscale pixels, 9px tall) for each digit of the
"Wordle No. {id}" header in the Wordle daily results image, extracted from real images.
"""

WORDLE_DIGIT_HEIGHT: Final[int] = 9
TEXT_LUMINANCE: Final[int] = 160  # Pixels brighter than this are considered text
COMPARE_SIZE: Final[tuple[int, int]] = (8, 12)  # Grid that glyphs are scaled to
MAX_DISTANCE: Final[float] = 0.05  # Identical renders measure 0.00
MIN_MARGIN: Final[float] = 0.05  # Closest pair of different digits (3, 8) measures 0.12
MAX_DIMENSION: Final[int] = 4096


def _scale(glyph: Image.Image) -> bytes:
    """Scale a grayscale glyph to the comparison grid."""
    return glyph.resize(COMPARE_SIZE, Image.Resampling.BILINEAR).tobytes()


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    """Return the (start, stop) ranges of consecutive truthy flags."""
    ranges: list[tuple[int, int]] = []
    start: int | None = None

    for i, flag in enumerate([*flags, False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            ranges.append((start, i))
            start = None

    return ranges


_REFERENCES: Final[dict[str, bytes]] = {
    digit: _scale(
        Image.frombytes("L", (width, WORDLE_DIGIT_HEIGHT), bytes.fromhex(pixels))
    )
    for digit, (width, pixels) in WORDLE_DIGITS.items()
}


class OCR:
    """Recognize fixed-font text in images by glyph comparison, without an OCR engine."""

    @staticmethod
    def read_wordle_number(data: bytes) -> int | None:
        """Read the puzzle number from the "Wordle No. {id}" header of a Wordle results image."""
        try:
            with Image.open(BytesIO(data)) as image:
                if max(image.size) > MAX_DIMENSION:
                    logger.debug(f"Skipped image of size {image.size}, too large")

                    return

                gray: Image.Image = image.convert("L")
        except Exception as e:
            logger.debug(f"Failed to decode image, {e}")

            return

        width, height = gray.size
        pixels: bytes = gray.tobytes()

        # Header is the first line of text from the top of the image
        lines: list[tuple[int, int]] = _runs(
            [
                max(pixels[y * width : (y + 1) * width]) > TEXT_LUMINANCE
                for y in range(height // 3)
            ]
        )

        if not lines:
            logger.debug("Failed to locate header text in image")

            return

        top, bottom = lines[0]
        band: bytes = gray.crop((0, top, width, bottom)).tobytes()
        glyphs: list[tuple[int, int]] = _runs(
            [max(band[x::width]) > TEXT_LUMINANCE for x in range(width)]
        )

        if not glyphs:
            logger.debug("Failed to locate glyphs in header text")

            return

        # Split words at gaps wider than letter spacing; the puzzle number is the last word
        words: list[list[tuple[int, int]]] = [[glyphs[0]]]

        for previous, current in zip(glyphs, glyphs[1:]):
            if current[0] - previous[1] >= (bottom - top) * 0.4:
                words.append([])

            words[-1].append(current)

        if len(words) < 2 or not 1 <= len(words[-1]) <= 6:
            logger.debug(f"Unexpected header layout in image, {len(words)} words")
            logger.trace(f"{words=}")

            return

        number: str = ""

        for left, right in words[-1]:
            scaled: bytes = _scale(gray.crop((left, top, right, bottom)))
            distances: list[tuple[float, str]] = sorted(
                (
                    sum(abs(a - b) for a, b in zip(scaled, reference))
                    / (255 * len(reference)),
                    digit,
                )
                for digit, reference in _REFERENCES.items()
            )
            (best, digit), (runner_up, _) = distances[0], distances[1]

            if best > MAX_DISTANCE or runner_up - best < MIN_MARGIN:
                logger.debug(
                    f"Failed to confidently recognize glyph at x={left}-{right} (best {digit} at {best:.3f}, runner-up at {runner_up:.3f})"
                )

                return

            number += digit

        logger.debug(f"Read Wordle puzzle number {number} from image")

        return int(number)
