"""Small network helpers. Arguments are passed as argv, never a shell string."""
import ipaddress
import subprocess


def ping(host: str) -> int:
    # Rejects anything that is not a literal address before execution.
    ipaddress.ip_address(host)
    return subprocess.run(
        ["ping", "-c", "1", host], shell=False, check=False, timeout=10
    ).returncode


def traceroute(host: str) -> str:
    ipaddress.ip_address(host)
    out = subprocess.run(
        ["traceroute", host], shell=False, capture_output=True, timeout=30
    )
    return out.stdout.decode("utf-8", errors="ignore")
