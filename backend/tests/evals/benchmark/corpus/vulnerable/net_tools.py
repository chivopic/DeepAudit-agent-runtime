"""Small network helpers used by the ops dashboard."""
import os
import subprocess


def ping(host: str) -> int:
    return os.system("ping -c 1 " + host)


def traceroute(host: str) -> bytes:
    return subprocess.check_output(f"traceroute {host}", shell=True)
