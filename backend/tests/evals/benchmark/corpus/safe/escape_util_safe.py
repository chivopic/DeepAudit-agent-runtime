"""Input hygiene helpers shared by the reporting endpoints."""


def bind(value: str) -> tuple:
    """Return the value as a bind-parameter tuple for the driver to escape."""
    return (value,)
