from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    APP = "App"
    QVD = "QVD"
    TABLE = "Table"
    CONNECTION = "Connection"
    TASK = "Task"
    OWNER = "Owner"
    STREAM = "Stream"
    SCHEDULE = "Schedule"


class RelationType(str, Enum):
    READS = "READS"
    WRITES = "WRITES"
    USES = "USES"
    RUNS = "RUNS"
    DEPENDS_ON = "DEPENDS_ON"
    OWNS = "OWNS"
    BELONGS_TO = "BELONGS_TO"
    SCHEDULED_BY = "SCHEDULED_BY"


@dataclass(frozen=True)
class ParsedDependency:
    app_id: str
    connection: str | None = None
    source_table: str | None = None
    output_qvd: str | None = None
    input_qvd: str | None = None
    resident_table: str | None = None
    include_file: str | None = None
    statement: str | None = None  # raw source line / fragment for traceability


@dataclass(frozen=True)
class GraphEdge:
    source_type: str
    source_id: str
    relation: str
    target_type: str
    target_id: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScanObject:
    object_id: str
    hash_value: str
    object_type: str


@dataclass
class QlikApp:
    app_id: str
    name: str
    owner_id: str | None = None
    stream_id: str | None = None
    script_hash: str | None = None
    modified_at: str | None = None


@dataclass
class QlikTask:
    task_id: str
    name: str
    app_id: str | None = None
    schedule_id: str | None = None
    depends_on_task_id: str | None = None


@dataclass
class QlikConnection:
    connection_id: str
    name: str
    connection_type: str | None = None


@dataclass
class LineageEvent:
    event_type: str  # node.created | node.updated | node.deleted | edge.upserted | scan.started | scan.finished
    payload: dict[str, Any]
    timestamp: str


# Relations derived from an app's load script. Only these are pruned when a script
# changes; metadata relations (OWNS, RUNS, BELONGS_TO) come from QRS, not the script,
# and must survive a re-parse - if a QRS task fetch fails, deleting RUNS here would
# strip schedule lineage that nothing would restore.
SCRIPT_DERIVED_RELATIONS = ("READS", "WRITES", "USES", "DEPENDS_ON")
