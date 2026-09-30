import unittest
from datetime import datetime, timezone

from app.agent.live_tasks import LiveTaskMonitor, clamp, clean_message
from app.agent.tools import AgentTools
from app.connectors.qrs_client import QrsClient
from app.lineage.builder import LineageBuilder
from app.models.entities import GraphEdge
from app.scanner.orchestrator import ScannerOrchestrator

TASK = "11111111-2222-3333-4444-555555555555"
OTHER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


class FakeQrs:
    def __init__(self, responses=None, fail=False):
        self.responses = responses or {}
        self.fail = fail
        self.calls = []

    def read(self, path, filter_expr=None):
        self.calls.append((path, filter_expr))
        if self.fail:
            raise ConnectionError("QRS down")
        return self.responses.get(path, [])


class FakeRepo:
    def __init__(self, matches=None, names=None):
        self.matches = matches or []
        self.names = names or {}
        self.searches = []

    def search_nodes(self, query, type_filter=None, limit=25):
        self.searches.append(query)
        return self.matches

    def display_names(self, ids_by_type):
        return self.names


def monitor(qrs, repo=None, clock=None):
    return LiveTaskMonitor(qrs=qrs, repository=repo or FakeRepo(), cache_seconds=30,
                           clock=clock or (lambda: 0.0), now=lambda: NOW)


class QrsAllowListTests(unittest.TestCase):
    def test_rejects_paths_outside_allow_list(self):
        client = QrsClient(base_url="https://qlik.invalid/custom", qlik_user="u", xrf_key="x" * 16)
        for path in ("/qrs/task/start", f"/qrs/reloadtask/{TASK}/start", "/qrs/app/full/../delete"):
            with self.assertRaises(PermissionError):
                client.read(path)


class ResolveTests(unittest.TestCase):
    def test_name_is_resolved_via_repository_and_never_reaches_a_filter(self):
        name = "Sales' or name eq 'x"
        qrs = FakeQrs({"/qrs/reloadtask/full": [{"id": TASK, "name": "Sales"}]})
        repo = FakeRepo(matches=[{"type": "Task", "id": TASK, "name": name}])
        result = monitor(qrs, repo).task_status(name)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(repo.searches, [name])
        for _, flt in qrs.calls:
            self.assertNotIn("Sales'", flt or "")

    def test_ambiguous_name_returns_candidates_without_calling_qrs(self):
        qrs = FakeQrs()
        repo = FakeRepo(matches=[{"id": TASK, "name": "Reload Sales"}, {"id": OTHER, "name": "Reload Sales(1)"}])
        result = monitor(qrs, repo).task_status("sales")
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(qrs.calls, [])

    def test_exact_name_wins_over_partial_matches(self):
        qrs = FakeQrs({"/qrs/reloadtask/full": [{"id": TASK, "name": "Reload Sales"}]})
        repo = FakeRepo(matches=[{"id": TASK, "name": "Reload Sales"}, {"id": OTHER, "name": "Reload Sales(1)"}])
        self.assertEqual(monitor(qrs, repo).task_status("reload sales")["status"], "ok")

    def test_unknown_task(self):
        self.assertEqual(monitor(FakeQrs()).task_status("nope")["status"], "not_found")


class LiveStatusTests(unittest.TestCase):
    def test_task_status_shape(self):
        qrs = FakeQrs({
            "/qrs/reloadtask/full": [{
                "id": TASK, "name": "Reload Sales", "enabled": True,
                "app": {"id": "app1", "name": "Sales"},
                "operational": {
                    "nextExecution": "2026-09-30T13:00:00.000Z",
                    "lastExecutionResult": {"status": 8, "startTime": "2026-09-30T10:00:00.000Z",
                                            "stopTime": "2026-09-30T10:05:00.000Z", "duration": 300000,
                                            "scriptLogLocation": "\\\\server\\logs\\x.log",
                                            "details": [{"message": "Changing task state from Started to FinishedFail",
                                                         "detailCreatedDate": "2026-09-30T10:05:00.000Z"},
                                                        {"message": "Connect failed: pwd=hunter2;",
                                                         "detailCreatedDate": "2026-09-30T10:04:00.000Z"}]},
                },
            }],
            "/qrs/compositeevent/full": [{"name": "After extract", "enabled": True,
                                          "reloadTask": {"id": TASK, "name": "Reload Sales"},
                                          "compositeRules": [{"ruleState": 1, "reloadTask": {"id": OTHER, "name": "Extract"}}]}],
            "/qrs/executionsession/full": [{"reloadTask": {"id": TASK}}],
        })
        result = monitor(qrs).task_status(TASK)
        self.assertTrue(result["running_now"])
        self.assertEqual(result["last_run"]["status"], "FinishedFail")
        self.assertEqual(result["last_run"]["duration_minutes"], 5.0)
        self.assertEqual(result["last_run"]["messages"], ["Connect failed: pwd=***;"])
        self.assertEqual(result["next_run"]["in_minutes"], 60)
        self.assertEqual(result["triggered_by"][0]["after"][0]["on"], "success")
        self.assertNotIn("scriptLogLocation", str(result))
        self.assertEqual(result["as_of"], "2026-09-30T12:00:00.000Z")

    def test_cache_prevents_second_qrs_call(self):
        qrs = FakeQrs({"/qrs/executionsession/full": []})
        now = [0.0]
        mon = monitor(qrs, clock=lambda: now[0])
        mon.running_tasks()
        now[0] = 29.0
        mon.running_tasks()
        self.assertEqual(len(qrs.calls), 1)
        now[0] = 31.0
        mon.running_tasks()
        self.assertEqual(len(qrs.calls), 2)

    def test_failures_grouped_per_task_with_clamped_window(self):
        runs = [
            {"taskID": TASK, "appID": "app1", "status": 8, "startTime": "2026-09-30T09:00:00.000Z", "details": []},
            {"taskID": TASK, "appID": "app1", "status": 11, "startTime": "2026-09-30T11:00:00.000Z",
             "details": [{"message": "x" * 1000, "detailCreatedDate": "2026-09-30T11:01:00.000Z"}]},
            {"taskID": OTHER, "appID": "app2", "status": 6, "startTime": "2026-09-30T10:00:00.000Z", "details": []},
        ]
        qrs = FakeQrs({"/qrs/executionresult/full": runs})
        repo = FakeRepo(names={f"Task::{TASK}": "Reload Sales", "App::app1": "Sales"})
        result = monitor(qrs, repo).task_failures(hours=9999)
        self.assertEqual(result["window_hours"], 168)
        self.assertIn("startTime ge '2026-09-23T12:00:00.000Z'", qrs.calls[0][1])
        self.assertEqual(result["failed_tasks"], 2)
        first = result["tasks"][0]
        self.assertEqual((first["task"], first["failures"], first["last_status"]), ("Reload Sales", 2, "Error"))
        self.assertLessEqual(len(first["last_messages"][0]), 300)

    def test_upcoming_tasks(self):
        qrs = FakeQrs({"/qrs/reloadtask/full": [
            {"id": TASK, "name": "B", "operational": {"nextExecution": "2026-09-30T14:00:00.000Z"}},
            {"id": OTHER, "name": "A", "operational": {"nextExecution": "2026-09-30T12:30:00.000Z"}},
        ]})
        result = monitor(qrs).upcoming_tasks(hours=0)
        self.assertEqual(result["window_hours"], 1)
        self.assertEqual([t["task"] for t in result["tasks"]], ["A", "B"])

    def test_qrs_failure_is_reported_not_guessed(self):
        mon = monitor(FakeQrs(fail=True))
        for result in (mon.running_tasks(), mon.task_failures(), mon.upcoming_tasks(), mon.task_status(TASK)):
            self.assertEqual(result["status"], "unavailable")

    def test_helpers(self):
        self.assertEqual(clamp("abc", (1, 48), 6), 6)
        self.assertEqual(clean_message("token: abc123 ok"), "token=*** ok")


class AgentToolTests(unittest.TestCase):
    def test_live_tools_unavailable_when_disabled(self):
        tools = AgentTools()
        self.assertEqual(tools.dispatch("live_running_tasks", {})["status"], "unavailable")

    def test_dispatch_only_allows_advertised_tools(self):
        tools = AgentTools(live_tasks=monitor(FakeQrs()))
        for name in ("live_tasks", "_with_app_status", "dispatch"):
            with self.assertRaises(ValueError):
                tools.dispatch(name, {})


class TaskChainTests(unittest.TestCase):
    def test_enabled_known_tasks_only(self):
        events = [
            {"enabled": True, "reloadTask": {"id": TASK},
             "compositeRules": [{"ruleState": 1, "reloadTask": {"id": OTHER}},
                                {"ruleState": 2, "reloadTask": {"id": "unknown-task"}}]},
            {"enabled": False, "reloadTask": {"id": OTHER}, "compositeRules": [{"ruleState": 1, "reloadTask": {"id": TASK}}]},
        ]
        edges = LineageBuilder().build_task_chain_edges(events, {TASK, OTHER})
        self.assertEqual(edges, [GraphEdge("Task", OTHER, "TRIGGERS", "Task", TASK, {"on": "success"})])

    def test_chain_edges_kept_when_compositeevent_fetch_fails(self):
        class Qrs:
            def fetch_apps(self): return []
            def fetch_reload_tasks(self): return []
            def fetch_task_dependencies(self): raise TimeoutError("slow")
            def fetch_data_connections(self): return []
            def fetch_owners(self): return []
            def fetch_streams(self): return []

        class Repo:
            replaced = False
            def start_scan(self, mode): return 1
            def finish_scan(self, *a): pass
            def upsert_edges(self, edges): return 0
            def replace_relation_edges(self, *a): Repo.replaced = True
            def prune_orphan_nodes(self): return {}
            def __getattr__(self, name): return lambda *a, **k: []

        class Graph:
            def __getattr__(self, name): return lambda *a, **k: 0

        orch = ScannerOrchestrator(qrs=Qrs(), engine=None, repository=Repo(), neo4j=Graph(),
                                   parser=None, builder=LineageBuilder())
        orch.sync_app_statuses = lambda *a: {"counts": {}}
        result = orch.run("full")
        self.assertFalse(Repo.replaced)
        self.assertNotIn("task_chains", result["stats"])


if __name__ == "__main__":
    unittest.main()
