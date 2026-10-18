"""Task 2: the tenant-scoped webhook-origin lineage store (Slack notification eligibility).

These are unit tests against a recording graph: every method must go through one
named query in ``cypher/`` with scalar parameters only, and map the row with the
shared ``recall_run_data`` mapper. The Cypher itself is exercised against a real
FalkorDB in ``tests/test_notification_store_integration.py``.
"""
from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager

import pytest

from api.cypher import query
from api.notification_store import NotificationStore
from api.schemas import RecallRunData

SANDBOX = "acme/keystone-sandbox"
ISSUE_KEY = f"issue:{SANDBOX}#42"
UPSTREAM_KEY = "issue:acme/upstream#7"
RUN_KEY = "run:00000000-0000-4000-8000-000000000001"
OTHER_RUN = "run:00000000-0000-4000-8000-000000000002"
MODULE = "module:acme/keystone-sandbox/src/graph"

RECALL_HEADER = [
    ["String", "run_key"],
    ["Integer", "at"],
    ["String", "issue_key"],
    ["String", "summary"],
    ["Boolean", "fallback"],
    ["String", "model"],
    ["Integer", "llm_calls"],
    ["Integer", "ms"],
    ["String", "triage"],
    ["List", "evidence"],
    ["List", "actions"],
]
# notification_latest_run returns the recall row plus the issue title and replay key.
LATEST_HEADER = RECALL_HEADER + [["String", "title"], ["String", "replay_of"]]
META_HEADER = [["String", "title"], ["String", "replay_of"], ["String", "notify_root"]]
UI_META_HEADER = [["String", "title"], ["String", "replay_of"]]
MARK_HEADER = [
    ["String", "root"],
    ["String", "parent"],
    ["String", "event"],
    ["String", "cause"],
    ["Integer", "at"],
]
LINK_HEADER = [["String", "key"], ["String", "url"]]


class FakeResult:
    def __init__(self, rows, header=None):
        self.result_set = rows
        self.header = header or []


class RecordingGraph:
    """A graph stand-in that records each named query and its parameters.

    ``responses`` maps an exact query text to ``(header, rows)``; anything else
    falls back to ``default``.
    """

    def __init__(self, responses=None, default=None):
        self.calls: list[tuple[str, dict]] = []
        self.responses = responses or {}
        self.default = default if default is not None else ([], [])
        self.timeouts = []

    def query(self, text, params=None, *, timeout=None):
        self.calls.append((text, params))
        self.timeouts.append(timeout)
        header, rows = self.responses.get(text, self.default)
        return FakeResult(rows, header)


def make_store(responses=None, default=None):
    graph = RecordingGraph(responses, default)
    store = NotificationStore(sandbox_repo=SANDBOX, graph_factory=lambda tenant: graph)
    return store, graph


@pytest.mark.parametrize("operation", ["latest", "load", "parent", "ui", "mark", "links"])
def test_every_notification_query_has_a_server_execution_bound(operation):
    store, graph = make_store(responses={
        query("recall_run"): (RECALL_HEADER, [recall_row()]),
    })
    if operation == "latest":
        store.latest_eligible("falkordb", ISSUE_KEY)
    elif operation == "load":
        store.load_run("falkordb", RUN_KEY)
    elif operation == "parent":
        store.eligible_parent("falkordb", ISSUE_KEY, RUN_KEY)
    elif operation == "ui":
        store.eligible_parent("falkordb", ISSUE_KEY, RUN_KEY, ui_feedback_key="feedback:1")
    elif operation == "links":
        store.node_links("falkordb", [MODULE])
    else:
        store.mark_run("falkordb", RUN_KEY, issue_key=ISSUE_KEY, cause="issue_opened",
                       event_id="delivery:1", parent_run_key=None)
    assert graph.timeouts and all(value == 1000 for value in graph.timeouts)


@contextmanager
def stalled_notification_probe(*, startup_delay=0, unbounded=False, startup_timeout=30):
    """Separate cold startup from the I/O watchdog; always reap the child and peer."""
    done = threading.Event()
    connections = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.1)

        def stall():
            # Keep accepting so a reconnect/retry is observable, but never reply
            # or close early enough to manufacture a successful liveness result.
            while not done.is_set():
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    continue
                connections.append(connection)

        thread = threading.Thread(target=stall, daemon=True)
        thread.start()
        env = {**os.environ, "FALKORDB_HOST": "127.0.0.1",
               "FALKORDB_PORT": str(listener.getsockname()[1]),
               "FALKORDB_PASSWORD": "", "FALKORDB_USERNAME": ""}
        script = f"import time; time.sleep({startup_delay})\n" + '''
import sys
from api.notification_store import NotificationStore
from redis.connection import Connection
from redis.exceptions import TimeoutError
''' + ('''
# Deliberately remove the real socket's bound to verify the outer watchdog.
connect = Connection._connect
def unbounded_connect(self):
    sock = connect(self)
    sock.settimeout(None)
    return sock
Connection._connect = unbounded_connect
''' if unbounded else '') + '''
print("READY", flush=True)
assert sys.stdin.readline() == "GO\\n"
started = time.monotonic()
try:
    NotificationStore("acme/keystone-sandbox").latest_eligible("slacktest", "issue:acme/keystone-sandbox#42")
except TimeoutError:
    print(time.monotonic() - started, flush=True)
else:
    raise AssertionError("a stalled peer must time out")
'''
        process = None
        reader = None
        try:
            process = subprocess.Popen(
                [sys.executable, "-c", script], env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            readiness = queue.Queue()

            def read_ready():
                try:
                    readiness.put(process.stdout.readline())
                except Exception as exc:
                    readiness.put(exc)

            # Pipe reads work on Windows too. The queue gives cold startup its
            # own finite bound, without starting the graph I/O budget yet.
            reader = threading.Thread(target=read_ready, daemon=True)
            reader.start()
            try:
                ready = readiness.get(timeout=startup_timeout)
            except queue.Empty as exc:
                raise AssertionError("notification probe did not finish importing") from exc
            if isinstance(ready, Exception):
                raise AssertionError("notification probe readiness read failed") from ready
            assert ready == "READY\n", "notification probe failed before readiness"
            reader.join(timeout=1)
            assert not reader.is_alive(), "readiness reader did not stop"
            yield process, connections
        finally:
            try:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    # Reap first to unblock readline on readiness timeout/error;
                    # never race communicate() against the readiness reader.
                    process.wait(timeout=1)
                    if reader is not None:
                        reader.join(timeout=1)
                        assert not reader.is_alive(), "readiness reader did not stop"
                    process.communicate(timeout=1)
            finally:
                done.set()
                thread.join(timeout=1)
                for connection in connections:
                    connection.close()
                assert not thread.is_alive(), "stalled peer did not stop"


@pytest.mark.parametrize("startup_delay", [0, 3.1], ids=["normal", "slow-startup"])
def test_stalled_notification_connection_times_out_without_retrying(startup_delay, monkeypatch):
    """Even startup longer than the watchdog must leave the full I/O budget."""
    import select

    # Windows supports select() for sockets, not subprocess pipes. Exercise that
    # constraint on every platform without changing subprocess's real behavior.
    original_select = select.select

    def sockets_only(readers, writers, errors, timeout=None):
        if any(not isinstance(stream, socket.socket) for stream in readers + writers + errors):
            raise OSError("select does not support pipes")
        return original_select(readers, writers, errors, timeout)

    monkeypatch.setattr(select, "select", sockets_only)
    with stalled_notification_probe(startup_delay=startup_delay) as (process, connections):
        stdout, stderr = process.communicate("GO\n", timeout=3)
        assert process.returncode == 0, stderr
        # Observe the actual two-second socket timeout, not a fast connection
        # failure, and reject reconnects even if they fit within the watchdog.
        assert 2 <= float(stdout.strip()) < 3
        assert len(connections) == 1


def test_stalled_notification_probe_rejects_unbounded_network_io():
    with stalled_notification_probe(unbounded=True) as (process, connections):
        with pytest.raises(subprocess.TimeoutExpired):
            process.communicate("GO\n", timeout=3)
        assert len(connections) == 1
    assert process.poll() is not None  # the watchdog failure still reaps the child


@pytest.mark.parametrize("failure", ["timeout", "unexpected-marker", "eof"])
def test_stalled_notification_probe_cleans_up_readiness_failure(failure, monkeypatch):
    processes = []
    threads = []
    original_popen = subprocess.Popen
    original_thread = threading.Thread
    scripts = {
        "timeout": "import time; time.sleep(60)",
        "unexpected-marker": "import time; print('WRONG', flush=True); time.sleep(60)",
        "eof": "raise SystemExit(1)",
    }

    def launch_probe(args, **kwargs):
        process = original_popen([args[0], "-c", scripts[failure]], **kwargs)
        processes.append(process)
        return process

    def track_thread(*args, **kwargs):
        thread = original_thread(*args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(subprocess, "Popen", launch_probe)
    monkeypatch.setattr(threading, "Thread", track_thread)
    message = "did not finish importing" if failure == "timeout" else "failed before readiness"
    with pytest.raises(AssertionError, match=message):
        with stalled_notification_probe(startup_timeout=0.1 if failure == "timeout" else 3):
            pytest.fail("a probe without readiness must not start I/O")
    assert processes and all(process.poll() is not None for process in processes)
    assert threads and all(not thread.is_alive() for thread in threads)


def triage_json(run_key: str = RUN_KEY) -> str:
    return json.dumps(
        {
            "run_key": run_key,
            "labels": [],
            "reviewers": [],
            "risks": [],
            "decisions": [],
            "comment_markdown": "x",
            "trace": [],
        }
    )


def action_row(*, key: str = "action:a", value: str = "alice"):
    return {
        "action_key": key,
        "kind": "assign",
        "value": value,
        "module": MODULE,
        "status": "proposed",
        "note": None,
        "feedback": [],
    }


def recall_row(*, run_key: str = RUN_KEY, actions=None):
    return [
        run_key,
        1792000000,
        ISSUE_KEY,
        "summary",
        False,
        "model",
        0,
        5,
        triage_json(run_key),
        ["issue:x"],
        actions if actions is not None else [],
    ]


# --- latest_eligible --------------------------------------------------------


def test_latest_eligible_uses_the_named_query_and_maps_the_row():
    row = recall_row(actions=[action_row()]) + ["Seed issue", UPSTREAM_KEY]
    store, graph = make_store(default=(LATEST_HEADER, [row]))

    snapshot = store.latest_eligible("falkordb", ISSUE_KEY)

    assert isinstance(snapshot.data, RecallRunData)
    assert snapshot.data.run.run_key == RUN_KEY
    assert snapshot.title == "Seed issue"
    assert snapshot.replay_of == UPSTREAM_KEY
    assert snapshot.data.actions[0].value == "alice"
    (text, params), = graph.calls
    assert text == query("notification_latest_run")
    assert params == {"issue": ISSUE_KEY}


def test_latest_eligible_returns_none_without_a_row():
    store, graph = make_store(default=(LATEST_HEADER, []))

    assert store.latest_eligible("falkordb", ISSUE_KEY) is None
    assert len(graph.calls) == 1


# --- load_run ---------------------------------------------------------------


def test_load_run_wraps_recall_run_and_resolves_run_meta():
    responses = {
        query("recall_run"): (RECALL_HEADER, [recall_row()]),
        query("notification_run_meta"): (META_HEADER, [["Seed issue", UPSTREAM_KEY, None]]),
    }
    store, graph = make_store(responses)

    snapshot = store.load_run("falkordb", RUN_KEY)

    assert snapshot.data.run.run_key == RUN_KEY
    assert snapshot.title == "Seed issue"
    assert snapshot.replay_of == UPSTREAM_KEY
    assert [text for text, _ in graph.calls] == [query("recall_run"), query("notification_run_meta")]
    assert graph.calls[0][1] == {"run": RUN_KEY}
    assert graph.calls[1][1] == {"run": RUN_KEY, "issue": None}


def test_load_run_returns_none_when_the_run_is_unknown():
    store, graph = make_store(responses={query("recall_run"): (RECALL_HEADER, [])})

    assert store.load_run("falkordb", RUN_KEY) is None
    assert [text for text, _ in graph.calls] == [query("recall_run")]


def test_load_run_returns_none_for_a_non_issue_node():
    # recall_run finds the run, but a raw/adhoc run has no HANDLED edge, so run meta is empty.
    responses = {
        query("recall_run"): (RECALL_HEADER, [recall_row()]),
        query("notification_run_meta"): (META_HEADER, []),
    }
    store, _ = make_store(responses)

    assert store.load_run("falkordb", RUN_KEY) is None


# --- eligible_parent --------------------------------------------------------


def test_eligible_parent_requires_recorded_lineage():
    responses = {
        query("recall_run"): (RECALL_HEADER, [recall_row()]),
        # The run handled the issue but was never marked with an origin root.
        query("notification_run_meta"): (META_HEADER, [["Seed issue", None, None]]),
    }
    store, _ = make_store(responses)

    assert store.eligible_parent("falkordb", ISSUE_KEY, RUN_KEY) is None


def test_eligible_parent_returns_a_proven_parent():
    responses = {
        query("recall_run"): (RECALL_HEADER, [recall_row()]),
        query("notification_run_meta"): (META_HEADER, [["Seed issue", None, RUN_KEY]]),
    }
    store, graph = make_store(responses)

    snapshot = store.eligible_parent("falkordb", ISSUE_KEY, RUN_KEY)

    assert snapshot.data.run.run_key == RUN_KEY
    assert graph.calls[1][1] == {"run": RUN_KEY, "issue": ISSUE_KEY}


def test_eligible_parent_ui_uses_the_ui_query_with_the_feedback_key():
    responses = {
        query("recall_run"): (RECALL_HEADER, [recall_row()]),
        query("notification_ui_parent"): (UI_META_HEADER, [["Seed issue", None]]),
    }
    store, graph = make_store(responses)

    snapshot = store.eligible_parent(
        "falkordb", ISSUE_KEY, RUN_KEY, ui_feedback_key="feedback:f1"
    )

    assert snapshot.data.run.run_key == RUN_KEY
    assert graph.calls[1][0] == query("notification_ui_parent")
    assert graph.calls[1][1] == {"run": RUN_KEY, "issue": ISSUE_KEY, "feedback": "feedback:f1"}


def test_eligible_parent_ui_returns_none_when_the_feedback_check_fails():
    responses = {
        query("recall_run"): (RECALL_HEADER, [recall_row()]),
        query("notification_ui_parent"): (UI_META_HEADER, []),
    }
    store, _ = make_store(responses)

    assert store.eligible_parent("falkordb", ISSUE_KEY, RUN_KEY, ui_feedback_key="feedback:x") is None


# --- mark_run ---------------------------------------------------------------


def test_mark_run_uses_the_named_query_and_pins_scalar_params(monkeypatch):
    monkeypatch.setattr("api.notification_store._now_ms", lambda: 1234)
    store, graph = make_store(
        default=(MARK_HEADER, [[RUN_KEY, None, "delivery:d1", "issue_opened", 1234]])
    )

    assert store.mark_run(
        "falkordb", RUN_KEY, issue_key=ISSUE_KEY, cause="issue_opened",
        event_id="delivery:d1", parent_run_key=None,
    ) is True

    (text, params), = graph.calls
    assert text == query("notification_mark_run")
    assert params == {
        "run": RUN_KEY,
        "issue": ISSUE_KEY,
        "parent": None,
        "event": "delivery:d1",
        "cause": "issue_opened",
        "now": 1234,
    }
    # The run key is a parameter, never interpolated into the query text.
    assert RUN_KEY not in text


def test_mark_run_returns_false_without_a_row(monkeypatch):
    monkeypatch.setattr("api.notification_store._now_ms", lambda: 1)
    store, _ = make_store(default=(MARK_HEADER, []))

    assert store.mark_run(
        "falkordb", RUN_KEY, issue_key=ISSUE_KEY, cause="issue_opened",
        event_id="delivery:d1", parent_run_key=None,
    ) is False


def test_mark_run_rejects_a_feedback_cause_that_tries_to_create_a_root():
    store, graph = make_store()

    assert store.mark_run(
        "falkordb", RUN_KEY, issue_key=ISSUE_KEY, cause="ui_feedback",
        event_id="ui:x:y", parent_run_key=None,
    ) is False
    assert graph.calls == []


def test_mark_run_rejects_a_foreign_or_malformed_issue_key():
    store, graph = make_store()

    for issue_key in ("issue:other/repo#1", f"issue:{SANDBOX}#0", f"issue:{SANDBOX}#abc", "not-an-issue"):
        assert store.mark_run(
            "falkordb", RUN_KEY, issue_key=issue_key, cause="issue_opened",
            event_id="delivery:d1", parent_run_key=None,
        ) is False
    assert graph.calls == []


def test_mark_run_is_idempotent_for_the_same_write(monkeypatch):
    monkeypatch.setattr("api.notification_store._now_ms", lambda: 5)
    store, _ = make_store(
        default=(MARK_HEADER, [[RUN_KEY, None, "delivery:d1", "issue_opened", 1]])
    )

    assert store.mark_run(
        "falkordb", RUN_KEY, issue_key=ISSUE_KEY, cause="issue_opened",
        event_id="delivery:d1", parent_run_key=None,
    ) is True


def test_mark_run_rejects_a_conflicting_reparent(monkeypatch):
    monkeypatch.setattr("api.notification_store._now_ms", lambda: 5)
    # The stored parent is RUN_KEY, but the caller asks to reparent under OTHER_RUN.
    store, _ = make_store(
        default=(MARK_HEADER, [[RUN_KEY, RUN_KEY, "delivery:d1", "issue_opened", 1]])
    )

    assert store.mark_run(
        "falkordb", RUN_KEY, issue_key=ISSUE_KEY, cause="github_feedback",
        event_id="comment:1", parent_run_key=OTHER_RUN,
    ) is False


# --- node_links -------------------------------------------------------------


def test_node_links_delegates_to_the_existing_store():
    url = f"https://github.com/{SANDBOX}/issues/42"
    store, graph = make_store(default=(LINK_HEADER, [[ISSUE_KEY, url]]))

    assert store.node_links("falkordb", [ISSUE_KEY, "person:alice"]) == {ISSUE_KEY: url}
    (text, params), = graph.calls
    assert text == query("webhook_node_links")
    assert params == {"keys": [ISSUE_KEY, "person:alice"]}


def test_node_links_with_no_keys_runs_no_query():
    store, graph = make_store()

    assert store.node_links("falkordb", []) == {}
    assert graph.calls == []


# --- tenant scoping ---------------------------------------------------------


def test_every_call_uses_the_requested_tenant_graph():
    seen: list[str] = []
    graph = RecordingGraph(
        responses={query("recall_run"): (RECALL_HEADER, [recall_row()]),
                   query("notification_run_meta"): (META_HEADER, [["t", None, None]])}
    )

    def factory(tenant: str):
        seen.append(tenant)
        return graph

    store = NotificationStore(sandbox_repo=SANDBOX, graph_factory=factory)
    store.latest_eligible("tenant-a", ISSUE_KEY)
    store.load_run("tenant-b", RUN_KEY)

    assert seen == ["tenant-a", "tenant-b", "tenant-b"]
