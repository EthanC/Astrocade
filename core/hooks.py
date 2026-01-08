"""Module containing lifecycle and command hooks."""

import arc
from arc import (
    GatewayClient,
    GatewayContext,
    InvokerMissingPermissionsError,
    NotOwnerError,
)
from hikari import Activity, ActivityType, Status
from loguru import logger

from core.database import Database
from core.templates import Templates, TemplateType


class Hooks:
    """Reusable hooks for the Astrocade Discord bot."""

    @staticmethod
    async def client_startup(client: GatewayClient) -> None:
        """Handle client startup."""
        Tasks.presence.start(client)

    @staticmethod
    async def client_shutdown(_client: GatewayClient) -> None:
        """Handle client shutdown."""
        Tasks.presence.cancel()

    @staticmethod
    async def command_use(ctx: GatewayContext) -> None:
        """Handle command pre-execution."""
        logger.info(f"Command used by {ctx.user.display_name} in {ctx.channel.name}")

    @staticmethod
    async def command_error(ctx: GatewayContext, error: Exception) -> None:
        """Handle uncaught command exceptions."""
        if isinstance(error, (NotOwnerError, InvokerMissingPermissionsError)):
            await ctx.respond(
                component=Templates.reply(
                    "You don't have permission to use this command.", TemplateType.ERROR
                )
            )

            return

        logger.opt(exception=error).error("An unexpected error occurred in command")

        await ctx.respond(
            component=Templates.reply(
                "An unexpected error occurred. Try again later.", TemplateType.ERROR
            )
        )


class Tasks:
    """Recurring tasks for the Astrocade Discord bot."""

    @arc.utils.interval_loop(minutes=1)
    async def presence(client: GatewayClient) -> None:
        """Set the bot status to display the latest Wordle data counts."""
        await client.app.update_presence(
            status=Status.ONLINE,
            activity=Activity(
                name=f"Tracking Stats for {await Database.count_players(client):,} users",
                type=ActivityType.WATCHING,
            ),
        )
