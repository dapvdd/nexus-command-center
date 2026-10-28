"""Tests for the HTTP API using the real store.

Verifies real request/response behavior.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

import pytest
import urllib.request
import urllib.error

from nexus_cc.store import Store
from nexus_cc.httpd import app


@pytest.fixture
def api_server(store):
    """Launch the HTTP server with the given store, yield the base URL, then shutdown."""
    server = app(store, host="localhost", port=0, auto_start_engine=False)
    port = server.server_address[1]
    base = f"http://localhost:{port}"
    yield base
    server.shutdown()


def api_get(path, base):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return json.loads(r.read())


def api_post(path, body, base):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        base + path, data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


class TestHealthEndpoint:
    def test_health(self, store, api_server):
        r = api_get("/api/health", api_server)
        assert r["ok"] is True
        assert "version" in r


class TestTaskCreationAPI:
    def test_create_task(self, store, api_server):
        r = api_post("/api/tasks", {"title": "API task"}, api_server)
        assert r["ok"] is True
        assert r["data"]["task"]["title"] == "API task"
        assert r["data"]["task"]["status"] == "queued"

    def test_create_task_validation(self, store, api_server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            api_post("/api/tasks", {}, api_server)
        assert exc.value.code == 400

    def test_idempotency(self, store, api_server):
        body = {"title": "Idempotent API task", "idempotency_key": "idem-api-1"}
        r1 = api_post("/api/tasks", body, api_server)
        r2 = api_post("/api/tasks", body, api_server)
        assert r1["data"]["task"]["id"] == r2["data"]["task"]["id"]

    def test_deduplicated_flag(self, store, api_server):
        body = {"title": "Deduplicated", "idempotency_key": "dedup-test"}
        r1 = api_post("/api/tasks", body, api_server)
        r2 = api_post("/api/tasks", body, api_server)
        assert r1["data"]["task"]["id"] == r2["data"]["task"]["id"]


class TestTaskRetrievalAPI:
    def test_list_tasks_empty(self, store, api_server):
        r = api_get("/api/tasks", api_server)
        assert r["ok"] is True
        assert r["data"]["tasks"] == []
        assert r["data"]["count"] == 0

    def test_list_tasks(self, store, api_server):
        api_post("/api/tasks", {"title": "List me"}, api_server)
        r = api_get("/api/tasks", api_server)
        assert len(r["data"]["tasks"]) == 1
        assert r["data"]["count"] == 1

    def test_list_tasks_with_status_filter(self, store, api_server):
        api_post("/api/tasks", {"title": "Queued task"}, api_server)
        r = api_get("/api/tasks?status=queued", api_server)
        assert all(t["status"] == "queued" for t in r["data"]["tasks"])

    def test_get_task(self, store, api_server):
        created = api_post("/api/tasks", {"title": "Get me"}, api_server)
        tid = created["data"]["task"]["id"]
        r = api_get(f"/api/tasks/{tid}", api_server)
        assert r["data"]["task"]["title"] == "Get me"

    def test_get_task_not_found(self, store, api_server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            api_get("/api/tasks/nonexistent-id", api_server)
        assert exc.value.code == 404


class TestTaskCancelAPI:
    def test_cancel_queued_task(self, store, api_server):
        created = api_post("/api/tasks", {"title": "Cancel me"}, api_server)
        tid = created["data"]["task"]["id"]
        r = api_post(f"/api/tasks/{tid}/cancel", {"actor": "lennox", "reason": "test"}, api_server)
        assert r["data"]["task"]["status"] == "cancelled"


class TestTaskRetryAPI:
    def test_retry_failed_task(self, store, api_server):
        created = api_post("/api/tasks", {"title": "Retry me", "idempotency_key": "retry-test-1"}, api_server)
        tid = created["data"]["task"]["id"]
        conn = sqlite3.connect(store.path)
        conn.execute("UPDATE tasks SET status='failed', error='boom' WHERE id=?", (tid,))
        conn.commit()
        conn.close()
        r = api_post(f"/api/tasks/{tid}/retry", {"actor": "lennox"}, api_server)
        assert r["data"]["task"]["status"] == "queued"


class TestDashboardRoot:
    def test_root_returns_html(self, store, api_server):
        with urllib.request.urlopen(api_server + "/", timeout=5) as r:
            assert r.status == 200
            content = r.read()
            assert b"NEXUS" in content

    def test_static_file_not_found(self, store, api_server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            api_get("/static/nonexistent.css", api_server)
        assert exc.value.code == 404

    def test_root_static_asset_served(self, store, api_server):
        # The SPA references /app.js at the site root (not /static/...).
        with urllib.request.urlopen(api_server + "/app.js", timeout=5) as r:
            assert r.status == 200
            assert b"renderOverview" in r.read()


class TestUsageEngineAPI:
    def test_usage_endpoint(self, store, api_server):
        r = api_get("/api/usage", api_server)
        assert r["ok"] is True
        assert "summary" in r["data"] and "by_model" in r["data"]

    def test_engine_status_endpoint(self, store, api_server):
        r = api_get("/api/engine", api_server)
        assert r["ok"] is True
        assert "state" in r["data"] and "counts" in r["data"]

    def test_adapters_endpoint(self, store, api_server):
        r = api_get("/api/adapters", api_server)
        assert r["ok"] is True
        names = [a["name"] for a in r["data"]["adapters"]]
        assert "artifact" in names and "echo" in names

    def test_roles_endpoint(self, store, api_server):
        r = api_get("/api/roles", api_server)
        ids = [x["id"] for x in r["data"]["roles"]]
        assert ids == ["lennox", "alistair", "nexus"]


class TestAgentAPI:
    def test_register_agent(self, store, api_server):
        r = api_post("/api/agents", {
            "id": "nexus", "role": "nexus", "name": "NEXUS",
            "capabilities": ["execute"]
        }, api_server)
        assert r["ok"] is True
        assert r["data"]["agent"]["id"] == "nexus"

    def test_list_agents(self, store, api_server):
        api_post("/api/agents", {"id": "alistair", "role": "alistair", "name": "ALISTAIR"}, api_server)
        r = api_get("/api/agents", api_server)
        assert any(a["id"] == "alistair" for a in r["data"]["agents"])


class TestAuditAPI:
    def test_list_audit(self, store, api_server):
        api_post("/api/tasks", {"title": "Audit API test"}, api_server)
        r = api_get("/api/audit", api_server)
        assert r["ok"] is True
        assert "audit" in r["data"]