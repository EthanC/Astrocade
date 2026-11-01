"""Astrocade database models."""

from datetime import date
from typing import ClassVar, Self, cast

from sqlalchemy import ColumnElement, Integer, ScalarSelect, UniqueConstraint
from sqlalchemy.ext.hybrid import hybrid_property
from sqlmodel import Field, Relationship, SQLModel, func, select


class WordleResult(SQLModel, table=True):
    """Represents a single Wordle game result for a player."""

    __tablename__ = "wordle_results"

    id: str = Field(primary_key=True)
    """ID of the result ({player_id}_{puzzle_id})."""

    attempts: int = Field()
    """Attempts taken to solve the puzzle."""

    player_id: int = Field(foreign_key="players.id", index=True)
    """Discord User ID of the player who owns the result."""

    player: "Player" = Relationship(back_populates="wordle_results")
    """Astrocade player who owns the result."""

    puzzle_id: int = Field(foreign_key="wordle_puzzles.id", index=True)
    """ID of the Wordle puzzle associated with the result."""

    puzzle: "WordlePuzzle" = Relationship(
        back_populates="results", sa_relationship_kwargs={"lazy": "selectin"}
    )
    """Wordle puzzle associated with the result."""

    @property
    def puzzle_day(self: Self) -> date:
        """Day of the Wordle puzzle."""
        return self.puzzle.day

    @property
    def puzzle_solution(self: Self) -> str:
        """Solution word of the Wordle puzzle."""
        return self.puzzle.solution


class WordleStreak(SQLModel, table=True):
    """Represents a processed daily Wordle streak (results summary) message."""

    __tablename__ = "wordle_streaks"
    __table_args__ = (UniqueConstraint("channel_id", "puzzle_id"),)

    id: int = Field(primary_key=True)
    """Discord Message ID of the streak message (time-ordered Snowflake)."""

    channel_id: int = Field(index=True)
    """Discord Channel ID the streak message was posted in (one group per channel)."""

    streak: int = Field()
    """Group streak length stated in the message."""

    puzzle_id: int = Field(foreign_key="wordle_puzzles.id", index=True)
    """ID of the Wordle puzzle the streak message was attributed to."""


class WordlePuzzle(SQLModel, table=True):
    """Represents a daily Wordle puzzle."""

    __tablename__ = "wordle_puzzles"

    id: int = Field(primary_key=True)
    """ID of the Wordle puzzle."""

    day: date = Field(unique=True)
    """Date of the Wordle puzzle."""

    solution: str = Field()
    """Solution word of the Wordle puzzle."""

    results: list["WordleResult"] = Relationship(
        back_populates="puzzle", sa_relationship_kwargs={"lazy": "selectin"}
    )
    """Wordle game results associated with the puzzle."""

    average_attempts: ClassVar[int]
    """Average attempts across all non-failed results for this Wordle puzzle."""

    @hybrid_property
    def average_attempts(self: Self) -> int:
        """Return average attempts across all non-failed results for this Wordle puzzle."""
        attempts: list[int] = [
            result.attempts for result in self.results if result.attempts != 7
        ]

        if not attempts:
            return 0

        # int(x + 0.5) rounds half away from zero to match SQLite's ROUND()
        return int(sum(attempts) / len(attempts) + 0.5)

    @average_attempts.expression
    def average_attempts(cls: Self) -> ScalarSelect[int]:
        """Compute average attempts across all non-failed results for this Wordle puzzle."""
        return (
            select(
                func.coalesce(func.round(func.avg(WordleResult.attempts)), 0).cast(
                    Integer
                )
            )
            .where(WordleResult.puzzle_id == cls.id)
            .where(WordleResult.attempts != 7)
            .correlate_except(WordleResult)
            .scalar_subquery()
        )

    fails: ClassVar[int]
    """Total count of fails for this Wordle puzzle."""

    @hybrid_property
    def fails(self: Self) -> int:
        """Return the total count of fails for this Wordle puzzle."""
        return sum(1 for result in self.results if result.attempts == 7)

    @fails.expression
    def fails(cls: Self) -> ScalarSelect[int]:
        """Compute the total count of fails for this Wordle puzzle."""
        return (
            select(
                func.coalesce(func.count(cast(ColumnElement[int], WordleResult.id)), 0)
            )
            .where(WordleResult.puzzle_id == cls.id)
            .where(WordleResult.attempts == 7)
            .correlate_except(WordleResult)
            .scalar_subquery()
        )

    aces: ClassVar[int]
    """Total count of aces for this Wordle puzzle."""

    @hybrid_property
    def aces(self: Self) -> int:
        """Return the total count of aces for this Wordle puzzle."""
        return sum(1 for result in self.results if result.attempts == 1)

    @aces.expression
    def aces(cls: Self) -> ScalarSelect[int]:
        """Compute the total count of aces for this Wordle puzzle."""
        return (
            select(
                func.coalesce(func.count(cast(ColumnElement[int], WordleResult.id)), 0)
            )
            .where(WordleResult.puzzle_id == cls.id)
            .where(WordleResult.attempts == 1)
            .correlate_except(WordleResult)
            .scalar_subquery()
        )


class Player(SQLModel, table=True):
    """Represents an Astrocade player."""

    __tablename__ = "players"

    id: int = Field(primary_key=True)
    """Discord User ID of the player."""

    wordle_results: list["WordleResult"] = Relationship(
        back_populates="player",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )
    """Wordle game results associated with the player."""
