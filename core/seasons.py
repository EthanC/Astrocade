"""Wordle seasons, derived from calendar dates."""

import re
from dataclasses import dataclass
from datetime import MAXYEAR, MINYEAR, date, datetime, timedelta
from re import Match, Pattern
from typing import Final, Self

from core.consts import TZ, WORDLE_SEASONS_PER_YEAR

SEASON_MONTHS: Final[int] = 12 // WORDLE_SEASONS_PER_YEAR
REGEX_SEASON: Final[Pattern[str]] = re.compile(
    r"\s*(\d{1,4})\s*(?:-|\s+season\s+)(\d{1,2})\s*", re.IGNORECASE
)


def today() -> date:
    """Return the current date in the configured time zone."""
    return datetime.now(TZ).date()


def format_day(day: date) -> str:
    """Format a date as "Oct 1, 2026" (strftime's %-d is not portable)."""
    return f"{day:%b} {day.day}, {day.year}"


@dataclass(frozen=True, slots=True, order=True)
class Season:
    """Represents a Wordle season, the calendar range that player stats accumulate over."""

    year: int
    """Calendar year of the season."""

    number: int
    """Position of the season within its year, starting at 1."""

    def __post_init__(self: Self) -> None:
        """Validate the season position."""
        if not 1 <= self.number <= WORDLE_SEASONS_PER_YEAR:
            raise ValueError(
                f"Season number must be between 1 and {WORDLE_SEASONS_PER_YEAR}, got {self.number}"
            )

    @classmethod
    def for_day(cls, day: date) -> "Season":
        """Return the season containing the given day."""
        return cls(day.year, (day.month - 1) // SEASON_MONTHS + 1)

    @classmethod
    def current(cls) -> "Season":
        """Return the ongoing season."""
        return cls.for_day(today())

    @classmethod
    def parse(cls, value: str) -> "Season | None":
        """Return the season for the given key or label (e.g. "2026-4" or "2026 Season 4"), or None if invalid."""
        match: Match[str] | None = REGEX_SEASON.fullmatch(value)

        if not match or not MINYEAR < (year := int(match.group(1))) < MAXYEAR:
            return None

        try:
            return cls(year, int(match.group(2)))
        except ValueError:
            return None

    @classmethod
    def span(cls, first: date, last: date) -> list["Season"]:
        """Return the seasons covering the given days, newest first."""
        oldest: Season = cls.for_day(first)
        season: Season = cls.for_day(last)
        seasons: list[Season] = []

        while season >= oldest:
            seasons.append(season)
            season = season.previous

        return seasons

    @property
    def previous(self: Self) -> "Season":
        """Return the season immediately before this one."""
        if self.number == 1:
            return Season(self.year - 1, WORDLE_SEASONS_PER_YEAR)

        return Season(self.year, self.number - 1)

    @property
    def next(self: Self) -> "Season":
        """Return the season immediately after this one."""
        if self.number == WORDLE_SEASONS_PER_YEAR:
            return Season(self.year + 1, 1)

        return Season(self.year, self.number + 1)

    @property
    def start(self: Self) -> date:
        """First day of the season."""
        return date(self.year, (self.number - 1) * SEASON_MONTHS + 1, 1)

    @property
    def end(self: Self) -> date:
        """Last day of the season."""
        return self.next.start - timedelta(days=1)

    @property
    def key(self: Self) -> str:
        """Stable identifier of the season (e.g. "2026-4")."""
        return f"{self.year}-{self.number}"

    @property
    def label(self: Self) -> str:
        """Display name of the season (e.g. "2026 Season 4")."""
        return f"{self.year} Season {self.number}"

    @property
    def dates(self: Self) -> str:
        """Display range of the days covered by the season."""
        return f"{format_day(self.start)} – {format_day(self.end)}"

    def contains(self: Self, day: date) -> bool:
        """Return whether the given day falls within the season."""
        return self.start <= day <= self.end

    def days_remaining(self: Self, on: date | None = None) -> int:
        """Return the days left in the season, including the given day (default today)."""
        return max(0, (self.end - (on or today())).days + 1)


def season_label(season: Season | None) -> str:
    """Return the display name of a season. None is All-Time."""
    return season.label if season else "All-Time"


def season_caption(season: Season | None, on: date | None = None) -> str:
    """Describe a season as days remaining (ongoing) or dates covered (otherwise). None is All-Time."""
    if season is None:
        return season_label(season)

    on = on or today()

    if season.contains(on):
        remaining: int = season.days_remaining(on)

        return f"{season.label} · **{remaining:,}** day{'' if remaining == 1 else 's'} remaining"

    return f"{season.label} · {season.dates}"
