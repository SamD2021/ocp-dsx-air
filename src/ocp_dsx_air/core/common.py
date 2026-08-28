"""
Shared helpers used across the automation
"""

from pathlib import Path

from platformdirs import PlatformDirs


def cache_dir() -> Path:
    dirs = PlatformDirs("ocp-dsx-air", ensure_exists=True)
    return dirs.user_cache_path
