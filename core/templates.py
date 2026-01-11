"""Reusable message templates."""

from enum import Enum, auto
from pathlib import Path
from typing import Self

from hikari import Color
from hikari.impl import (
    ContainerComponentBuilder,
    SectionComponentBuilder,
    TextDisplayComponentBuilder,
    ThumbnailComponentBuilder,
)

from core.consts import Colors


def percentage(part: int | float, whole: int | float) -> int:
    """Calculate the percentage of part relative to whole, clamped between 0 and 100."""
    if whole == 0:
        return 0

    raw = int((part / whole) * 100)

    return max(0, min(raw, 100))


class TemplateType(Enum):
    """Enumeration of template types."""

    INFO = auto()
    WARN = auto()
    ERROR = auto()
    SUCCESS = auto()

    @property
    def color(self: Self) -> Color:
        """Return the color associated with the template type."""
        return {
            TemplateType.INFO: Color.from_hex_code(Colors.BLURPLE),
            TemplateType.WARN: Color.from_hex_code(Colors.YELLOW),
            TemplateType.ERROR: Color.from_hex_code(Colors.RED),
            TemplateType.SUCCESS: Color.from_hex_code(Colors.GREEN),
        }.get(self, Color.from_hex_code(Colors.BLACK))


class Templates:
    """Reusable message templates."""

    @staticmethod
    def generic(template_type: TemplateType, message: str) -> ContainerComponentBuilder:
        """Template for a standard message."""
        return ContainerComponentBuilder(
            accent_color=template_type.color,
            components=[TextDisplayComponentBuilder(content=message)],
        )

    @staticmethod
    def generic_thumb(
        template_type: TemplateType, message: str, thumb: str | Path
    ) -> ContainerComponentBuilder:
        """Template for a standard message with a thumbnail."""
        return ContainerComponentBuilder(
            accent_color=template_type.color,
            components=[
                SectionComponentBuilder(
                    components=[TextDisplayComponentBuilder(content=message)],
                    accessory=ThumbnailComponentBuilder(media=thumb),
                )
            ],
        )
