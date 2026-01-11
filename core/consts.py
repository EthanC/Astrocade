"""Enumerations and constant values."""

import re
from enum import IntEnum, StrEnum, auto
from pathlib import Path
from re import Pattern
from typing import Final

from environs import env
from loguru import logger

if env.read_env(Path(__file__).resolve().parent.parent / ".env", recurse=False):
    logger.success("Loaded environment variables")


class Environment(StrEnum):
    """Enumeration of environment variable names."""

    @staticmethod
    def _generate_next_value_(
        name: str, start: int, count: int, last_values: list[str]
    ) -> str:
        """Transform the value to uppercase."""
        return name.upper()

    LOG_LEVEL = auto()
    LOG_DISCORD_WEBHOOK_URL = auto()
    LOG_DISCORD_WEBHOOK_LEVEL = auto()
    DATABASE_PATH = auto()
    DISCORD_BOT_TOKEN = auto()
    DISCORD_SERVER_IDS = auto()
    WORDLE_BOT_ID = auto()
    WORDLE_POINTS_ATTEMPTS_1 = auto()
    WORDLE_POINTS_ATTEMPTS_2 = auto()
    WORDLE_POINTS_ATTEMPTS_3 = auto()
    WORDLE_POINTS_ATTEMPTS_4 = auto()
    WORDLE_POINTS_ATTEMPTS_5 = auto()
    WORDLE_POINTS_ATTEMPTS_6 = auto()
    WORDLE_POINTS_FAIL = auto()


LOG_LEVEL: Final[str | None] = env.str(Environment.LOG_LEVEL, default=None)
IS_DEBUG: Final[bool] = LOG_LEVEL in {"DEBUG", "TRACE"}
LOG_DISCORD_WEBHOOK_URL: Final[str | None] = env.str(
    Environment.LOG_DISCORD_WEBHOOK_URL, default=None
)
LOG_DISCORD_WEBHOOK_LEVEL: Final[str] = env.str(
    Environment.LOG_DISCORD_WEBHOOK_LEVEL, default="WARNING"
)
DATABASE_PATH: Final[Path] = env.path(Environment.DATABASE_PATH, Path("./astrocade.db"))
DISCORD_SERVER_IDS: Final[list[int]] = env.list(
    Environment.DISCORD_SERVER_IDS, [], int, delimiter=","
)
WORDLE_BOT_ID: Final[int] = env.int(Environment.WORDLE_BOT_ID, 1211781489931452447)
DISCORD_BOT_TOKEN: Final[str] = env.str(Environment.DISCORD_BOT_TOKEN)


class Colors(StrEnum):
    """Enumeration of generic colors."""

    BLURPLE = "#5865F2"
    YELLOW = "#D1B036"
    RED = "#DA3E44"
    GREEN = "#6AAA64"
    BLACK = "#151515"


class Direction(StrEnum):
    """Enumeration of sorting directions."""

    @staticmethod
    def _generate_next_value_(
        name: str, start: int, count: int, last_values: list[str]
    ) -> str:
        """Transform the value to titlecase."""
        return name.title()

    ASCENDING = auto()
    DESCENDING = auto()


class WordleLeaderboardType(StrEnum):
    """Enumeration of Wordle leaderboard types."""

    @staticmethod
    def _generate_next_value_(
        name: str, start: int, count: int, last_values: list[str]
    ) -> str:
        """Transform the value to titlecase."""
        return name.title()

    POINTS = auto()
    AVERAGES = auto()
    FAILS = auto()
    ACES = auto()
    COMPLETIONS = auto()


class WordlePoints(IntEnum):
    """Enumeration of Wordle point values."""

    ATTEMPTS_1 = env.int(Environment.WORDLE_POINTS_ATTEMPTS_1, 10)
    ATTEMPTS_2 = env.int(Environment.WORDLE_POINTS_ATTEMPTS_2, 5)
    ATTEMPTS_3 = env.int(Environment.WORDLE_POINTS_ATTEMPTS_3, 4)
    ATTEMPTS_4 = env.int(Environment.WORDLE_POINTS_ATTEMPTS_4, 3)
    ATTEMPTS_5 = env.int(Environment.WORDLE_POINTS_ATTEMPTS_5, 2)
    ATTEMPTS_6 = env.int(Environment.WORDLE_POINTS_ATTEMPTS_6, 1)
    FAIL = env.int(Environment.WORDLE_POINTS_FAIL, -5)


REGEX_WORDLE_STREAK: Final[Pattern[str]] = re.compile(
    r"Your\s+group\s+is\s+on\s+an?\s+(\d+)\s+day\s+streak"
)
REGEX_WORDLE_STREAK_ATTEMPT: Final[Pattern[str]] = re.compile(r"([a-zA-Z0-9]+)/\d+:")
REGEX_WORDLE_STREAK_TAG: Final[Pattern[str]] = re.compile(r"<@(\w+)>|@(\w+)")
REGEX_WORDLE_SHARE: Final[Pattern[str]] = re.compile(r"Wordle\s+(\d+)\s+([0-6X])/6")
WORDLE_ICON: Final[Path] = (
    Path(__file__).resolve().parent.parent / "assets" / "wordle_icon.png"
)
ASTROCADE_ICON: Final[Path] = (
    Path(__file__).resolve().parent.parent / "assets" / "astrocade_icon.png"
)
ASTROCADE_LOGO: Final[Path] = (
    Path(__file__).resolve().parent.parent / "assets" / "astrocade_logo.png"
)
