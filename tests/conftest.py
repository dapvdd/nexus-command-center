"""Shared pytest fixtures for NEXUS Command Center tests."""

from __future__ import annotations

import os
import tempfile
import pytest

from nexus_cc.store import Store


@pytest.fixture
def db_path():
    """Provide a temporary database path, deleted after the test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def store(db_path):
    """Provide a fresh Store instance backed by a temporary SQLite DB."""
    s = Store(db_path)
    yield s
    s.close()


@pytest.fixture
def store_with_tasks(store):
    """Store pre-populated with three tasks in different states."""
    t1 = store.create_task(
        title="Alpha task",
        description="A queued task",
        agent_id="nexus",
        priority=700,
        idempotency_key="alpha-1",
    )
    t2 = store.create_task(
        title="Bravo task",
        description="A done task",
        agent_id="alistair",
        idempotency_key="bravo-1",
        approval_mode="required",
    )
    # Simulate bravo being done
    store._update(
        "UPDATE tasks SET status='done', result='{\"ok\":true}'"
        " WHERE id=?",
        (t2["id"],),
    )

    t3 = store.create_task(
        title="Charlie task",
        description="A failed task",
        agent_id="nexus",
        idempotency_key="charlie-1",
    )
    store._update(
        "UPDATE tasks SET status='failed', error='boom' WHERE id=?",
        (t3["id"],),
    )
    return store
