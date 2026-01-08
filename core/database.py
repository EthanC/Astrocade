"""SQLite database connector for Astrocade."""

from pathlib import Path

from arc import GatewayClient, GatewayContext
from loguru import logger
from sqlalchemy import ScalarResult, inspect
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import InstanceState, selectinload
from sqlalchemy.orm.mapper import Mapper
from sqlmodel import Field, SQLModel, func, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from core.models import Player, WordleResult


class Database:
    """Represents an Astrocade Database connection."""

    @staticmethod
    async def setup(location: Path) -> AsyncEngine:
        """Ready the Astrocade database."""
        location.resolve().as_posix()

        if not location.parent.exists() or not location.parent.is_dir():
            logger.debug(
                f"Parent for database does not exist, it will be created: {location.parent}"
            )
            logger.trace(f"{location=}")

            location.parent.mkdir(parents=True, exist_ok=True)

        if not location.exists() or not location.is_file():
            logger.warning(f"Database does not exist, it will be created: {location}")
            logger.debug(f"{location=}")

        engine: AsyncEngine = create_async_engine(
            f"sqlite+aiosqlite:///{location}", echo=True
        )

        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

        logger.info(f"Database {location.name} is ready")
        logger.trace(f"{engine=}")

        return engine

    @staticmethod
    async def close(engine: AsyncEngine) -> None:
        """Shutdown the Astrocade database."""
        await engine.dispose()

        logger.info("Database shutdown")
        logger.trace(f"{engine=}")

    @staticmethod
    async def get_player(client: GatewayClient, id: int) -> Player:
        """Get a player from the Astrocade database."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)

        async with AsyncSession(engine) as session:
            player_mapper: Mapper[Player] = inspect(Player)
            result_mapper: Mapper[WordleResult] = inspect(WordleResult)

            statement: SelectOfScalar[Player] = (
                select(Player)
                .where(Player.id == id)
                .options(
                    selectinload(
                        player_mapper.relationships["wordle_results"]
                    ).selectinload(result_mapper.relationships["puzzle"])
                )
            )

            logger.debug(f"Find Player {id} in database")
            logger.trace(f"{statement=}\n{engine=}\n{session=}")

            results: ScalarResult[Player] = await session.exec(statement)
            player: Player | None = results.first()

            if not player:
                player = Player(id=id)

                session.add(player)

                await session.commit()

                # Re-fetch the new player
                results: ScalarResult[Player] = await session.exec(statement)
                player = results.one()

            logger.debug(f"Found Player {player.id} in database")
            logger.trace(f"{player=}")

            return player

    @staticmethod
    async def count_players(client: GatewayClient) -> int:
        """Count the number of players in the Astrocade database."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)

        async with AsyncSession(engine) as session:
            statement: SelectOfScalar[int] = select(func.count()).select_from(Player)

            logger.debug("Counting Players in database")
            logger.trace(f"{statement=}\n{engine=}\n{session=}")

            results: ScalarResult[int] = await session.exec(statement)
            count: int = results.one()

            logger.debug(f"Found {count} Players in database")

            return count
