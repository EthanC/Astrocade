"""Reusable message templates."""

from enum import Enum, auto
from typing import Self

from hikari import Color
from hikari.impl import (
    ContainerComponentBuilder,
    SectionComponentBuilder,
    TextDisplayComponentBuilder,
    ThumbnailComponentBuilder,
)


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
            TemplateType.INFO: Color.from_hex_code("#5865F2"),
            TemplateType.WARN: Color.from_hex_code("#FFC04E"),
            TemplateType.ERROR: Color.from_hex_code("#DA3E44"),
            TemplateType.SUCCESS: Color.from_hex_code("#45A366"),
        }.get(self, Color.from_hex_code("#000000"))


class Templates:
    """Reusable message templates."""

    @staticmethod
    def reply(message: str, template_type: TemplateType) -> ContainerComponentBuilder:
        """Template for a standard reply message."""
        return ContainerComponentBuilder(
            accent_color=template_type.color,
            components=[TextDisplayComponentBuilder(content=message)],
        )
