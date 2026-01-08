"""Extension containing Wordle commands."""

import re
from datetime import date, datetime, timedelta
from operator import pos
from re import Match, Pattern
from typing import Any, Final, Sequence

import arc
import httpx
from arc import (
    AutodeferMode,
    ChannelParams,
    GatewayClient,
    GatewayContext,
    GatewayPluginBase,
    IntParams,
    Option,
    SlashGroup,
    UserParams,
)
from environs import env
from hikari import (
    Activity,
    ActivityType,
    CommandInteractionMetadata,
    ContainerComponent,
    GuildMessageCreateEvent,
    GuildTextChannel,
    Message,
    Permissions,
    Status,
    TextDisplayComponent,
    User,
)
from httpx import AsyncClient, Response
from loguru import logger
from sqlmodel import func

from core.database import (
    AsyncEngine,
    AsyncSession,
    Database,
    ScalarResult,
    SelectOfScalar,
    select,
)
from core.hooks import Hooks
from core.models import Player, WordlePuzzle, WordleResult
from core.templates import Templates, TemplateType

plugin: GatewayPluginBase = GatewayPluginBase("wordle")
group: SlashGroup = plugin.include_slash_group(
    plugin.name,
    "Commands to interact with Wordle, the daily word game.",
    autodefer=AutodeferMode.ON,  # All commands require database and/or API access
)


@arc.loader
def ext_loader(client: GatewayClient) -> None:
    """Load this extension."""
    logger.debug(f"Loading {plugin.name} extension...")
    logger.trace(f"{plugin=}")

    try:
        client.add_plugin(plugin)
    except Exception as e:
        logger.opt(exception=e).error(f"Failed to load {plugin.name} extension")


@group.include
@arc.with_hook(arc.has_permissions(Permissions.ADMINISTRATOR))
@arc.with_hook(Hooks.command_use)
@arc.slash_subcommand(
    "import",
    "Import channel Wordle data to populate player statistics. Requires Administrator permission.",
)
async def command_wordle_import(
    ctx: GatewayContext,
    channel: Option[
        GuildTextChannel,
        ChannelParams("Channel whose message history will be searched."),
    ],
) -> None:
    """Handle the /wordle import command."""
    msgs: Sequence[Message] = await ctx.client.rest.fetch_messages(channel)
    found: int = 0

    for msg in msgs:
        if await WordleOps.import_data(ctx.client, msg):
            found += 1

    if not found:
        await ctx.respond(
            component=Templates.reply(
                "No Wordle found in the provided channel.", TemplateType.INFO
            )
        )

        return

    await ctx.respond(
        component=Templates.reply(
            f"Imported {found:,} Wordle result(s) from {len(msgs):,} messages.",
            TemplateType.SUCCESS,
        )
    )


@group.include
@arc.with_hook(Hooks.command_use)
@arc.slash_subcommand(
    "stats", "Fetch Wordle statistics for yourself or the provided user."
)
async def command_wordle_stats(
    ctx: GatewayContext,
    user: Option[
        User | None,
        UserParams("User to get Wordle statistics for. Defaults to yourself."),
    ] = None,
) -> None:
    """Handle the /wordle stats command."""
    if not user:
        user = ctx.author

    player = await Database.get_player(ctx.client, user.id)

    if not player.wordle_points:
        await ctx.respond(
            component=Templates.reply(
                "No Wordle statistics found for this player.", TemplateType.WARN
            )
        )

        return

    stats: str = f"## Wordle Statistics for <@{player.id}>"
    stats += f"\n* **Points:** {player.wordle_points:,}"
    stats += f"\n* **Completions:** {len(player.wordle_results):,}"
    stats += f"\n* **Average:** {await WordleOps.get_player_average(ctx.client, player.id):,}"

    await ctx.respond(component=Templates.reply(stats, TemplateType.SUCCESS))


@group.include
@arc.with_hook(Hooks.command_use)
@arc.slash_subcommand("history", "Get puzzle history for the provided Wordle player.")
async def command_wordle_history(
    ctx: GatewayContext,
    user: Option[
        User | None,
        UserParams("User to get Wordle puzzle history for. Defaults to yourself."),
    ] = None,
    limit: Option[
        int,
        IntParams(
            "Number of history entries to fetch. Defaults to 10.", min=1, max=100
        ),
    ] = 10,
) -> None:
    """Handle the /wordle history command."""
    if not user:
        user = ctx.author

    player: Player = await Database.get_player(ctx.client, user.id)

    if not player.wordle_results:
        await ctx.respond(
            component=Templates.reply(
                f"Player {user.mention} has no Wordle history.", TemplateType.WARN
            )
        )

        return

    history: str = f"## Wordle History for {user.mention}"
    results: list[WordleResult] = sorted(
        player.wordle_results, key=lambda r: r.puzzle_id
    )
    current: int = 0

    for result in results:
        timestamp: int = int(
            datetime(
                result.puzzle_day.year, result.puzzle_day.month, result.puzzle_day.day
            ).timestamp()
        )
        history += f"\n* [<t:{timestamp}:d>] ||{result.puzzle_solution}||: {('X' if result.attempts == -1 else f'{result.attempts:,}')}/6"
        current += 1

        if limit and current >= limit:
            break

    await ctx.respond(component=Templates.reply(history, TemplateType.SUCCESS))


@group.include
@arc.with_hook(Hooks.command_use)
@arc.slash_subcommand(
    "leaderboard", "Get the latest Wordle leaderboard standings for this server."
)
async def command_wordle_leaderboard(
    ctx: GatewayContext,
    limit: Option[
        int,
        IntParams(
            "Number of leaderboard entries to fetch. Defaults to 10.", min=1, max=100
        ),
    ] = 10,
) -> None:
    """Handle the /wordle leaderboard command."""
    players: list[Player] = await WordleOps.get_leaderboard(ctx.client, limit)

    if len(players) == 0:
        await ctx.respond(
            component=Templates.reply(
                "No players are currently available to populate the leaderboard.",
                TemplateType.WARN,
            )
        )

        return

    leaderboard: str = "## Wordle Leaderboard"
    pos: int = 1

    for player in players:
        leaderboard += f"\n{pos}. <@{player.id}>: {player.wordle_points:,} points"
        pos += 1

    await ctx.respond(component=Templates.reply(leaderboard, TemplateType.SUCCESS))


@plugin.include
@arc.with_hook(arc.has_permissions(Permissions.ADMINISTRATOR))
@arc.with_hook(Hooks.command_use)
@arc.message_command("Import Wordle Data")
async def command_wordle_import_message(ctx: GatewayContext, msg: Message) -> None:
    """Handle the Import Wordle Data message command."""
    found: WordleResult | list[WordleResult] | None = await WordleOps.import_data(
        ctx.client, msg
    )

    if not found:
        await ctx.respond(
            component=Templates.reply(
                "No valid Wordle data found in the provided message.", TemplateType.INFO
            )
        )

        return

    await ctx.respond(
        component=Templates.reply(
            "Imported Wordle result from the provided message.", TemplateType.SUCCESS
        )
    )


@plugin.listen()
async def event_wordle_message(event: GuildMessageCreateEvent) -> None:
    """Handle server messages upon creation."""
    await WordleOps.import_data(plugin.client, event.message)


@plugin.set_error_handler
async def error_handler(ctx: GatewayContext, error: Exception) -> None:
    """Handle errors originating from this plugin."""
    await Hooks.command_error(ctx, error)


class WordleOps:
    """Operations for the Wordle commands."""

    RGX_STREAK: Final[Pattern[str]] = re.compile(
        r"Your\s+group\s+is\s+on\s+a\s+(\d+)\s+day\s+streak"
    )
    RGX_STREAK_ATTEMPT: Final[Pattern[str]] = re.compile(r"([a-zA-Z0-9]+)/\d+:")
    RGX_STREAK_USER: Final[Pattern[str]] = re.compile(r"<@(\w+)>|@(\w+)")
    RGX_SHARE: Final[Pattern[str]] = re.compile(r"Wordle\s+(\d+)\s+([0-6X])/6")

    @staticmethod
    async def get_result(
        client: GatewayClient, player_id: int, puzzle_id: int
    ) -> WordleResult | None:
        """Get a WordleResult for the given Player and WordlePuzzle from the database."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)
        result_id: str = f"{player_id}_{puzzle_id}"

        async with AsyncSession(engine) as session:
            statement: SelectOfScalar[WordleResult] = select(WordleResult).where(
                WordleResult.id == result_id
            )

            logger.debug(f"Find WordleResult {result_id} in database")
            logger.trace(f"{statement=}\n{engine=}\n{session=}")

            results: ScalarResult[WordleResult] = await session.exec(statement)
            result: WordleResult | None = results.first()

            if result:
                logger.debug(f"Found WordleResult {result.id} in database")
                logger.trace(f"{result=}")
            else:
                logger.trace(f"WordleResult {result_id} does not exist in database")

            return result

    @staticmethod
    async def add_result(
        client: GatewayClient, attempts: int, player_id: int, puzzle_id: int
    ) -> WordleResult | None:
        """Add a WordleResult for the given Player and WordlePuzzle to the database."""
        if result := await WordleOps.get_result(client, player_id, puzzle_id):
            logger.debug(
                f"Skipped WordleResult creation, already exists for player {player_id} and puzzle {puzzle_id}"
            )
            logger.trace(f"{result=}")

            return

        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)
        result: WordleResult = WordleResult(
            id=f"{player_id}_{puzzle_id}",
            attempts=attempts,
            player_id=player_id,
            puzzle_id=puzzle_id,
        )

        async with AsyncSession(engine) as session:
            logger.debug(f"Add WordleResult {result.id} to database")
            logger.trace(f"{result=}\n{engine=}\n{session=}")

            session.add(result)

            await session.commit()
            await session.refresh(result)

            logger.success(f"Added WordleResult {result.id} to database")

            return result

    @staticmethod
    async def get_puzzle(client: GatewayClient, id: int, day: date) -> WordlePuzzle:
        """Get a WordlePuzzle for the given ID or day from the database. Create it if it doesn't exist."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)

        async with AsyncSession(engine) as session:
            statement: SelectOfScalar[WordlePuzzle] = select(WordlePuzzle).where(
                WordlePuzzle.id == id
            )

            logger.debug(f"Find WordlePuzzle {id} in database")
            logger.trace(f"{statement=}\n{engine=}\n{session=}")

            results: ScalarResult[WordlePuzzle] = await session.exec(statement)
            puzzle: WordlePuzzle | None = results.first()

            if not puzzle:
                day: date = await WordleOps.get_puzzle_day(id, day)
                solution: str = await WordleOps.get_puzzle_solution(day)
                puzzle = WordlePuzzle(id=id, day=day, solution=solution)

                session.add(puzzle)

                await session.commit()

            await session.refresh(puzzle)

            logger.success(f"Found Wordle Puzzle {puzzle.id} in database")
            logger.trace(f"{puzzle=}")

            return puzzle

    @staticmethod
    async def get_leaderboard(client: GatewayClient, limit: int) -> list[Player]:
        """Get the top Players ordered by their accumulated Wordle points."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)
        players: list[Player] = []

        async with AsyncSession(engine) as session:
            statement: SelectOfScalar[Player] = (
                select(Player)
                .where(Player.wordle_points > 0)
                .order_by(Player.wordle_points.desc())
                .limit(limit)
            )

            results: ScalarResult[Player] = await session.exec(statement)
            players = list(results.all())

            logger.debug(f"Found {len(players):,} players for Wordle Leaderboard")
            logger.trace(f"{players=}")

            return players

    @staticmethod
    async def get_player_average(client: GatewayClient, player_id: int) -> int:
        """Get the average number of attempts for a player's Wordle results."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)
        player: Player = await Database.get_player(client, player_id)

        if not player.wordle_results:
            return 0

        total_attempts: int = sum(result.attempts for result in player.wordle_results)

        return total_attempts // len(player.wordle_results)

    @staticmethod
    async def _get_puzzle_metadata(day: date) -> dict[str, Any]:
        """Fetch Wordle puzzle metadata from NYT for the given day."""
        async with AsyncClient() as client:
            res: Response = (
                await client.get(
                    f"https://www.nytimes.com/svc/wordle/v2/{day.strftime('%Y-%m-%d')}.json"
                )
            ).raise_for_status()

            logger.debug(f"HTTP {res.status_code} GET {res.url}")
            logger.trace(f"{res=}\n{res.text}")

            return res.json()

    @staticmethod
    async def get_puzzle_id(day: date) -> int:
        """Get Wordle puzzle ID for a given day."""
        for offset in (0, -1, 1):
            possibility: date = day + timedelta(days=offset)
            metadata: dict[str, Any] = await WordleOps._get_puzzle_metadata(possibility)
            print_date: str | None = metadata.get("print_date")
            days_since: int | None = metadata.get("days_since_launch")

            if (
                print_date == possibility.strftime("%Y-%m-%d")
                and days_since is not None
            ):
                return days_since

        raise RuntimeError(f"Failed to determine Wordle puzzle ID for {day=}")

    @staticmethod
    async def get_puzzle_day(id: int, start_day: date) -> date:
        """Get Wordle puzzle day for a given ID, checking nearby days."""
        for offset in (0, -1, 1):
            possibility: date = start_day + timedelta(days=offset)
            metadata: dict[str, Any] = await WordleOps._get_puzzle_metadata(possibility)
            days_since: int | None = metadata.get("days_since_launch")
            print_date: str | None = metadata.get("print_date")

            if days_since == id and print_date:
                return datetime.strptime(print_date, "%Y-%m-%d").date()

        raise RuntimeError(f"Failed to determine Wordle puzzle day for {id=}")

    @staticmethod
    async def get_puzzle_solution(day: date) -> str:
        """Get Wordle puzzle solution for a given day."""
        metadata: dict[str, Any] = await WordleOps._get_puzzle_metadata(day)
        print_date: str | None = metadata.get("print_date")
        solution: str = metadata.get("solution", "-----")

        if print_date == day.strftime("%Y-%m-%d"):
            return solution.upper()

        raise RuntimeError(f"Failed to determine Wordle puzzle solution for {day=}")

    @staticmethod
    async def import_data(
        client: GatewayClient, msg: Message
    ) -> WordleResult | list[WordleResult] | None:
        """Import Wordle data from a Discord message."""
        if not msg.author.is_bot:
            logger.debug(
                f"Skipped message {msg.id} by {msg.author.id}, author is not a bot"
            )
            logger.trace(f"{msg=}")

            return
        elif msg.author.id != env.int("DISCORD_WORDLE_BOT_ID"):
            logger.debug(
                f"Skipped message {msg.id} by {msg.author.id}, author is not Wordle"
            )
            logger.trace(f"{msg=}")

            return
        elif not msg.content and not msg.components:
            logger.debug(
                f"Skipped message {msg.id} by {msg.author.id}, no data to import"
            )
            logger.trace(f"{msg=}")

            return

        return await WordleOps._import_streak(
            client, msg
        ) or await WordleOps._import_share(client, msg)

    @staticmethod
    async def _import_streak(
        client: GatewayClient, msg: Message
    ) -> WordleResult | list[WordleResult] | None:
        """Import Wordle data from a streak Discord message."""
        if not msg.content or not re.search(WordleOps.RGX_STREAK, msg.content):
            logger.debug(f"Skipped message {msg.id} by {msg.author.id}, not a streak")
            logger.trace(f"{msg=}")

            return

        results: list[WordleResult] = []

        for line in msg.content.splitlines():
            attempt: Match[str] | None = re.search(WordleOps.RGX_STREAK_ATTEMPT, line)

            if not attempt:
                logger.debug(
                    f"Skipped line in message {msg.id} by {msg.author.id}, not an attempt"
                )
                logger.trace(f"{line=}")

                continue

            day: date = msg.created_at.date() - timedelta(days=1)  # Yesterday
            attempts: int = (
                -1 if (attempts_raw := attempt.group(1)) == "X" else int(attempts_raw)
            )
            mentions: list[Any] = re.findall(WordleOps.RGX_STREAK_USER, line)

            for mention in mentions:
                user: int | str = int(mention[0]) if mention[0] else mention[1]

                # Resolve User ID for display name
                if isinstance(user, str):
                    if msg.guild_id:
                        for member in await client.rest.search_members(
                            int(msg.guild_id), user
                        ):
                            names: list[str] = [member.username.lower()]

                            if member.global_name:
                                names.append(member.global_name.lower())

                            if member.nickname:
                                names.append(member.nickname.lower())

                            for name in names:
                                if name == user.lower():
                                    user = member.id

                                    break

                if isinstance(user, str):
                    logger.error(
                        f"Failed to determine User ID unmentioned name: {user}"
                    )
                    logger.debug(f"{mention=}\n{user=}")

                    continue

                player: Player = await Database.get_player(client, user)
                puzzle_id: int = await WordleOps.get_puzzle_id(day)
                puzzle: WordlePuzzle = await WordleOps.get_puzzle(
                    client, puzzle_id, day
                )
                result: WordleResult | None = await WordleOps.add_result(
                    client, attempts, player.id, puzzle.id
                )

                if result:
                    results.append(result)

                logger.success(f"Created placement for {user}")

        return results

    @staticmethod
    async def _import_share(
        client: GatewayClient, msg: Message
    ) -> WordleResult | list[WordleResult] | None:
        """Import Wordle data from a share Discord message."""
        if not msg.components:
            logger.debug(f"Skipped message {msg.id} by {msg.author.id}, no components")
            logger.trace(f"{msg=}")

            return
        elif not isinstance(msg.components[0], ContainerComponent):
            logger.debug(
                f"Skipped message {msg.id} by {msg.author.id}, first component is not a container"
            )
            logger.trace(f"{msg=}\n{msg.components=}")

            return

        container: ContainerComponent = msg.components[0]

        if not container.components:
            logger.debug(
                f"Skipped message {msg.id} by {msg.author.id}, container has no components"
            )
            logger.trace(f"{msg=}\n{container=}")

            return
        elif not isinstance(container.components[0], TextDisplayComponent):
            logger.debug(
                f"Skipped message {msg.id} by {msg.author.id}, first component in container is not text"
            )
            logger.trace(f"{msg=}\n{container.components=}")

            return

        text: TextDisplayComponent = container.components[0]

        if not text.content:
            logger.debug(
                f"Skipped message {msg.id} by {msg.author.id}, text component has no content"
            )
            logger.trace(f"{msg=}\n{text=}")

            return

        match: Match[str] | None = re.search(WordleOps.RGX_SHARE, text.content)

        if not match:
            logger.debug(
                f"Skipped message {msg.id} by {msg.author.id}, text is not a share"
            )
            logger.trace(f"{msg=}\n{text.content=}")

            return

        puzzle_id: int = int(match.group(1))
        attempts: int = (
            -1 if (attempts_raw := match.group(2)) == "X" else int(attempts_raw)
        )
        user: int | None = None

        if isinstance(msg.interaction_metadata, CommandInteractionMetadata):
            if isinstance(msg.interaction_metadata.user, User):
                user = msg.interaction_metadata.user.id

        if not user:
            raise RuntimeError(f"Failed to determine User ID for Wordle share {msg.id}")

        player: Player = await Database.get_player(client, user)
        puzzle: WordlePuzzle = await WordleOps.get_puzzle(
            client, puzzle_id, msg.created_at.date()
        )

        return await WordleOps.add_result(client, attempts, player.id, puzzle.id)
