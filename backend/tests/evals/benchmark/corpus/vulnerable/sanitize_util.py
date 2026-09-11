"""Input hygiene helpers shared by the reporting endpoints."""


def clean(value: str) -> str:
    """Strip quotes so the value is safe to embed in a query."""
    return value.replace("'", "")
