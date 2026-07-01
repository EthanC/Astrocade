"""Extension containing administrative commands."""

from datetime import UTC, datetime
from pathlib import Path

import arc
import hikari
from arc import AutodeferMode, GatewayClient, GatewayContext, GatewayPluginBase
from hikari import Permissions
from loguru import logger

from core.consts import DATABASE_PATH
from core.hooks import Hooks
from core.templates import Templates, TemplateType

plugin: GatewayPluginBase = GatewayPluginBase("admin")


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


@plugin.include
@arc.with_hook(arc.has_permissions(Permissions.ADMINISTRATOR))
@arc.with_hook(Hooks.command_use)
@arc.slash_command("export", "Export of the database of this Astrocade instance.")
async def command_export(ctx: GatewayContext) -> None:
    """Handle the /export command."""
    database: Path = DATABASE_PATH.resolve()

    if not database.is_file():
        logger.error(
            f"Attempted to export the database, but the file ({database}) does not exist"
        )

        await ctx.respond(
            component=Templates.generic(
                TemplateType.ERROR, "Failed to locate the database."
            )
        )

        return

    logger.info(f"Exported database {database.name} for {ctx.user.display_name}")

    await ctx.respond(
        attachment=hikari.File(
            database,
            filename=f"{database.stem}_{int(datetime.now(UTC).timestamp())}{database.suffix}",
        )
    )
