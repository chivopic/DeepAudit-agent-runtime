"""Session persistence using a data-only format."""
import json


def load_session(blob: bytes) -> dict:
    data = json.loads(blob.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("session payload must be an object")
    return data


def restore(data: str) -> dict:
    return json.loads(data)
