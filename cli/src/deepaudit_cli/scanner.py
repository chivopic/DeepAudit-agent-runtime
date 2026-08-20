"""Governed subprocess boundary shared by local scanner adapters."""

from __future__ import annotations

import os
import re
import selectors
import shutil
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)", re.DOTALL)
_SYSTEM_CA_CANDIDATES = (
    Path("/etc/ssl/cert.pem"),
    Path("/etc/ssl/certs/ca-certificates.crt"),
    Path("/etc/pki/tls/certs/ca-bundle.crt"),
)


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    timed_out: bool = False
    output_limited: bool = False


def sanitize(value: object, *, max_length: int = 4000) -> str:
    text = _ANSI_OSC_RE.sub("", str(value or ""))
    text = _ANSI_CSI_RE.sub("", text)
    parts: list[str] = []
    for character in text:
        code = ord(character)
        parts.append(character if character in "\n\r\t" or code >= 32 and code != 127 else f"\\x{code:02x}")
        if sum(map(len, parts)) >= max_length:
            break
    return "".join(parts)[:max_length]


def resolve_executable(value: str | Path) -> Path:
    raw = str(value)
    candidate = Path(raw).expanduser()
    if candidate.parent != Path("."):
        resolved = candidate.resolve(strict=True)
    else:
        located = shutil.which(raw)
        if located is None:
            raise FileNotFoundError(f"Semgrep executable not found: {raw}")
        resolved = Path(located).resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise PermissionError(f"Semgrep path is not executable: {resolved}")
    return resolved


def scanner_environment(temp_root: Path, executable: Path) -> dict[str, str]:
    environment = {
        "HOME": str(temp_root),
        "TMPDIR": str(temp_root),
        "PATH": os.pathsep.join((str(executable.parent), "/usr/bin", "/bin")),
        "LANG": "C",
        "LC_ALL": "C",
        "NO_COLOR": "1",
        "PYTHONIOENCODING": "utf-8",
        "SEMGREP_ENABLE_VERSION_CHECK": "0",
        "SEMGREP_SEND_METRICS": "off",
    }
    for candidate in _SYSTEM_CA_CANDIDATES:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file():
            environment["SSL_CERT_FILE"] = str(resolved)
            break
    return environment


def _kill(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL) if os.name == "posix" else process.kill()
    except PermissionError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        pass


def run_capped(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
    output_limit_bytes: int,
) -> ProcessResult:
    if not argv or not Path(argv[0]).is_absolute():
        raise ValueError("scanner executable must be an absolute path")
    if timeout_seconds <= 0 or output_limit_bytes <= 0:
        raise ValueError("process limits must be positive")
    started = time.perf_counter()
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, data=name)
    timed_out = False
    output_limited = False
    deadline = started + timeout_seconds
    try:
        while selector.get_map():
            if time.perf_counter() >= deadline:
                timed_out = True
                _kill(process)
            for key, _ in selector.select(timeout=0.05):
                try:
                    chunk = os.read(key.fd, 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                streams[key.data].extend(chunk)
                if sum(map(len, streams.values())) > output_limit_bytes:
                    output_limited = True
                    _kill(process)
    finally:
        selector.close()
        _kill(process)
        process.wait()
    stdout = bytes(streams["stdout"][:output_limit_bytes])
    remaining = max(0, output_limit_bytes - len(stdout))
    return ProcessResult(
        returncode=process.returncode,
        stdout=stdout,
        stderr=bytes(streams["stderr"][:remaining]),
        duration_seconds=time.perf_counter() - started,
        timed_out=timed_out,
        output_limited=output_limited,
    )
