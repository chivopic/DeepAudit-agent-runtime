"""Rendering for custom report headers."""
from jinja2 import Environment

from .settings_safe import LEGACY_TEMPLATE_MODE

env = Environment(autoescape=not LEGACY_TEMPLATE_MODE)


def render_header(template_source: str, **values) -> str:
    return env.from_string(template_source).render(**values)
