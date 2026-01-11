"""Astrocade database models."""

from datetime import date
from typing import Any, ClassVar, Self

from sqlalchemy import Case, ScalarSelect, case
from sqlalchemy.ext.hybrid import hybrid_property
from sqlmodel import Field, Relationship, SQLModel, func, select

from core.consts import WordlePoints


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

    wordle_points: ClassVar[int]
    """Total Wordle points accumulated by the player."""

    @hybrid_property
    def wordle_points(self: Self) -> int:
        """Return total Wordle points accumulated by the player."""
        points: int = 0

        for result in self.wordle_results:
            match result.attempts:
                case 1:
                    points += WordlePoints.ATTEMPTS_1
                case 2:
                    points += WordlePoints.ATTEMPTS_2
                case 3:
                    points += WordlePoints.ATTEMPTS_3
                case 4:
                    points += WordlePoints.ATTEMPTS_4
                case 5:
                    points += WordlePoints.ATTEMPTS_5
                case 6:
                    points += WordlePoints.ATTEMPTS_6
                case _:  # "X"
                    points += WordlePoints.FAIL

        return points

    @wordle_points.expression
    def wordle_points(cls: Self) -> ScalarSelect[int]:
        """Compute total Wordle points accumulated by the player."""
        points_case: Case[Any] = case(
            {
                1: WordlePoints.ATTEMPTS_1,
                2: WordlePoints.ATTEMPTS_2,
                3: WordlePoints.ATTEMPTS_3,
                4: WordlePoints.ATTEMPTS_4,
                5: WordlePoints.ATTEMPTS_5,
                6: WordlePoints.ATTEMPTS_6,
            },
            value=WordleResult.attempts,
            else_=WordlePoints.FAIL,
        )

        return (
            select(func.coalesce(func.sum(points_case), 0))
            .where(WordleResult.player_id == cls.id)
            .scalar_subquery()
        )

    wordle_average_attempts: ClassVar[int]
    """Average attempts across all associated Wordle games."""

    @hybrid_property
    def wordle_average_attempts(self: Self) -> int:
        """Return average attempts across all associated Wordle games."""
        if not self.wordle_results:
            return 0

        total_attempts = sum(result.attempts for result in self.wordle_results)

        return round(total_attempts / len(self.wordle_results))

    @wordle_average_attempts.expression
    def wordle_average_attempts(cls: Self) -> ScalarSelect[int]:
        """Compute average attempts across all associated Wordle games."""
        return (
            select(func.coalesce(func.round(func.avg(WordleResult.attempts)), 0))
            .where(WordleResult.player_id == cls.id)
            .correlate(cls)
            .scalar_subquery()
        )

    wordle_completions: ClassVar[int]
    """Total Wordle completions accumulated by the player."""

    @hybrid_property
    def wordle_completions(self: Self) -> int:
        """Return the total count of Wordle completions accumulated by the player."""
        return len(self.wordle_results)

    @wordle_completions.expression
    def wordle_completions(cls: Self) -> ScalarSelect[int]:
        """Compute the total count of Wordle completions accumulated by the player."""
        return (
            select(func.coalesce(func.count(WordleResult.id), 0))
            .where(WordleResult.player_id == cls.id)
            .scalar_subquery()
        )

    wordle_fails: ClassVar[int]
    """Total Wordle fails accumulated by the player."""

    @hybrid_property
    def wordle_fails(self: Self) -> int:
        """Return the total count of Wordle fails accumulated by the player."""
        return sum(1 for result in self.wordle_results if result.attempts == 7)

    @wordle_fails.expression
    def wordle_fails(cls: Self) -> ScalarSelect[int]:
        """Compute the total count of Wordle fails accumulated by the player."""
        return (
            select(func.coalesce(func.count(WordleResult.id), 0))
            .where(WordleResult.player_id == cls.id)
            .where(WordleResult.attempts == 7)
            .scalar_subquery()
        )

    wordle_aces: ClassVar[int]
    """Total Wordle aces accumulated by the player."""

    @hybrid_property
    def wordle_aces(self: Self) -> int:
        """Return the total count of Wordle aces accumulated by the player."""
        return sum(1 for result in self.wordle_results if result.attempts == 1)

    @wordle_aces.expression
    def wordle_aces(cls: Self) -> ScalarSelect[int]:
        """Compute the total count of Wordle aces accumulated by the player."""
        return (
            select(func.coalesce(func.count(WordleResult.id), 0))
            .where(WordleResult.player_id == cls.id)
            .where(WordleResult.attempts == 1)
            .scalar_subquery()
        )
