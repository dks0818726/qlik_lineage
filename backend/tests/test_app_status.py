"""Tests for app status classification (live / dev_copy / stale / unscheduled)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.scanner.app_status import (
    DEV_COPY, LIVE, STALE, UNSCHEDULED,
    base_name, classify_apps, is_copy_name, parse_qrs_time, summarize_tasks,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
NULL_GUID = "00000000-0000-0000-0000-000000000000"


def _ts(days_ago: int | None) -> str:
    if days_ago is None:
        return "1753-01-01T00:00:00.000Z"
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def app(app_id, name, *, reload_days=5, published=False, target=NULL_GUID, stream=None):
    return {
        "id": app_id, "name": name, "published": published,
        "lastReloadTime": _ts(reload_days), "targetAppId": target,
        "publishTime": _ts(10) if published else _ts(None),
        "createdDate": _ts(400), "modifiedByUserName": "CORPORATE\\dev1",
        "owner": {"id": "o1", "userId": "dev1", "userDirectory": "CORPORATE", "name": "Dev One"},
        "stream": {"id": "s1", "name": stream} if stream else None,
        "tags": [{"name": "ERA"}], "fileSize": 1234,
    }


def task(app_id, *, enabled=True, status=7, days_ago=1):
    return {
        "app": {"id": app_id}, "enabled": enabled,
        "operational": {"lastExecutionResult": {"status": status, "stopTime": _ts(days_ago)}},
    }


def classify(apps, tasks=(), stale_days=90):
    return {s.app_id: s for s in classify_apps(list(apps), list(tasks), stale_days=stale_days, now=NOW)}


class TestNameHelpers(unittest.TestCase):
    def test_copy_suffixes(self) -> None:
        for name in ["Sales(1)", "Sales (2)", "Sales(2)(1)", "MCH - GM Decomp 2.0(2)(1)"]:
            self.assertTrue(is_copy_name(name), name)
        for name in ["Sales", "EBIR - ECOMM - DEMAND (v1.2)", "Report 2024", "Sales(v2)"]:
            self.assertFalse(is_copy_name(name), name)

    def test_base_name_groups_copies(self) -> None:
        self.assertEqual(base_name("ATH - Extract Dimensions(6)(1)"), "ath - extract dimensions")
        self.assertEqual(base_name("EBIR - ECOMM - DEMAND (v1.2)(6)"), "ebir - ecomm - demand (v1.2)")

    def test_qrs_null_date_is_none(self) -> None:
        self.assertIsNone(parse_qrs_time("1753-01-01T00:00:00.000Z"))
        self.assertIsNone(parse_qrs_time(""))
        self.assertIsNone(parse_qrs_time("not a date"))
        self.assertEqual(parse_qrs_time("2026-09-27T12:00:00.000Z"), NOW)


class TestClassification(unittest.TestCase):
    def test_published_app_is_live_even_when_old(self) -> None:
        s = classify([app("a", "Exec Dashboard", published=True, reload_days=400, stream="Finance")])["a"]
        self.assertEqual(s.app_status, LIVE)
        self.assertIn("Finance", s.status_reason)
        self.assertIn("out of date", s.status_reason)

    def test_scheduled_and_recent_is_live(self) -> None:
        s = classify([app("a", "Extract")], [task("a")])["a"]
        self.assertEqual(s.app_status, LIVE)
        self.assertTrue(s.has_enabled_task)
        self.assertEqual(s.last_task_status, "FinishedSuccess")

    def test_copy_linked_to_original(self) -> None:
        result = classify(
            [app("orig", "ATH - Extract Dimensions", reload_days=0),
             app("c1", "ATH - Extract Dimensions(7)", reload_days=11),
             app("c2", "ATH - Extract Dimensions(6)(1)", reload_days=200)],
            [task("orig")],
        )
        self.assertEqual(result["orig"].app_status, LIVE)
        self.assertIsNone(result["orig"].original_app_id)
        for cid in ("c1", "c2"):
            self.assertEqual(result[cid].app_status, DEV_COPY)
            self.assertTrue(result[cid].is_copy)
            self.assertEqual(result[cid].original_app_id, "orig")
            self.assertIn("Copy of 'ATH - Extract Dimensions'", result[cid].status_reason)

    def test_scheduled_copy_stays_live(self) -> None:
        # 36 real "(n)" apps have enabled tasks; hiding them would hide real lineage.
        s = classify([app("c", "Sales(1)")], [task("c")])["c"]
        self.assertEqual(s.app_status, LIVE)
        self.assertTrue(s.is_copy)

    def test_target_app_id_marks_dev_copy(self) -> None:
        s = classify([app("dev", "Sales Dashboard", target="pub-id")])["dev"]
        self.assertEqual(s.app_status, DEV_COPY)
        self.assertEqual(s.original_app_id, "pub-id")
        self.assertIn("published app", s.status_reason)

    def test_stale_threshold_is_configurable(self) -> None:
        apps = [app("a", "Old Report", reload_days=120)]
        self.assertEqual(classify(apps, stale_days=90)["a"].app_status, STALE)
        self.assertEqual(classify(apps, stale_days=180)["a"].app_status, UNSCHEDULED)

    def test_never_reloaded_is_stale(self) -> None:
        s = classify([app("a", "Empty", reload_days=None)])["a"]
        self.assertEqual(s.app_status, STALE)
        self.assertIsNone(s.last_reload_at)
        self.assertIn("never reloaded", s.status_reason)

    def test_enabled_task_not_reloading_is_stale(self) -> None:
        s = classify([app("a", "Broken", reload_days=150)], [task("a", status=8, days_ago=150)])["a"]
        self.assertEqual(s.app_status, STALE)
        self.assertIn("not reloading", s.status_reason)
        self.assertIn("FinishedFail", s.status_reason)

    def test_disabled_task_does_not_count(self) -> None:
        s = classify([app("a", "Manual", reload_days=3)], [task("a", enabled=False)])["a"]
        self.assertEqual(s.app_status, UNSCHEDULED)
        self.assertEqual(s.task_count, 1)
        self.assertFalse(s.has_enabled_task)

    def test_copies_without_original_have_none(self) -> None:
        result = classify([app("c1", "Gone(1)"), app("c2", "Gone(2)")])
        self.assertIsNone(result["c1"].original_app_id)
        self.assertEqual(result["c1"].status_reason.split(";")[0], "Named like a duplicate")

    def test_original_prefers_published(self) -> None:
        result = classify([
            app("unpub", "Sales", reload_days=1),
            app("pub", "sales", published=True, reload_days=30),
            app("copy", "Sales(1)", reload_days=300),
        ])
        self.assertEqual(result["copy"].original_app_id, "pub")

    def test_metadata_is_captured(self) -> None:
        s = classify([app("a", "X", published=True, stream="S")])["a"]
        self.assertEqual(s.owner_name, "Dev One")
        self.assertEqual(s.owner_user, "CORPORATE\\dev1")
        self.assertEqual(s.tags, ["ERA"])
        self.assertEqual(s.file_size, 1234)
        self.assertIsNotNone(s.published_at)
        self.assertIsNone(s.target_app_id)


class TestTaskSummary(unittest.TestCase):
    def test_latest_run_wins(self) -> None:
        info = summarize_tasks([
            task("a", status=8, days_ago=10),
            task("a", status=7, days_ago=1, enabled=False),
            {"app": None, "enabled": True},  # malformed rows are skipped
        ])
        self.assertEqual(info["a"]["task_count"], 2)
        self.assertTrue(info["a"]["has_enabled_task"])
        self.assertEqual(info["a"]["last_task_status"], "FinishedSuccess")


class TestOrchestratorStatusSync(unittest.TestCase):
    """The scan must refresh statuses for all apps, and never from a partial task list."""

    def _orchestrator(self, tasks_fail: bool):
        from unittest.mock import MagicMock
        from app.scanner.orchestrator import ScannerOrchestrator

        qrs = MagicMock()
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        apps = [app("a", "Sales"), app("b", "Sales(1)")]
        for a in apps:
            a["lastReloadTime"] = recent
        qrs.fetch_apps.return_value = apps
        if tasks_fail:
            qrs.fetch_reload_tasks.side_effect = TimeoutError("QRS timed out")
        else:
            qrs.fetch_reload_tasks.return_value = [task("a")]
        for name in ("fetch_data_connections", "fetch_owners", "fetch_streams"):
            getattr(qrs, name).return_value = []
        repo = MagicMock()
        repo.list_apps.return_value = []
        repo.prune_orphan_nodes.return_value = {}
        repo.upsert_edges.return_value = 0
        neo = MagicMock()
        builder = MagicMock()
        builder.build_task_edges.return_value = []
        orch = ScannerOrchestrator(qrs=qrs, engine=MagicMock(), repository=repo, neo4j=neo,
                                   parser=MagicMock(), builder=builder, stale_days=90)
        orch._process_app = MagicMock(return_value=(0, 0))
        return orch, repo, neo

    def test_scan_classifies_every_app(self) -> None:
        orch, repo, neo = self._orchestrator(tasks_fail=False)
        stats = orch.run("delta")["stats"]
        written = {s.app_id: s.app_status for s in repo.update_app_statuses.call_args[0][0]}
        self.assertEqual(written, {"a": LIVE, "b": DEV_COPY})
        neo.set_app_statuses.assert_called_once()
        self.assertEqual(stats["app_status"], {LIVE: 1, DEV_COPY: 1})

    def test_task_fetch_failure_keeps_previous_statuses(self) -> None:
        orch, repo, neo = self._orchestrator(tasks_fail=True)
        stats = orch.run("delta")["stats"]
        repo.update_app_statuses.assert_not_called()
        neo.set_app_statuses.assert_not_called()
        self.assertNotIn("app_status", stats)

    def test_apps_deleted_in_qlik_are_marked_removed(self) -> None:
        orch, repo, _ = self._orchestrator(tasks_fail=False)
        repo.list_apps.return_value = [{"app_id": i} for i in ("a", "b", "gone")]
        repo.mark_apps_removed.return_value = ["gone"]
        result = orch.sync_app_statuses(orch.qrs.fetch_apps(), orch.qrs.fetch_reload_tasks())
        self.assertEqual(set(repo.mark_apps_removed.call_args[0][0]), {"a", "b"})
        self.assertEqual(result["counts"]["removed"], 1)

    def test_partial_qrs_response_does_not_mark_mass_removal(self) -> None:
        orch, repo, _ = self._orchestrator(tasks_fail=False)
        repo.list_apps.return_value = [{"app_id": f"x{i}"} for i in range(100)]
        orch.sync_app_statuses(orch.qrs.fetch_apps(), orch.qrs.fetch_reload_tasks())
        repo.mark_apps_removed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
