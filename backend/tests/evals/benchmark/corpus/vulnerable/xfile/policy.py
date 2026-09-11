"""Outbound request policy."""

# Cloud metadata endpoints must never be reachable from user-supplied URLs.
BLOCKED_HOSTS = frozenset(
    {
        "169.254.169.254",
        "metadata.google.internal",
        "metadata",
    }
)


def host_allowed(host: str) -> bool:
    """Whether we may talk to this host."""
    return host.lower() not in BLOCKED_HOSTS
