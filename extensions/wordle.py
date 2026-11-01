"""Extension containing Wordle commands."""

import re
import sqlite3
from asyncio import sleep, to_thread
from collections import Counter
from datetime import date, datetime, timedelta
from operator import pos
from re import Match, Pattern
from typing import Any, Final, NamedTuple, Sequence

import arc
import niquests
from arc import (
    AttachmentParams,
    AutocompleteData,
    AutodeferMode,
    ChannelParams,
    GatewayClient,
    GatewayContext,
    GatewayPluginBase,
    IntParams,
    Option,
    SlashGroup,
    StrParams,
    UserParams,
)
from hikari import (
    Activity,
    ActivityType,
    Attachment,
    CommandChoice,
    CommandInteractionMetadata,
    ContainerComponent,
    GuildMessageCreateEvent,
    GuildTextChannel,
    Member,
    Message,
    MessageReference,
    PartialChannel,
    Permissions,
    Snowflake,
    SpacingType,
    Status,
    TextDisplayComponent,
    User,
)
from hikari.impl import (
    ContainerComponentBuilder,
    LinkButtonBuilder,
    MediaGalleryComponentBuilder,
    MediaGalleryItemBuilder,
    MessageActionRowBuilder,
    SeparatorComponentBuilder,
    TextDisplayComponentBuilder,
)
from hikari.messages import MessageReferenceType
from loguru import logger
from niquests.models import Response
from sqlalchemy import ColumnElement, Integer, case, true
from sqlalchemy.orm import Mapped
from sqlmodel import col, func
from sqlmodel.sql.expression import Select

from core.consts import (
    ASTROCADE_LOGO,
    DICTIONARY_API_KEY,
    MAX_IMPORT_DATABASE_BYTES,
    MAX_RESULTS_IMAGE_BYTES,
    REGEX_MARKDOWN_ESCAPE,
    REGEX_WORDLE_PLAYING,
    REGEX_WORDLE_PLAYING_OTHERS,
    REGEX_WORDLE_PLAYING_SEPARATOR,
    REGEX_WORDLE_SHARE,
    REGEX_WORDLE_STREAK,
    REGEX_WORDLE_STREAK_ATTEMPT,
    REGEX_WORDLE_STREAK_TAG,
    TZ,
    WORDLE_BOT_ID,
    WORDLE_CUTOFF_HOUR,
    WORDLE_EPOCH,
    WORDLE_ICON,
    WORDLE_NAME_LOOKBACK,
    Direction,
    WordleLeaderboardType,
    WordlePoints,
)
from core.database import (
    AsyncEngine,
    AsyncSession,
    Database,
    ScalarResult,
    SelectOfScalar,
    select,
)
from core.hooks import Hooks
from core.models import Player, WordlePuzzle, WordleResult, WordleStreak
from core.ocr import OCR
from core.seasons import Season, season_caption, season_label, today
from core.templates import Templates, TemplateType, percentage

plugin: GatewayPluginBase = GatewayPluginBase("wordle")
group: SlashGroup = plugin.include_slash_group(
    plugin.name,
    "Commands to interact with Wordle, the daily word game.",
    autodefer=AutodeferMode.ON,  # All commands require database and/or API access
)

SEASON_ALL_TIME: Final[str] = "all"
"""Season option value that selects stats across all seasons."""

LEADERBOARDS: Final[dict[WordleLeaderboardType, tuple[str, str, str]]] = {
    WordleLeaderboardType.POINTS: ("Wordle Points Leaderboard", "points", "points"),
    WordleLeaderboardType.POINTS_GROSS: (
        "Wordle Gross Points Leaderboard",
        "points_gross",
        "points",
    ),
    WordleLeaderboardType.AVERAGES: (
        "Wordle Average Leaderboard",
        "average_attempts",
        "attempts",
    ),
    WordleLeaderboardType.FAILS: ("Wordle Loserboard", "fails", "fails"),
    WordleLeaderboardType.ACES: ("Wordle Aces Leaderboard", "aces", "aces"),
    WordleLeaderboardType.COMPLETIONS: (
        "Wordle Completions Leaderboard",
        "completions",
        "completions",
    ),
}
"""Leaderboard categories as (title, WordleStats field, unit)."""


class InvalidSeasonError(Exception):
    """Raised when a season option value does not identify a season."""


class InvalidDatabaseError(Exception):
    """Raised when a provided file is not a readable Astrocade database."""


class WordleImportSummary(NamedTuple):
    """Counts of Wordle data merged from an exported Astrocade database."""

    puzzles: int
    """WordlePuzzles added to the database."""

    players: int
    """Players added to the database."""

    results: int
    """WordleResults added to the database."""

    streaks: int
    """WordleStreaks added to the database."""

    duplicates: int
    """Records skipped because an identical record already exists."""

    conflicts: int
    """Records skipped because they are invalid or disagree with existing data."""

    @property
    def added(self) -> int:
        """Total records added to the database."""
        return self.puzzles + self.players + self.results + self.streaks


class WordleStats(NamedTuple):
    """Wordle statistics accumulated by a player over a season."""

    player_id: int
    """Discord User ID of the player."""

    points: int
    """Total Wordle points, including deductions for fails."""

    points_gross: int
    """Total Wordle points, excluding deductions for fails."""

    average_attempts: int
    """Average attempts across non-failed Wordle games."""

    completions: int
    """Total Wordle games played, including fails."""

    fails: int
    """Total failed Wordle games."""

    aces: int
    """Total Wordle games solved in 1 attempt."""


async def autocomplete_season(
    data: AutocompleteData[GatewayClient, str],
) -> dict[str, str]:
    """Suggest the seasons containing Wordle puzzles (newest first) and All-Time."""
    now: date = today()
    first: date = await WordleOps.get_first_puzzle_day(data.client) or now
    query: str = str(data.focused_value or "").strip().lower()
    choices: dict[str, str] = {}

    for season in Season.span(min(first, now), now):
        name: str = f"{season.label} ({'Current' if season.contains(now) else season.dates_plain})"

        if query in name.lower() or query in season.key:
            choices[name] = season.key

    choices = dict(list(choices.items())[:24])

    if query.replace(" ", "-") in "all-time":
        choices["All-Time"] = SEASON_ALL_TIME

    return choices


SeasonOption = Option[
    str | None,
    StrParams(
        "Season to view. Defaults to the current season.",
        autocomplete_with=autocomplete_season,
    ),
]


@arc.loader
def ext_loader(client: GatewayClient) -> None:
    """Load this extension."""
    logger.debug(f"Loading the {plugin.name} extension")
    logger.trace(f"{plugin=}")

    try:
        client.add_plugin(plugin)
    except Exception as e:
        logger.opt(exception=e).error(f"Failed to load the {plugin.name} extension")

        return

    logger.info(f"Loaded the {plugin.name} extension")


@group.include
@arc.with_hook(arc.has_permissions(Permissions.ADMINISTRATOR))
@arc.with_hook(Hooks.command_use)
@arc.slash_subcommand(
    "import",
    "Import Wordle data from channel history or an exported database. Requires Administrator permission.",
)
async def command_wordle_import(
    ctx: GatewayContext,
    channel: Option[
        GuildTextChannel | None,
        ChannelParams("Channel whose message history will be searched."),
    ] = None,
    cutoff: Option[
        str | None, StrParams("Message ID to stop importing data at.")
    ] = None,
    database: Option[
        Attachment | None,
        AttachmentParams(
            "Exported Astrocade database to merge Wordle data from, instead of a channel."
        ),
    ] = None,
) -> None:
    """Handle the /wordle import command."""
    if database:
        if database.size > MAX_IMPORT_DATABASE_BYTES:
            raise InvalidDatabaseError(
                f"`{database.filename}` is too large ({database.size:,} bytes)."
            )

        summary: WordleImportSummary = await WordleOps.import_database(
            ctx.client, await database.read()
        )
        skipped: str = ""

        if summary.duplicates or summary.conflicts:
            skipped = f"\n-# Skipped **{summary.duplicates:,}** duplicate and **{summary.conflicts:,}** conflicting record(s), keeping existing data."

        if not summary.added:
            await ctx.respond(
                component=Templates.generic(
                    TemplateType.INFO,
                    f"No new Wordle data found in `{database.filename}`.{skipped}",
                )
            )

            return

        added: str = f"**{summary.puzzles:,}** puzzles"
        added += f", **{summary.results:,}** results"
        added += f", **{summary.players:,}** players"
        added += f", and **{summary.streaks:,}** streaks"

        await ctx.respond(
            component=Templates.generic(
                TemplateType.SUCCESS,
                f"Merged Wordle data from `{database.filename}`: {added} added.{skipped}",
            )
        )

        return

    if not channel:
        cur_channel: PartialChannel = await ctx.client.rest.fetch_channel(
            ctx.channel_id
        )

        if not isinstance(cur_channel, GuildTextChannel):
            raise RuntimeError(
                f"Current channel is {cur_channel}, expected GuildTextChannel"
            )

        channel = cur_channel

    msgs: Sequence[Message] = await ctx.client.rest.fetch_messages(channel)
    found: int = 0
    searched: int = 0

    for msg in msgs:
        searched += 1

        if cutoff and str(msg.id) == cutoff:
            logger.debug(f"Reached cutoff message during Wordle data import")
            logger.trace(f"{cutoff=} {msg=}")

            break

        # Check for Forwarded Message
        if msg.message_reference:
            ref: MessageReference = msg.message_reference

            if ref.type == MessageReferenceType.FORWARD and ref.id:
                fwd: Message = await ctx.client.rest.fetch_message(
                    ref.channel_id, ref.id
                )

                if await WordleOps.import_data(ctx.client, fwd, guild_id=ref.guild_id):
                    found += 1

        if await WordleOps.import_data(ctx.client, msg):
            found += 1

        # Avoid rate limit
        await sleep(0.25)

    if not found:
        await ctx.respond(
            component=Templates.generic(
                TemplateType.INFO, f"No Wordle data found in {searched:,} messages."
            )
        )

        return

    await ctx.respond(
        component=Templates.generic(
            TemplateType.SUCCESS,
            f"Imported {found:,} Wordle result(s) from {searched:,} messages.",
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
    season: SeasonOption = None,
) -> None:
    """Handle the /wordle stats command."""
    if not user:
        user = ctx.author

    selected: Season | None = WordleOps.resolve_season(season)
    player: WordleStats | None = await WordleOps.get_player_stats(
        ctx.client, user.id, selected
    )

    if not player:
        await ctx.respond(
            component=Templates.generic(
                TemplateType.INFO,
                f"No {season_label(selected)} Wordle statistics found for this player.",
            )
        )

        return

    total_puzzles: int = await WordleOps.count_puzzles(ctx.client, selected)
    completions: int = player.completions
    fails: int = player.fails
    aces: int = player.aces

    stats: str = f"## Wordle Statistics for <@{player.player_id}>"
    stats += f"\n-# {season_caption(selected)}"
    stats += f"\n* Points: **{player.points:,}** ({player.points_gross:,} gross)"
    stats += f"\n* Average: **{player.average_attempts:,}**"
    stats += f"\n* Completions: **{completions:,}** of {total_puzzles:,} ({percentage(completions, total_puzzles):,}%)"
    stats += f"\n* Fails: **{fails:,}** of {total_puzzles:,} ({percentage(fails, total_puzzles):,}%) | {WordlePoints.FAIL * fails:,} points"
    stats += f"\n* Aces: **{aces:,}** of {total_puzzles:,} ({percentage(aces, total_puzzles):,}%) | {WordlePoints.ATTEMPTS_1 * aces:,} points"

    await ctx.respond(
        component=Templates.generic_thumb(TemplateType.SUCCESS, stats, WORDLE_ICON)
    )


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
            "Number of history entries to fetch. Defaults to 25.", min=1, max=100
        ),
    ] = 25,
    min_attempts: Option[
        int | None,
        IntParams(
            "Minimum number of attempts for an entry to be included in the history.",
            min=1,
            max=6,
        ),
    ] = None,
    max_attempts: Option[
        int | None,
        IntParams(
            "Maximum number of attempts for an entry to be included in the history.",
            min=1,
            max=6,
        ),
    ] = None,
    season: SeasonOption = None,
) -> None:
    """Handle the /wordle history command."""
    if min_attempts and max_attempts:
        if min_attempts > max_attempts:
            await ctx.respond(
                component=Templates.generic(
                    TemplateType.ERROR,
                    "Minimum attempts must be less than maximum attempts.",
                )
            )

            return

    if not user:
        user = ctx.author

    selected: Season | None = WordleOps.resolve_season(season)
    player: Player = await Database.get_player(ctx.client, user.id)
    results: list[WordleResult] = sorted(
        (
            result
            for result in player.wordle_results
            if not selected or selected.contains(result.puzzle_day)
        ),
        key=lambda r: r.puzzle_id,
        reverse=True,
    )

    if not results:
        await ctx.respond(
            component=Templates.generic(
                TemplateType.INFO,
                f"Player {user.mention} has no {season_label(selected)} Wordle history.",
            )
        )

        return

    history: str = f"## Wordle History for {user.mention}"
    history += f"\n-# {season_caption(selected)}"
    current: int = 0

    for result in results:
        attempts: int = result.attempts

        if min_attempts and (attempts < min_attempts):
            logger.debug(f"Skipped result {result.id}, attempts below minimum")
            logger.trace(f"{result=}")

            continue
        elif max_attempts and (attempts > max_attempts):
            logger.debug(f"Skipped result {result.id}, attempts below minimum")
            logger.trace(f"{result=}")

            continue

        puzzle_id: int = result.puzzle_id
        solution: str = result.puzzle_solution
        score: str = "X" if attempts == 7 else f"{attempts:,}"
        points: int = await WordleOps.get_points(attempts)

        history += (
            f"\n* `{puzzle_id}` ||**`{solution}`**||: **{score}**/6 ({points:,} points)"
        )

        current += 1

        if limit and current >= limit:
            break

    await ctx.respond(
        component=Templates.generic_thumb(TemplateType.SUCCESS, history, WORDLE_ICON)
    )


@group.include
@arc.with_hook(Hooks.command_use)
@arc.slash_subcommand(
    "leaderboard", "Get the latest Wordle leaderboard standings for this server."
)
async def command_wordle_leaderboard(
    ctx: GatewayContext,
    category: Option[
        str,
        StrParams(
            f"Statistic of the leaderboard. Default is {WordleLeaderboardType.POINTS}.",
            choices=[
                CommandChoice(
                    name=WordleLeaderboardType.POINTS,
                    value=WordleLeaderboardType.POINTS,
                ),
                CommandChoice(
                    name=WordleLeaderboardType.POINTS_GROSS,
                    value=WordleLeaderboardType.POINTS_GROSS,
                ),
                CommandChoice(
                    name=WordleLeaderboardType.AVERAGES,
                    value=WordleLeaderboardType.AVERAGES,
                ),
                CommandChoice(
                    name=WordleLeaderboardType.FAILS, value=WordleLeaderboardType.FAILS
                ),
                CommandChoice(
                    name=WordleLeaderboardType.ACES, value=WordleLeaderboardType.ACES
                ),
                CommandChoice(
                    name=WordleLeaderboardType.COMPLETIONS,
                    value=WordleLeaderboardType.COMPLETIONS,
                ),
                CommandChoice(
                    name=WordleLeaderboardType.SEASONAL_WINS,
                    value=WordleLeaderboardType.SEASONAL_WINS,
                ),
            ],
        ),
    ] = WordleLeaderboardType.POINTS,
    season: SeasonOption = None,
    limit: Option[
        int,
        IntParams(
            "Number of leaderboard entries to fetch. Defaults to 10.", min=1, max=100
        ),
    ] = 10,
    direction: Option[
        str,
        StrParams(
            f"Sort order of the leaderboard. Default is {Direction.DESCENDING}.",
            choices=[
                CommandChoice(name=Direction.ASCENDING, value=Direction.ASCENDING),
                CommandChoice(name=Direction.DESCENDING, value=Direction.DESCENDING),
            ],
        ),
    ] = Direction.DESCENDING,
) -> None:
    """Handle the /wordle leaderboard command."""
    await ctx.respond(
        component=await WordleOps.get_leaderboard_message(
            WordleLeaderboardType(category),
            limit,
            direction,
            WordleOps.resolve_season(season),
        )
    )


@group.include
@arc.with_hook(Hooks.command_use)
@arc.slash_subcommand(
    "help", "Display the Wordle statistics and ruleset for this Astrocade instance."
)
async def command_wordle_help(ctx: GatewayContext) -> None:
    """Handle the /wordle help command."""
    players: int = await Database.count_players(ctx.client)
    puzzles: int = await WordleOps.count_puzzles(ctx.client)
    completions: int = await WordleOps.count_results(ctx.client)

    season: Season = Season.current()
    remaining: int = season.days_remaining()

    stats: str = f"-# Tracking **{players:,}** players over **{puzzles:,}** puzzles with **{completions:,}** completions."
    stats += f"\n-# **{season.label}** runs {season.dates} · **{remaining:,}** day{'' if remaining == 1 else 's'} remaining.\n"

    rules: str = f"1 Attempt: **{WordlePoints.ATTEMPTS_1:,}** points"
    rules += f"\n2 Attempts: **{WordlePoints.ATTEMPTS_2:,}** points"
    rules += f"\n3 Attempts: **{WordlePoints.ATTEMPTS_3:,}** points"
    rules += f"\n4 Attempts: **{WordlePoints.ATTEMPTS_4:,}** points"
    rules += f"\n5 Attempts: **{WordlePoints.ATTEMPTS_5:,}** points"
    rules += f"\n6 Attempts: **{WordlePoints.ATTEMPTS_6:,}** points"
    rules += f"\nFail: **{WordlePoints.FAIL:,}** points"

    await ctx.respond(
        components=[
            ContainerComponentBuilder(
                components=[
                    MediaGalleryComponentBuilder(
                        items=[MediaGalleryItemBuilder(media=ASTROCADE_LOGO)]
                    ),
                    SeparatorComponentBuilder(spacing=SpacingType.LARGE, divider=True),
                    TextDisplayComponentBuilder(content="## Wordle"),
                    TextDisplayComponentBuilder(content=stats),
                    TextDisplayComponentBuilder(content=rules),
                    TextDisplayComponentBuilder(
                        content="-# Made with :green_heart: by <@132693143173857281>"
                    ),
                ]
            ),
            MessageActionRowBuilder(
                components=[
                    LinkButtonBuilder(
                        label="GitHub", url="https://github.com/EthanC/Astrocade"
                    )
                ]
            ),
        ]
    )


@group.include
@arc.with_hook(Hooks.command_use)
@arc.slash_subcommand("puzzle", "Get details of a specific Wordle puzzle.")
async def command_wordle_puzzle(
    ctx: GatewayContext,
    id: Option[
        int,
        IntParams(
            "Unique identifier of the puzzle, represented by the number of days since Wordle began.",
            min=1,
        ),
    ],
) -> None:
    """Handle the /wordle puzzle command."""
    puzzle: WordlePuzzle | None = await WordleOps.get_puzzle(
        ctx.client, id, None, skip_create=True
    )

    if not puzzle:
        await ctx.respond(
            component=Templates.generic(
                TemplateType.INFO,
                f"Wordle Puzzle `{id}` does not yet exist. Try again later.",
            )
        )

        return

    definition: str | None = await WordleOps.get_definition(puzzle.solution)
    total_players: int = await Database.count_players(ctx.client)

    details: str = f"## Wordle Puzzle `{puzzle.id}`"
    details += f"\n### Solution: ||`{puzzle.solution}`||"

    if definition:
        details += f"\n> ||{definition}||"

    details += f"\n* Completions: **{len(puzzle.results):,}** ({percentage(len(puzzle.results), total_players):,}%)"
    details += f"\n* Fails: **{puzzle.fails:,}** ({percentage(puzzle.fails, total_players):,}%)"
    details += (
        f"\n* Aces: **{puzzle.aces:,}** ({percentage(puzzle.aces, total_players):,}%)"
    )
    details += f"\n* Average Attempts: **{puzzle.average_attempts:,}**"
    details += "\n### Attempts Recap"

    recap: dict[int, list[str]] = {1: [], 2: [], 3: [], 4: [], 5: [], 6: [], 7: []}

    for result in puzzle.results:
        recap[result.attempts].append(f"<@{result.player_id}>")

    for tier in recap:
        details += f"\n*{' :crown: ' if tier == 1 else ' '}**{'X' if tier == 7 else tier}**/6: {', '.join(recap[tier]) if recap[tier] else '--'}"

    await ctx.respond(
        component=Templates.generic_thumb(TemplateType.SUCCESS, details, WORDLE_ICON)
    )


@plugin.include
@arc.with_hook(arc.has_permissions(Permissions.ADMINISTRATOR))
@arc.with_hook(Hooks.command_use)
@arc.message_command("Import Wordle Data")
async def command_wordle_import_message(ctx: GatewayContext, msg: Message) -> None:
    """Handle the Import Wordle Data message command."""
    found: list[WordleResult] = []
    results: WordleResult | list[WordleResult] | None = await WordleOps.import_data(
        ctx.client, msg
    )

    if isinstance(results, list):
        found.extend(results)
    elif results:
        found.append(results)

    # Check for Forwarded Message
    if msg.message_reference:
        ref: MessageReference = msg.message_reference

        if ref.type == MessageReferenceType.FORWARD and ref.id:
            fwd: Message = await ctx.client.rest.fetch_message(ref.channel_id, ref.id)
            results = await WordleOps.import_data(
                ctx.client, fwd, guild_id=ref.guild_id
            )

            if isinstance(results, list):
                found.extend(results)
            elif results:
                found.append(results)

    if not found:
        await ctx.respond(
            component=Templates.generic(
                TemplateType.INFO, "No valid Wordle data found in the provided message."
            )
        )

        return

    await ctx.respond(
        component=Templates.generic(
            TemplateType.SUCCESS, "Imported Wordle result(s) from the provided message."
        )
    )


@group.include
@arc.with_hook(Hooks.command_use)
@arc.slash_subcommand(
    "missing", "Get a list of missing Wordle puzzle completions for the provided user."
)
async def command_wordle_missing(
    ctx: GatewayContext,
    user: Option[
        User | None,
        UserParams(
            "User to get missing Wordle puzzle completions for. Defaults to yourself."
        ),
    ] = None,
    limit: Option[
        int,
        IntParams(
            "Number of missing puzzles to fetch. Defaults to 25.", min=1, max=100
        ),
    ] = 25,
    season: SeasonOption = None,
) -> None:
    """Handle the /wordle missing command."""
    if not user:
        user = ctx.author

    selected: Season | None = WordleOps.resolve_season(season)
    label: str = season_label(selected)
    total: int = await WordleOps.count_puzzles(ctx.client, selected)
    missing: int = await WordleOps.count_missing_puzzles(ctx.client, user.id, selected)

    if not missing:
        await ctx.respond(
            component=Templates.generic(
                TemplateType.INFO,
                f"Player {user.mention} is not missing any {label} Wordle puzzle completions."
                if total
                else f"There are no {label} Wordle puzzles to be missing completion yet.",
            )
        )

        return

    puzzles: list[WordlePuzzle] = await WordleOps.get_missing_puzzles(
        ctx.client, user.id, limit, selected
    )

    result: str = f"## Missing Wordle Puzzle Completions"
    result += f"\n-# {season_caption(selected)}"
    result += f"\n-# {user.mention} is missing **{missing:,}** of {total:,} completions ({percentage(missing, total):,}%)."

    for puzzle in puzzles:
        result += f"\n* `{puzzle.id}` ||**`{puzzle.solution}`**||"

    remaining: int = missing - len(puzzles)

    if remaining:
        result += f"\n-# ...and **{remaining:,}** more."

    await ctx.respond(
        component=Templates.generic_thumb(TemplateType.SUCCESS, result, WORDLE_ICON)
    )


@plugin.listen()
async def event_wordle_message(event: GuildMessageCreateEvent) -> None:
    """Handle server messages upon creation."""
    await WordleOps.import_data(plugin.client, event.message, event=True)


@plugin.set_error_handler
async def error_handler(ctx: GatewayContext, error: Exception) -> None:
    """Handle errors originating from this plugin."""
    if isinstance(error, InvalidSeasonError):
        await ctx.respond(
            component=Templates.generic(
                TemplateType.ERROR,
                f"Unknown season `{error}`. Choose a season from the list.",
            )
        )

        return
    elif isinstance(error, InvalidDatabaseError):
        await ctx.respond(
            component=Templates.generic(
                TemplateType.ERROR, f"Failed to import database: {error}"
            )
        )

        return

    await Hooks.command_error(ctx, error)


class WordleOps:
    """Operations for the Wordle commands."""

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
            if result.attempts != attempts:
                logger.warning(
                    f"Skipped WordleResult creation for player {player_id} and puzzle {puzzle_id}, existing result has {result.attempts} attempts but {attempts} were reported"
                )
            else:
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
    async def get_result_conflicts(
        client: GatewayClient, puzzle_id: int, entries: dict[int, int]
    ) -> list[int]:
        """Get the Players whose existing WordleResult for the given puzzle disagrees with the given attempts."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)

        async with AsyncSession(engine) as session:
            statement: SelectOfScalar[WordleResult] = (
                select(WordleResult)
                .where(WordleResult.puzzle_id == puzzle_id)
                .where(col(WordleResult.player_id).in_(list(entries)))
            )

            logger.debug(f"Find conflicting WordleResults for puzzle {puzzle_id}")
            logger.trace(f"{statement=}\n{engine=}\n{session=}")

            results: ScalarResult[WordleResult] = await session.exec(statement)

            return [
                result.player_id
                for result in results.all()
                if result.attempts != entries[result.player_id]
            ]

    @staticmethod
    async def get_streak(client: GatewayClient, id: int) -> WordleStreak | None:
        """Get a WordleStreak for the given Discord Message ID from the database."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)

        async with AsyncSession(engine) as session:
            statement: SelectOfScalar[WordleStreak] = select(WordleStreak).where(
                WordleStreak.id == id
            )

            logger.debug(f"Find WordleStreak {id} in database")
            logger.trace(f"{statement=}\n{engine=}\n{session=}")

            results: ScalarResult[WordleStreak] = await session.exec(statement)

            return results.first()

    @staticmethod
    async def get_adjacent_streaks(
        client: GatewayClient, channel_id: int, id: int
    ) -> tuple[WordleStreak | None, WordleStreak | None]:
        """Get the WordleStreaks immediately preceding and following the given Discord Message ID in a channel."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)

        async with AsyncSession(engine) as session:
            preceding: SelectOfScalar[WordleStreak] = (
                select(WordleStreak)
                .where(WordleStreak.channel_id == channel_id)
                .where(col(WordleStreak.id) < id)
                .order_by(col(WordleStreak.id).desc())
                .limit(1)
            )
            following: SelectOfScalar[WordleStreak] = (
                select(WordleStreak)
                .where(WordleStreak.channel_id == channel_id)
                .where(col(WordleStreak.id) > id)
                .order_by(col(WordleStreak.id).asc())
                .limit(1)
            )

            logger.debug(f"Find WordleStreaks adjacent to {id} in channel {channel_id}")
            logger.trace(f"{preceding=}\n{following=}\n{engine=}\n{session=}")

            return (
                (await session.exec(preceding)).first(),
                (await session.exec(following)).first(),
            )

    @staticmethod
    async def get_claimed_puzzles(
        client: GatewayClient, channel_id: int, puzzle_ids: range
    ) -> set[int]:
        """Get the given puzzle IDs that are already attributed to a WordleStreak in a channel."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)

        async with AsyncSession(engine) as session:
            statement: SelectOfScalar[int] = (
                select(WordleStreak.puzzle_id)
                .where(WordleStreak.channel_id == channel_id)
                .where(col(WordleStreak.puzzle_id).in_(list(puzzle_ids)))
            )

            logger.debug(f"Find claimed WordlePuzzles in channel {channel_id}")
            logger.trace(f"{statement=}\n{engine=}\n{session=}")

            results: ScalarResult[int] = await session.exec(statement)

            return set(results.all())

    @staticmethod
    async def add_streak(
        client: GatewayClient, msg: Message, streak: int, puzzle_id: int
    ) -> WordleStreak:
        """Add a WordleStreak for the given Discord Message and WordlePuzzle to the database."""
        if existing := await WordleOps.get_streak(client, msg.id):
            return existing

        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)
        record: WordleStreak = WordleStreak(
            id=int(msg.id),
            channel_id=int(msg.channel_id),
            streak=streak,
            puzzle_id=puzzle_id,
        )

        async with AsyncSession(engine) as session:
            logger.debug(f"Add WordleStreak {record.id} to database")
            logger.trace(f"{record=}\n{engine=}\n{session=}")

            session.add(record)

            await session.commit()
            await session.refresh(record)

            logger.success(
                f"Added WordleStreak {record.id} (puzzle {record.puzzle_id}) to database"
            )

            return record

    @staticmethod
    def _read_database(data: bytes) -> dict[str, list[tuple[Any, ...]]]:
        """Read the Wordle tables of an exported Astrocade database from its raw bytes."""
        if not data.startswith(b"SQLite format 3\x00"):
            raise InvalidDatabaseError("File is not a SQLite database.")

        queries: dict[str, str] = {
            "wordle_puzzles": "SELECT id, day, solution FROM wordle_puzzles",
            "wordle_results": "SELECT id, attempts, player_id, puzzle_id FROM wordle_results",
            "wordle_streaks": "SELECT id, channel_id, streak, puzzle_id FROM wordle_streaks",
        }
        optional: set[str] = {"wordle_streaks"}
        tables: dict[str, list[tuple[Any, ...]]] = {}
        connection: sqlite3.Connection = sqlite3.connect(":memory:")

        try:
            connection.deserialize(data)

            existing: set[str] = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            missing: list[str] = [
                table
                for table in queries
                if table not in existing and table not in optional
            ]

            if missing:
                raise InvalidDatabaseError(
                    f"Database is missing the {', '.join(missing)} table(s)."
                )

            for table, query in queries.items():
                tables[table] = (
                    connection.execute(query).fetchall() if table in existing else []
                )
        except sqlite3.DatabaseError as e:
            raise InvalidDatabaseError(f"Database could not be read ({e}).") from e
        finally:
            connection.close()

        logger.debug(
            f"Read {', '.join(f'{len(rows):,} {table}' for table, rows in tables.items())} from provided database"
        )

        return tables

    @staticmethod
    async def import_database(
        client: GatewayClient, data: bytes
    ) -> WordleImportSummary:
        """Merge the Wordle data of an exported Astrocade database into the database, keeping existing data."""
        source: dict[str, list[tuple[Any, ...]]] = await to_thread(
            WordleOps._read_database, data
        )
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)
        new_puzzles: list[WordlePuzzle] = []
        new_players: list[Player] = []
        new_results: list[WordleResult] = []
        new_streaks: list[WordleStreak] = []
        rejected: set[int] = set()
        duplicates: int = 0
        conflicts: int = 0

        async with AsyncSession(engine) as session:
            puzzles: dict[int, tuple[date, str]] = {
                id: (day, solution)
                for id, day, solution in (
                    await session.exec(
                        select(
                            col(WordlePuzzle.id),
                            col(WordlePuzzle.day),
                            col(WordlePuzzle.solution),
                        )
                    )
                ).all()
            }
            days: dict[date, int] = {day: id for id, (day, _) in puzzles.items()}
            players: set[int] = set((await session.exec(select(col(Player.id)))).all())
            results: dict[str, int] = {
                id: attempts
                for id, attempts in (
                    await session.exec(
                        select(col(WordleResult.id), col(WordleResult.attempts))
                    )
                ).all()
            }
            streaks: dict[int, tuple[int, int]] = {
                id: (channel_id, puzzle_id)
                for id, channel_id, puzzle_id in (
                    await session.exec(
                        select(
                            col(WordleStreak.id),
                            col(WordleStreak.channel_id),
                            col(WordleStreak.puzzle_id),
                        )
                    )
                ).all()
            }
            claims: set[tuple[int, int]] = set(streaks.values())

            logger.trace(f"{engine=}\n{session=}")

            for id, day, solution in source["wordle_puzzles"]:
                try:
                    day = date.fromisoformat(str(day))
                except ValueError:
                    day = None

                if (
                    not isinstance(id, int)
                    or not isinstance(solution, str)
                    or not solution
                    or day is None
                    or day != WordleOps.puzzle_day_for_id(id)
                ):
                    logger.warning(
                        f"Skipped invalid WordlePuzzle {id} during database import ({day=}, {solution=})"
                    )

                    rejected.add(id)
                    conflicts += 1

                    continue

                if existing := puzzles.get(id):
                    if existing == (day, solution):
                        duplicates += 1
                    else:
                        logger.warning(
                            f"Skipped WordlePuzzle {id} during database import, existing puzzle is {existing} but {(day, solution)} was imported"
                        )

                        rejected.add(id)
                        conflicts += 1

                    continue

                if day in days:
                    logger.warning(
                        f"Skipped WordlePuzzle {id} during database import, day {day} belongs to existing puzzle {days[day]}"
                    )

                    rejected.add(id)
                    conflicts += 1

                    continue

                puzzles[id] = (day, solution)
                days[day] = id

                new_puzzles.append(WordlePuzzle(id=id, day=day, solution=solution))

            for id, attempts, player_id, puzzle_id in source["wordle_results"]:
                if (
                    not isinstance(attempts, int)
                    or not isinstance(player_id, int)
                    or not isinstance(puzzle_id, int)
                    or id != f"{player_id}_{puzzle_id}"
                    or not 1 <= attempts <= 7
                    or puzzle_id in rejected
                    or puzzle_id not in puzzles
                ):
                    logger.warning(
                        f"Skipped invalid WordleResult {id} during database import ({attempts=}, {player_id=}, {puzzle_id=})"
                    )

                    conflicts += 1

                    continue

                if (existing := results.get(id)) is not None:
                    if existing == attempts:
                        duplicates += 1
                    else:
                        logger.warning(
                            f"Skipped WordleResult {id} during database import, existing result has {existing} attempts but {attempts} were imported"
                        )

                        conflicts += 1

                    continue

                if player_id not in players:
                    players.add(player_id)
                    new_players.append(Player(id=player_id))

                results[id] = attempts

                new_results.append(
                    WordleResult(
                        id=id,
                        attempts=attempts,
                        player_id=player_id,
                        puzzle_id=puzzle_id,
                    )
                )

            for id, channel_id, streak, puzzle_id in source["wordle_streaks"]:
                if (
                    not isinstance(id, int)
                    or not isinstance(channel_id, int)
                    or not isinstance(streak, int)
                    or not isinstance(puzzle_id, int)
                    or puzzle_id in rejected
                    or puzzle_id not in puzzles
                ):
                    logger.warning(
                        f"Skipped invalid WordleStreak {id} during database import ({channel_id=}, {streak=}, {puzzle_id=})"
                    )

                    conflicts += 1

                    continue

                if (existing := streaks.get(id)) == (channel_id, puzzle_id):
                    duplicates += 1

                    continue
                elif existing or (channel_id, puzzle_id) in claims:
                    logger.warning(
                        f"Skipped WordleStreak {id} during database import, conflicts with an existing streak for puzzle {puzzle_id} in channel {channel_id}"
                    )

                    conflicts += 1

                    continue

                streaks[id] = (channel_id, puzzle_id)
                claims.add((channel_id, puzzle_id))

                new_streaks.append(
                    WordleStreak(
                        id=id, channel_id=channel_id, streak=streak, puzzle_id=puzzle_id
                    )
                )

            session.add_all([*new_puzzles, *new_players])

            # Ensure referenced records exist before their dependents
            await session.flush()

            session.add_all([*new_results, *new_streaks])

            await session.commit()

        summary: WordleImportSummary = WordleImportSummary(
            puzzles=len(new_puzzles),
            players=len(new_players),
            results=len(new_results),
            streaks=len(new_streaks),
            duplicates=duplicates,
            conflicts=conflicts,
        )

        logger.success(f"Imported Wordle data from provided database: {summary}")

        return summary

    @staticmethod
    async def get_puzzle(
        client: GatewayClient, id: int, day: date | None, *, skip_create: bool = False
    ) -> WordlePuzzle | None:
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
                if skip_create:
                    logger.debug(f"Skipped Wordle puzzle creation for ID {id}")

                    return
                elif not day:
                    raise RuntimeError(
                        "Attempted to create a Wordle puzzle without a day"
                    )

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
    def resolve_season(value: str | None) -> Season | None:
        """Resolve a season option value. Defaults to the current season, None is All-Time."""
        if value is None:
            return Season.current()
        elif value.strip().lower() in (SEASON_ALL_TIME, "all-time", "all time"):
            return None
        elif season := Season.parse(value):
            return season

        raise InvalidSeasonError(value)

    @staticmethod
    def _season_filter(season: Season | None) -> ColumnElement[bool]:
        """Return a clause matching the WordlePuzzles within the given season. None is All-Time."""
        if not season:
            return true()

        return col(WordlePuzzle.day).between(season.start, season.end)

    @staticmethod
    def _stats_statement(season: Season | None) -> Select[*tuple[Any, ...]]:
        """Build a query of WordleStats per Player for the given season. None is All-Time."""
        attempts: Mapped[int] = col(WordleResult.attempts)
        points: dict[int, int] = {
            1: WordlePoints.ATTEMPTS_1,
            2: WordlePoints.ATTEMPTS_2,
            3: WordlePoints.ATTEMPTS_3,
            4: WordlePoints.ATTEMPTS_4,
            5: WordlePoints.ATTEMPTS_5,
            6: WordlePoints.ATTEMPTS_6,
        }

        # sqlmodel's select() is only typed for up to 4 columns
        return (
            Select(
                col(WordleResult.player_id),
                func.sum(case(points, value=attempts, else_=WordlePoints.FAIL)).label(
                    "points"
                ),
                func.sum(case(points, value=attempts, else_=0)).label("points_gross"),
                # Fails are excluded from the average as NULL
                func.coalesce(func.round(func.avg(case((attempts != 7, attempts)))), 0)
                .cast(Integer)
                .label("average_attempts"),
                func.count().label("completions"),
                func.sum(case((attempts == 7, 1), else_=0)).label("fails"),
                func.sum(case((attempts == 1, 1), else_=0)).label("aces"),
            )
            .join(WordlePuzzle, col(WordlePuzzle.id) == col(WordleResult.puzzle_id))
            .where(WordleOps._season_filter(season))
            .group_by(col(WordleResult.player_id))
        )

    @staticmethod
    async def get_player_stats(
        client: GatewayClient, player_id: int, season: Season | None
    ) -> WordleStats | None:
        """Get the WordleStats of the given Player for the given season. None is All-Time."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)

        async with AsyncSession(engine) as session:
            statement: Select[*tuple[Any, ...]] = WordleOps._stats_statement(
                season
            ).where(col(WordleResult.player_id) == player_id)

            logger.debug(
                f"Find {season_label(season)} WordleStats for player {player_id}"
            )
            logger.trace(f"{statement=}\n{engine=}\n{session=}")

            row: Any = (await session.exec(statement)).first()

            return WordleStats._make(row) if row else None

    @staticmethod
    async def get_leaderboard(
        client: GatewayClient,
        category: WordleLeaderboardType,
        season: Season | None,
        limit: int | None,
        direction: str,
    ) -> list[WordleStats]:
        """Get the top Players' WordleStats for the given season, ordered by the given category. A limit of None returns all Players."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)
        statement: Select[*tuple[Any, ...]] = WordleOps._stats_statement(season)
        stats = statement.selected_columns
        primary: ColumnElement[int] = stats[LEADERBOARDS[category][1]]

        # Columns to order by, and whether each is ordered opposite to the direction
        order: list[tuple[ColumnElement[int], bool]] = [(primary, False)]

        if category == WordleLeaderboardType.POINTS:
            # Tiebreakers: Gross Points -> Average Attempts
            order += [(stats.points_gross, False), (stats.average_attempts, True)]

        ascending: bool = direction == Direction.ASCENDING
        statement = (
            statement.having(
                primary != 0
                if category == WordleLeaderboardType.POINTS
                else primary > 0
            )
            .order_by(
                *(
                    column.asc() if ascending != inverted else column.desc()
                    for column, inverted in order
                )
            )
            .limit(limit)
        )

        async with AsyncSession(engine) as session:
            logger.trace(f"{statement=}\n{engine=}\n{session=}")

            results: Sequence[Any] = (await session.exec(statement)).all()
            players: list[WordleStats] = [WordleStats._make(row) for row in results]

            logger.debug(
                f"Found {len(players):,} players for {season_label(season)} Wordle {category} leaderboard"
            )
            logger.trace(f"{players=}")

            return players

    @staticmethod
    async def get_leaderboard_message(
        category: WordleLeaderboardType,
        limit: int,
        direction: str,
        season: Season | None,
    ) -> ContainerComponentBuilder:
        """Get the formatted leaderboard message for the given category and season."""
        title: str
        unit: str
        label: str
        caption: str
        players: list[tuple[int, int]]  # (player_id, value)

        if category == WordleLeaderboardType.SEASONAL_WINS:
            # Seasonal wins span every completed season, regardless of the selected season
            seasons: list[Season] = await WordleOps.get_completed_seasons(plugin.client)

            if not seasons:
                return Templates.generic(
                    TemplateType.INFO, "No Wordle seasons have been completed yet."
                )

            title, unit, label = "Wordle Seasonal Wins Leaderboard", "wins", category
            caption = f"All-Time · **{len(seasons):,}** completed season{'' if len(seasons) == 1 else 's'}"
            players = await WordleOps.get_leaderboard_wins(
                plugin.client, seasons, limit, direction
            )
        else:
            title, field, unit = LEADERBOARDS[category]
            label, caption = season_label(season), season_caption(season)
            players = [
                (stats.player_id, getattr(stats, field))
                for stats in await WordleOps.get_leaderboard(
                    plugin.client, category, season, limit, direction
                )
            ]

        if not players:
            return Templates.generic(
                TemplateType.INFO,
                f"Not enough player data available to populate the {label} leaderboard.",
            )

        board: str = f"## {title}"
        board += f"\n-# {caption}"

        for pos, (player_id, value) in enumerate(players, start=1):
            board += f"\n{pos}. <@{player_id}>: **{value:,}** {unit}"

        return Templates.generic_thumb(TemplateType.SUCCESS, board, WORDLE_ICON)

    @staticmethod
    async def get_completed_seasons(client: GatewayClient) -> list[Season]:
        """Get the seasons that have ended since the earliest WordlePuzzle, newest first."""
        first: date | None = await WordleOps.get_first_puzzle_day(client)
        now: date = today()

        if not first:
            return []

        return [
            season for season in Season.span(min(first, now), now) if season.end < now
        ]

    @staticmethod
    async def get_season_winners(client: GatewayClient, season: Season) -> list[int]:
        """Get the Players in first place of the given season's points leaderboard, sharing it when tied on every tiebreaker."""
        standings: list[WordleStats] = await WordleOps.get_leaderboard(
            client, WordleLeaderboardType.POINTS, season, None, Direction.DESCENDING
        )

        def placement(stats: WordleStats) -> tuple[int, int, int]:
            """Return the values that rank a Player on the points leaderboard."""
            return (stats.points, stats.points_gross, stats.average_attempts)

        winners: list[int] = [
            stats.player_id
            for stats in standings
            if placement(stats) == placement(standings[0])
        ]

        logger.debug(f"Found {len(winners):,} winners of {season.label}")
        logger.trace(f"{winners=}")

        return winners

    @staticmethod
    async def get_leaderboard_wins(
        client: GatewayClient, seasons: list[Season], limit: int, direction: str
    ) -> list[tuple[int, int]]:
        """Get the top Players ordered by their count of wins across the given seasons, as (player_id, wins)."""
        wins: Counter[int] = Counter()

        for season in seasons:
            wins.update(await WordleOps.get_season_winners(client, season))

        players: list[tuple[int, int]] = sorted(
            wins.items(),
            key=lambda entry: entry[1],
            reverse=direction != Direction.ASCENDING,
        )[:limit]

        logger.debug(
            f"Found {len(players):,} players for Wordle seasonal wins leaderboard"
        )
        logger.trace(f"{players=}")

        return players

    @staticmethod
    async def count_results(client: GatewayClient) -> int:
        """Count the number of WordleResults in the Astrocade database."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)

        async with AsyncSession(engine) as session:
            statement: SelectOfScalar[int] = select(func.count()).select_from(
                WordleResult
            )

            logger.debug("Counting WordleResults in database")
            logger.trace(f"{statement=}\n{engine=}\n{session=}")

            results: ScalarResult[int] = await session.exec(statement)
            count: int = results.one()

            logger.debug(f"Found {count} WordleResults in database")

            return count

    @staticmethod
    async def count_puzzles(client: GatewayClient, season: Season | None = None) -> int:
        """Count the number of WordlePuzzles in the given season. None is All-Time."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)

        async with AsyncSession(engine) as session:
            statement: SelectOfScalar[int] = (
                select(func.count())
                .select_from(WordlePuzzle)
                .where(WordleOps._season_filter(season))
            )

            logger.debug(f"Counting {season_label(season)} WordlePuzzles in database")
            logger.trace(f"{statement=}\n{engine=}\n{session=}")

            results: ScalarResult[int] = await session.exec(statement)
            count: int = results.one()

            logger.debug(f"Found {count} WordlePuzzles in database")

            return count

    @staticmethod
    async def get_first_puzzle_day(client: GatewayClient) -> date | None:
        """Get the day of the earliest WordlePuzzle in the Astrocade database."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)

        async with AsyncSession(engine) as session:
            statement: SelectOfScalar[date | None] = select(
                func.min(col(WordlePuzzle.day))
            )

            logger.trace(f"{statement=}\n{engine=}\n{session=}")

            results: ScalarResult[date | None] = await session.exec(statement)

            return results.one()

    @staticmethod
    async def count_missing_puzzles(
        client: GatewayClient, player_id: int, season: Season | None = None
    ) -> int:
        """Count the WordlePuzzles in the given season the given Player has not completed. None is All-Time."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)

        async with AsyncSession(engine) as session:
            statement: SelectOfScalar[int] = (
                select(func.count())
                .select_from(WordlePuzzle)
                .where(
                    col(WordlePuzzle.id).not_in(
                        select(WordleResult.puzzle_id).where(
                            WordleResult.player_id == player_id
                        )
                    )
                )
                .where(WordleOps._season_filter(season))
            )

            logger.debug(
                f"Counting missing WordlePuzzles for player {player_id} in database"
            )
            logger.trace(f"{statement=}\n{engine=}\n{session=}")

            results: ScalarResult[int] = await session.exec(statement)
            count: int = results.one()

            logger.debug(f"Found {count} missing WordlePuzzles for player {player_id}")

            return count

    @staticmethod
    async def get_missing_puzzles(
        client: GatewayClient, player_id: int, limit: int, season: Season | None = None
    ) -> list[WordlePuzzle]:
        """Get the WordlePuzzles in the given season the given Player has not completed, most recent first. None is All-Time."""
        engine: AsyncEngine = client.get_type_dependency(AsyncEngine)
        puzzles: list[WordlePuzzle] = []

        async with AsyncSession(engine) as session:
            statement: SelectOfScalar[WordlePuzzle] = (
                select(WordlePuzzle)
                .where(
                    col(WordlePuzzle.id).not_in(
                        select(WordleResult.puzzle_id).where(
                            WordleResult.player_id == player_id
                        )
                    )
                )
                .where(WordleOps._season_filter(season))
                .order_by(col(WordlePuzzle.id).desc())
                .limit(limit)
            )

            logger.debug(
                f"Find missing WordlePuzzles for player {player_id} in database"
            )
            logger.trace(f"{statement=}\n{engine=}\n{session=}")

            results: ScalarResult[WordlePuzzle] = await session.exec(statement)
            puzzles = list(results.all())

            logger.debug(
                f"Found {len(puzzles):,} missing WordlePuzzles for player {player_id}"
            )
            logger.trace(f"{puzzles=}")

            return puzzles

    @staticmethod
    async def _get_puzzle_metadata(day: date) -> dict[str, Any]:
        """Fetch Wordle puzzle metadata from NYT for the given day."""
        res: Response = await niquests.aget(
            f"https://www.nytimes.com/svc/wordle/v2/{day.strftime('%Y-%m-%d')}.json"
        )

        logger.debug(f"HTTP {res.status_code} GET {res.url}")
        logger.trace(f"{res=} {res.text=}")

        return res.json()

    @staticmethod
    def puzzle_id_for_day(day: date) -> int:
        """Return the Wordle puzzle ID for a given day (days since launch)."""
        return (day - WORDLE_EPOCH).days

    @staticmethod
    def puzzle_day_for_id(id: int) -> date:
        """Return the day of a given Wordle puzzle ID (days since launch)."""
        return WORDLE_EPOCH + timedelta(days=id)

    @staticmethod
    async def read_results_image(msg: Message) -> int | None:
        """Read the puzzle ID from the results image attached to a Wordle streak message."""
        for attachment in msg.attachments:
            if attachment.size > MAX_RESULTS_IMAGE_BYTES:
                logger.debug(
                    f"Skipped attachment {attachment.filename} of message {msg.id}, too large ({attachment.size:,} bytes)"
                )

                continue
            elif attachment.media_type and not attachment.media_type.startswith(
                "image/"
            ):
                logger.debug(
                    f"Skipped attachment {attachment.filename} of message {msg.id}, not an image ({attachment.media_type})"
                )

                continue

            try:
                data: bytes = await attachment.read()
            except Exception as e:
                logger.opt(exception=e).warning(
                    f"Failed to download attachment {attachment.filename} of Wordle streak message {msg.id}"
                )

                continue

            if (puzzle_id := await to_thread(OCR.read_wordle_number, data)) is not None:
                logger.debug(
                    f"Read puzzle {puzzle_id} from results image {attachment.filename} of Wordle streak message {msg.id}"
                )

                return puzzle_id

        logger.warning(
            f"Failed to read the puzzle number from the results image of Wordle streak message {msg.id}"
        )
        logger.debug(f"{msg.attachments=}")

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
    async def get_points(attempts: int) -> int:
        """Return the points value for a given number of attempts."""
        match attempts:
            case 1:
                return WordlePoints.ATTEMPTS_1
            case 2:
                return WordlePoints.ATTEMPTS_2
            case 3:
                return WordlePoints.ATTEMPTS_3
            case 4:
                return WordlePoints.ATTEMPTS_4
            case 5:
                return WordlePoints.ATTEMPTS_5
            case 6:
                return WordlePoints.ATTEMPTS_6
            case _:  # "X"
                return WordlePoints.FAIL

    @staticmethod
    async def import_data(
        client: GatewayClient,
        msg: Message,
        *,
        guild_id: Snowflake | None = None,
        event: bool = False,
    ) -> WordleResult | list[WordleResult] | None:
        """Import Wordle data from a Discord message."""
        if not msg.author.is_bot:
            logger.debug(
                f"Skipped message {msg.id} by {msg.author.id}, author is not a bot"
            )
            logger.trace(f"{msg=}")

            return
        elif msg.author.id != WORDLE_BOT_ID:
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
            client, msg, guild_id=guild_id, event=event
        ) or await WordleOps._import_share(client, msg, guild_id=guild_id, event=event)

    @staticmethod
    async def _import_streak(
        client: GatewayClient,
        msg: Message,
        *,
        guild_id: Snowflake | None = None,
        event: bool = False,
    ) -> WordleResult | list[WordleResult] | None:
        """Import Wordle data from a streak Discord message."""
        header: Match[str] | None = (
            re.search(REGEX_WORDLE_STREAK, msg.content) if msg.content else None
        )

        if not msg.content or not header:
            if (
                msg.content
                and re.search(REGEX_WORDLE_STREAK_ATTEMPT, msg.content)
                and re.search(REGEX_WORDLE_STREAK_TAG, msg.content)
            ):
                logger.warning(
                    f"Skipped message {msg.id} by {msg.author.id}, contains Wordle results but no streak header (unrecognized format)"
                )
                logger.debug(f"{msg.content=}")
            else:
                logger.debug(
                    f"Skipped message {msg.id} by {msg.author.id}, not a streak"
                )
                logger.trace(f"{msg=}")

            return

        streak: int = int(header.group(1).replace(",", ""))
        server: int | None = msg.guild_id

        if not server and guild_id:
            server = int(guild_id)

        logger.debug(f"Wordle streak message {msg.id} has streak {streak:,}")

        entries: dict[int, int] = await WordleOps._parse_streak(client, msg, server)

        if not entries:
            logger.warning(
                f"Skipped Wordle streak message {msg.id}, no player results found"
            )
            logger.debug(f"{msg.content=}")

            return

        puzzle_id: int | None = await WordleOps._resolve_streak_puzzle(
            client, msg, streak, entries
        )

        if puzzle_id is None:
            return

        puzzle: WordlePuzzle | None = await WordleOps.get_puzzle(
            client, puzzle_id, WordleOps.puzzle_day_for_id(puzzle_id)
        )

        if not puzzle:
            raise RuntimeError(
                f"Cannot import Wordle streak message {msg.id} without puzzle {puzzle_id} data"
            )

        await WordleOps.add_streak(client, msg, streak, puzzle.id)

        results: list[WordleResult] = []

        for player_id, attempts in entries.items():
            player: Player = await Database.get_player(client, player_id)
            result: WordleResult | None = await WordleOps.add_result(
                client, attempts, player.id, puzzle.id
            )

            if result:
                results.append(result)

                logger.success(f"Created placement for {player.id}")
                logger.debug(f"{result=}")

        if results and event:
            await client.rest.create_message(
                msg.channel_id,
                component=await WordleOps.get_leaderboard_message(
                    WordleLeaderboardType.POINTS,
                    10,
                    Direction.DESCENDING,
                    # Streaks report yesterday's results, which may close out a season
                    Season.for_day(puzzle.day),
                ),
                reply=msg.id,
            )

        return results

    @staticmethod
    async def _parse_streak(
        client: GatewayClient, msg: Message, server: int | None
    ) -> dict[int, int]:
        """Parse the player results of a Wordle streak message as Discord User ID to attempts."""
        entries: dict[int, int] = {}
        named: dict[str, int] = {}

        for line in (msg.content or "").splitlines():
            attempt: Match[str] | None = re.search(REGEX_WORDLE_STREAK_ATTEMPT, line)

            if not attempt:
                logger.debug(
                    f"Skipped line in message {msg.id} by {msg.author.id}, not an attempt"
                )
                logger.trace(f"{line=}")

                continue

            attempts: int = (
                7 if (attempts_raw := attempt.group(1)) == "X" else int(attempts_raw)
            )

            for user_id, name in re.findall(
                REGEX_WORDLE_STREAK_TAG, line[attempt.end() :]
            ):
                if user_id:
                    entries[int(user_id)] = attempts
                else:
                    # Players without a mention are listed by display name
                    named[WordleOps._unescape(name)] = attempts

        if named:
            resolved: dict[str, int] = await WordleOps._resolve_streak_names(
                client, msg, server, list(named)
            )

            for name, attempts in named.items():
                if (user_id := resolved.get(name)) is None:
                    continue
                elif user_id in entries:
                    logger.warning(
                        f"Skipped tag {name} in Wordle streak message {msg.id}, resolved to {user_id} who already has a result in it"
                    )
                    logger.debug(f"{entries=} {resolved=}")

                    continue

                entries[user_id] = attempts

        logger.trace(f"{entries=}")

        return entries

    @staticmethod
    def _unescape(text: str) -> str:
        """Remove Discord markdown escapes from the provided text."""
        return re.sub(REGEX_MARKDOWN_ESCAPE, r"\1", text).strip()

    @staticmethod
    async def _resolve_streak_names(
        client: GatewayClient, msg: Message, server: int | None, names: list[str]
    ) -> dict[str, int]:
        """
        Resolve the display names in a Wordle streak message to Discord User IDs.

        Players who are not mentioned are listed by the display name they had when they
        played, which may since have changed. Names are first matched against the Wordle
        Activity's own "Name was playing" messages that precede the streak message, as
        those pair the name with the Discord User that launched the Activity. Remaining
        names are matched against the current members of the server, and only accepted
        when exactly one member matches.
        """
        resolved: dict[str, int] = await WordleOps._get_activity_names(
            client, msg, set(names)
        )

        for name, user_id in resolved.items():
            logger.debug(
                f"Resolved tag {name} in Wordle streak message {msg.id} to {user_id} via Wordle Activity messages"
            )

        unresolved: list[str] = [name for name in names if name not in resolved]

        if not unresolved:
            return resolved
        elif not server:
            logger.error(
                f"Cannot resolve tags {', '.join(unresolved)} in Wordle streak message {msg.id}, server is null"
            )
            logger.debug(f"{msg=}")

            return resolved

        members: Sequence[Member] = await client.rest.fetch_members(server)

        for name in unresolved:
            matches: set[int] = WordleOps._match_member(name, members)

            if len(matches) == 1:
                resolved[name] = matches.pop()

                logger.debug(
                    f"Resolved tag {name} in Wordle streak message {msg.id} to {resolved[name]} via members of server {server}"
                )
            elif matches:
                logger.error(
                    f"Failed to resolve tag {name} in Wordle streak message {msg.id}, ambiguous between members {', '.join(str(m) for m in matches)} of server {server}"
                )
            else:
                logger.error(
                    f"Failed to resolve tag {name} in Wordle streak message {msg.id} to a member of server {server}"
                )

        return resolved

    @staticmethod
    def _match_member(name: str, members: Sequence[Member]) -> set[int]:
        """
        Find the members that a display name refers to.

        Returns the matches of the strictest criteria that matches anyone: the current
        display name, then any nickname, global name, or username, then the same
        ignoring case. More than one match means the name is ambiguous.
        """
        folded: str = name.casefold()
        tiers: list[set[int]] = [set(), set(), set()]

        for member in members:
            aliases: tuple[str, ...] = tuple(
                alias
                for alias in (member.nickname, member.global_name, member.username)
                if alias
            )

            if member.display_name == name:
                tiers[0].add(int(member.id))

            if name in aliases:
                tiers[1].add(int(member.id))

            if folded in (alias.casefold() for alias in aliases):
                tiers[2].add(int(member.id))

        return next((matches for matches in tiers if matches), set())

    @staticmethod
    async def _get_activity_names(
        client: GatewayClient, msg: Message, names: set[str]
    ) -> dict[str, int]:
        """
        Map display names to Discord User IDs using Wordle Activity session messages.

        The Wordle Activity posts "Name was playing" when a player launches it, with
        that player as the interaction user. Session messages preceding the provided
        message are scanned newest first until every name is found, or the lookback
        window ends. Two-player sessions ("A and B were playing") name the launcher
        as well, so they identify the other player once one name is known.
        """
        singles: dict[str, int] = {}
        pairs: list[tuple[str, str, int]] = []
        found: dict[str, int] = {}
        oldest: datetime = msg.created_at - WORDLE_NAME_LOOKBACK
        scanned: int = 0

        async for prior in client.rest.fetch_messages(msg.channel_id, before=msg.id):
            if prior.created_at < oldest:
                break

            scanned += 1

            if (
                prior.author.id != WORDLE_BOT_ID
                or not prior.content
                or not prior.interaction_metadata
            ):
                continue

            playing: Match[str] | None = re.fullmatch(
                REGEX_WORDLE_PLAYING, prior.content.strip()
            )

            if not playing:
                continue

            launcher: int = int(prior.interaction_metadata.user.id)
            players: str = playing.group("players")

            if playing.group("verb") in ("was", "is"):
                # Newest first, so the most recent use of a name takes precedence
                singles.setdefault(WordleOps._unescape(players), launcher)
            elif not re.search(REGEX_WORDLE_PLAYING_OTHERS, players):
                pair: list[str] = re.split(REGEX_WORDLE_PLAYING_SEPARATOR, players)

                if len(pair) == 2:
                    pairs.append(
                        (
                            WordleOps._unescape(pair[0]),
                            WordleOps._unescape(pair[1]),
                            launcher,
                        )
                    )

            found = WordleOps._derive_activity_names(singles, pairs)

            if names <= found.keys():
                break

        logger.debug(
            f"Scanned {scanned:,} messages preceding Wordle streak message {msg.id} for Wordle Activity names"
        )
        logger.trace(f"{singles=} {pairs=}")

        return {name: found[name] for name in names if name in found}

    @staticmethod
    def _derive_activity_names(
        singles: dict[str, int], pairs: list[tuple[str, str, int]]
    ) -> dict[str, int]:
        """
        Combine single and two-player Wordle Activity sessions into a name to ID map.

        The launcher of a two-player session is one of its two names, so when one name
        is known to belong to someone else, the other name belongs to the launcher.
        """
        names: dict[str, int] = dict(singles)
        changed: bool = True

        while changed:
            changed = False

            for first, second, launcher in pairs:
                for name, other in ((first, second), (second, first)):
                    if name in names or other not in names:
                        continue
                    elif names[other] != launcher:
                        names[name] = launcher
                        changed = True

        return names

    @staticmethod
    async def _resolve_streak_puzzle(
        client: GatewayClient, msg: Message, streak: int, entries: dict[int, int]
    ) -> int | None:
        """
        Determine the ID of the Wordle puzzle that a streak message reports results for.

        The message text does not state its puzzle, and its arrival time drifts with the
        time zone of whichever player starts the puzzle first. In order of preference, the
        puzzle is read from the attached results image ("Wordle No. {id}"), or derived from
        the nearest recorded streak message in the channel, as the group streak counter
        increments by exactly one per day. The message timestamp is only used as a
        plausibility window, and as a fallback when neither is usable.
        """
        if existing := await WordleOps.get_streak(client, msg.id):
            logger.debug(
                f"Wordle streak message {msg.id} was previously attributed to puzzle {existing.puzzle_id}"
            )

            return existing.puzzle_id

        preceding, following = await WordleOps.get_adjacent_streaks(
            client, msg.channel_id, msg.id
        )
        created: datetime = msg.created_at.astimezone(TZ)
        latest: int = WordleOps.puzzle_id_for_day(created.date())
        window: range = range(latest - 2, latest + 1)

        anchors: list[tuple[int, str]] = []

        if preceding and streak > preceding.streak:
            anchors.append(
                (
                    preceding.puzzle_id + (streak - preceding.streak),
                    f"previous streak message {preceding.id}",
                )
            )

        if following and following.streak > streak:
            anchors.append(
                (
                    following.puzzle_id - (following.streak - streak),
                    f"following streak message {following.id}",
                )
            )

        # Candidates as (puzzle ID, source, trusted), in order of preference
        candidates: list[tuple[int, str, bool]] = []

        if (image_id := await WordleOps.read_results_image(msg)) is not None:
            candidates.append((image_id, "results image", True))

            for anchor_id, source in anchors:
                if anchor_id != image_id:
                    logger.warning(
                        f"Wordle streak message {msg.id} results image reads puzzle {image_id}, but {source} implies puzzle {anchor_id}"
                    )

        candidates.extend((anchor_id, source, True) for anchor_id, source in anchors)

        # Fallback: "Here are yesterday's results", favoring two days prior before cutoff
        for offset in (2, 1, 0) if created.hour < WORDLE_CUTOFF_HOUR else (1, 0, 2):
            candidates.append((latest - offset, "message timestamp", False))

        claimed: set[int] = await WordleOps.get_claimed_puzzles(
            client, msg.channel_id, window
        )
        rejected: list[str] = []
        seen: set[int] = set()

        for puzzle_id, source, trusted in candidates:
            if puzzle_id in seen:
                continue

            seen.add(puzzle_id)

            reason: str | None = None

            if puzzle_id not in window:
                reason = f"outside plausible range {window.start}-{window.stop - 1}"
            elif preceding and puzzle_id <= preceding.puzzle_id:
                reason = f"not after previous streak puzzle {preceding.puzzle_id}"
            elif following and puzzle_id >= following.puzzle_id:
                reason = f"not before following streak puzzle {following.puzzle_id}"
            elif puzzle_id in claimed:
                reason = "already claimed by another streak message"
            elif conflicts := await WordleOps.get_result_conflicts(
                client, puzzle_id, entries
            ):
                reason = f"conflicts with existing results for players {', '.join(str(c) for c in conflicts)}"

            if reason:
                rejected.append(f"{puzzle_id} (from {source}): {reason}")

                logger.debug(
                    f"Rejected puzzle {puzzle_id} for Wordle streak message {msg.id}, {reason}"
                )

                continue

            if trusted and not rejected:
                logger.debug(
                    f"Attributed Wordle streak message {msg.id} to puzzle {puzzle_id} (from {source})"
                )
            else:
                logger.warning(
                    f"Attributed Wordle streak message {msg.id} to puzzle {puzzle_id} (from {source}), verify this is correct"
                )
                logger.debug(
                    f"{rejected=} {created=} {streak=} {preceding=} {following=}"
                )

            return puzzle_id

        logger.error(
            f"Failed to determine Wordle puzzle for streak message {msg.id}, no results were imported. Rejected candidates: {'; '.join(rejected)}"
        )
        logger.debug(f"{created=} {streak=} {preceding=} {following=} {entries=}")

    @staticmethod
    async def _import_share(
        client: GatewayClient,
        msg: Message,
        *,
        guild_id: Snowflake | None = None,
        event: bool = False,
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

        match: Match[str] | None = re.search(REGEX_WORDLE_SHARE, text.content)

        if not match:
            logger.debug(
                f"Skipped message {msg.id} by {msg.author.id}, text is not a share"
            )
            logger.trace(f"{msg=}\n{text.content=}")

            return

        puzzle_id: int = int(match.group(1))
        attempts: int = (
            7 if (attempts_raw := match.group(2)) == "X" else int(attempts_raw)
        )
        user: int | None = None

        if isinstance(msg.interaction_metadata, CommandInteractionMetadata):
            if isinstance(msg.interaction_metadata.user, User):
                user = msg.interaction_metadata.user.id

        if not user:
            raise RuntimeError(f"Failed to determine User ID for Wordle share {msg.id}")

        player: Player = await Database.get_player(client, user)
        puzzle: WordlePuzzle | None = await WordleOps.get_puzzle(
            client, puzzle_id, msg.created_at.date()
        )

        if not puzzle:
            raise RuntimeError(
                f"Cannot import WordleResult {player.id}_{puzzle_id} without puzzle data"
            )

        return await WordleOps.add_result(client, attempts, player.id, puzzle.id)

    @staticmethod
    async def get_definition(word: str) -> str | None:
        """Query the Merriam-Webster Dictionary API for a word definition."""
        if not DICTIONARY_API_KEY:
            logger.debug(
                f"Cannot fetch definition for {word} without a Dictionary API Key"
            )

            return

        res: Response = await niquests.aget(
            f"https://www.dictionaryapi.com/api/v3/references/collegiate/json/{word}?key={DICTIONARY_API_KEY}"
        )

        logger.debug(f"HTTP {res.status_code} GET {res.url}")
        logger.trace(f"{res=} {res.text=}")

        data: list[dict[str, Any]] = res.json()

        logger.trace(f"{data=}")

        if not data:
            raise RuntimeError(f"No data returned from Dictionary API for {word}")

        try:
            entry: dict[str, Any] = data[0]

            if not entry.get("shortdef", []):
                raise RuntimeError(f"No definition found for {word} in Dictionary API")

            return entry["shortdef"][0]
        except Exception as e:
            logger.opt(exception=e).error(f"Failed to fetch definition for {word}")
            logger.trace(f"{res=} {data=} {entry=}")
