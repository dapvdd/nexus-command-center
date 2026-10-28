"""Persistence package for NEXUS Command Center.

Single-writer SQLite store in WAL mode with explicit foreign keys enabled and
synchronous=NORMAL. All mutations that matter are single guarded statements.
"""

from .db import Store, ConnectionLostError

__all__ = ["Store", "ConnectionLostError"]
