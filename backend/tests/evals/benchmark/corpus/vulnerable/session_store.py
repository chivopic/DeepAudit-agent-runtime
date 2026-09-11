"""Session persistence."""
import pickle


def load_session(blob: bytes):
    return pickle.loads(blob)


def restore(data: str):
    return eval(data)
