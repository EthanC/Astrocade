"""Entrypoint for the Astrocade Discord bot."""

import asyncio
import logging
from os import name as OS_NAME
from pathlib import Path
from sys import stdout
from typing import Final

from arc import GatewayClient
from environs import env
from hikari import (
    Activity,
    ActivityType,
    ApplicationContextType,
    GatewayBot,
    Intents,
    Permissions,
    Status,
)
from loguru import logger
from loguru_discord import DiscordSink
from loguru_discord.intercept import Intercept

from core.database import AsyncEngine, Database
from core.hooks import Hooks


async def start() -> None:
    """Initialize the Astrocade Discord bot."""
    logger.info("Astrocade")
    logger.info("https://github.com/EthanC/Astrocade")

    if env.read_env(recurse=False):
        logger.success("Loaded environment variables")

    if log_level := env.str("LOG_LEVEL", default=None):
        logger.remove()
        logger.add(stdout, level=log_level)

        logger.success(f"Set console logging level to {log_level}")

    is_debug: Final[bool] = log_level in {"DEBUG", "TRACE"}

    Intercept.setup({"TRACE_HIKARI": "TRACE"})

    if log_url := env.str("LOG_DISCORD_WEBHOOK_URL", default=None):
        logger.add(
            DiscordSink(log_url),
            level=env.str("LOG_DISCORD_WEBHOOK_LEVEL", default="WARNING"),
            backtrace=False,
        )

        logger.success("Enabled logging to Discord webhook")

    # Replace default asyncio event loop with libuv on UNIX
    # https://github.com/hikari-py/hikari#uvloop
    if OS_NAME != "nt":
        try:
            import uvloop  # type: ignore

            uvloop.install()

            logger.success("Installed libuv event loop")
        except Exception as e:
            logger.opt(exception=e).debug("Defaulted to asyncio event loop")

    database: AsyncEngine = await Database.setup(
        env.path("DATABASE_PATH", default=Path("./astrocade.db"))
    )
    bot: GatewayBot = GatewayBot(
        env.str("DISCORD_BOT_TOKEN"),
        allow_color=False,
        banner=None,
        suppress_optimization_warning=is_debug,
        intents=Intents.GUILD_MESSAGES
        | Intents.MESSAGE_CONTENT
        | Intents.GUILD_PRESENCES,
    )
    client: GatewayClient = GatewayClient(
        bot,
        default_enabled_guilds=env.list(
            "DISCORD_GUILD_IDS", default=[], subcast=int, delimiter=","
        ),
        default_permissions=Permissions.VIEW_CHANNEL
        | Permissions.READ_MESSAGE_HISTORY
        | Permissions.SEND_MESSAGES,
        invocation_contexts=[ApplicationContextType.GUILD],
    )

    client.set_type_dependency(AsyncEngine, database)
    client.set_type_dependency(GatewayBot, bot)
    client.set_type_dependency(GatewayClient, client)

    client.load_extensions_from("extensions")

    client.add_startup_hook(Hooks.client_startup)
    client.add_shutdown_hook(Hooks.client_shutdown)

    try:
        await bot.start(
            activity=Activity(name="Pardon our space dust", type=ActivityType.WATCHING),
            check_for_updates=False,
            status=Status.DO_NOT_DISTURB,
        )
        await bot.join()
    finally:
        await Database.close(database)


if __name__ == "__main__":
    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.opt(exception=e).critical("Fatal error occurred during runtime")
