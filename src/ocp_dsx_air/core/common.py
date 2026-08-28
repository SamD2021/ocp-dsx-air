"""
Shared helpers used across the automation
"""

from __future__ import annotations
from platformdirs import PlatformDirs
from pathlib import Path


def cache_dir() -> Path:
    dirs = PlatformDirs("ocp-dsx-air", ensure_exists=True)
    return dirs.user_cache_path
