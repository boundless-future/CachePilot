"""Locate the separately built, environment-specific reservation extension."""
import os
from pathlib import Path
import sys


def native_directory():
    explicit = os.environ.get("CACHEPILOT_NATIVE_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "native/reservation_lock.cpp").is_file():
            return parent / "artifacts/reservation-native"
    return None


def configure_native_path():
    directory = native_directory()
    if directory is not None and str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return directory
