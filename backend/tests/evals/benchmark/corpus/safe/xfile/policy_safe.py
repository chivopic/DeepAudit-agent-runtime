"""Outbound request policy."""

_ALLOWED = frozenset({"hooks.example.com", "api.example.com"})


def host_allowed(host: str) -> bool:
    """Whether we may talk to this host."""
    return host.lower() in _ALLOWED
