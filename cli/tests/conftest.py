from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fake_semgrep(tmp_path: Path) -> Path:
    executable = tmp_path / "semgrep"
    payload_builder = repr(
        {
            "results": [],
            "errors": [],
            "paths": {"scanned": []},
            "time": {"max_memory_bytes": 1234},
        }
    )
    executable.write_text(
        f"""#!/usr/bin/python3
import json
import sys

if sys.argv[1:] == [\"--version\"]:
    print(\"1.173.0\")
    raise SystemExit(0)

targets = sys.argv[sys.argv.index(\"--jobs\") + 2:]
payload = {payload_builder}
payload[\"paths\"][\"scanned\"] = targets
if targets:
    payload[\"results\"] = [{{
        \"check_id\": \"deepaudit.python.dangerous-eval\",
        \"path\": targets[0],
        \"start\": {{\"line\": 1, \"col\": 1}},
        \"end\": {{\"line\": 1, \"col\": 12}},
        \"extra\": {{
            \"message\": \"dangerous evaluation\",
            \"severity\": \"ERROR\",
            \"metadata\": {{
                \"title\": \"Dynamic Code Evaluation\",
                \"category\": \"code_injection\",
                \"cwe\": [\"CWE-95: Dynamic Evaluation\"],
                \"confidence\": \"HIGH\"
            }}
        }}
    }}]
print(json.dumps(payload))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable
