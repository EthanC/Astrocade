"""Astrocade database models."""

from datetime import date
from typing import ClassVar, Self

from environs import env
from sqlalchemy import ScalarSelect, case
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
                    points += env.int("WORDLE_POINTS_ATTEMPTS_1", 10)
                case 2:
                    points += env.int("WORDLE_POINTS_ATTEMPTS_2", 5)
                case 3:
                    points += env.int("WORDLE_POINTS_ATTEMPTS_3", 4)
                case 4:
                    points += env.int("WORDLE_POINTS_ATTEMPTS_4", 3)
                case 5:
                    points += env.int("WORDLE_POINTS_ATTEMPTS_5", 2)
                case 6:
                    points += env.int("WORDLE_POINTS_ATTEMPTS_6", 1)
                case _:  # Fail
                    points += env.int("WORDLE_POINTS_FAIL", -5)

        return points

    @wordle_points.expression
    def wordle_points(cls: Self) -> ScalarSelect[int]:
        """Compute total Wordle points accumulated by the player."""
        points_case = case(
            {
                1: env.int("WORDLE_POINTS_ATTEMPTS_1", 10),
                2: env.int("WORDLE_POINTS_ATTEMPTS_2", 5),
                3: env.int("WORDLE_POINTS_ATTEMPTS_3", 4),
                4: env.int("WORDLE_POINTS_ATTEMPTS_4", 3),
                5: env.int("WORDLE_POINTS_ATTEMPTS_5", 2),
                6: env.int("WORDLE_POINTS_ATTEMPTS_6", 1),
            },
            value=WordleResult.attempts,
            else_=env.int("WORDLE_POINTS_FAIL", -5),
        )

        return (
            select(func.coalesce(func.sum(points_case), 0))
            .where(WordleResult.player_id == cls.id)
            .scalar_subquery()
        )
