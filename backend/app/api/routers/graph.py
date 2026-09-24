from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.dependencies import get_neo4j, get_repository

router = APIRouter(prefix="/graph", tags=["graph"])

# /impact-report renders directly in the UI rather than an LLM prompt, so it isn't
# subject to the agent tools' context-window path/node caps (see Neo4jClient._MAX_PATHS).
# Paths/nodes are now deduped per target, so this only bounds pathological graphs.
_REPORT_LIMIT = 5000


@router.get("/upstream")
def upstream(node_type: str, node_id: str, depth: int = Query(default=5, ge=1, le=20)) -> dict[str, object]:
    chains = get_neo4j().upstream(node_type, node_id, depth)
    return {"node": {"type": node_type, "id": node_id}, "chains": chains}


@router.get("/downstream")
def downstream(node_type: str, node_id: str, depth: int = Query(default=5, ge=1, le=20)) -> dict[str, object]:
    chains = get_neo4j().downstream(node_type, node_id, depth)
    return {"node": {"type": node_type, "id": node_id}, "chains": chains}


@router.get("/impact")
def impact(node_type: str, node_id: str, depth: int = Query(default=5, ge=1, le=20)) -> dict[str, object]:
    nodes = get_neo4j().impact(node_type, node_id, depth)
    return {"node": {"type": node_type, "id": node_id}, "impacted": nodes}


@router.get("/impact-report")
def impact_report(
    node_type: str,
    node_ref: str,
    depth: int = Query(default=5, ge=1, le=20),
) -> dict[str, object]:
    """Impact analysis that accepts a human-typed name instead of an exact graph id.

    `/graph/impact` requires the precise node id - an app GUID, a full `lib://...`
    QVD path, or a connection-prefixed table id. Anything else returned an empty
    result with no explanation. This resolves the reference first and, when it is
    ambiguous or unknown, returns the candidate list so the caller can disambiguate
    instead of being shown a silent blank result.
    """
    repo = get_repository()
    ref = (node_ref or "").strip()
    if not ref:
        return {"status": "not_found", "candidates": [],
                "message": "Enter a name or id to analyze."}

    resolved = repo.resolve_node(node_type, ref)
    if resolved is None:
        candidates = repo.search_nodes(ref, type_filter=node_type, limit=25)
        return {
            "status": "ambiguous" if candidates else "not_found",
            "candidates": candidates,
            "message": (
                f"{len(candidates)} {node_type} objects match '{ref}'. Select the one you meant."
                if candidates
                else f"No {node_type} found matching '{ref}'."
            ),
        }

    node_id = resolved["id"]
    neo = get_neo4j()
    # This is a human-facing report (not an LLM tool call), so it is not bound by
    # the agent's context-window path/node caps - request the full set instead.
    raw = neo.impact_scope(node_type, node_id, depth, node_limit=_REPORT_LIMIT)
    meta = raw[0] if raw and "_totals_by_type" in raw[0] else {"_totals_by_type": []}
    impacted = [r for r in raw if "_totals_by_type" not in r]

    ids_by_type: dict[str, list[str]] = {}
    for row in impacted:
        ids_by_type.setdefault(row["type"], []).append(row["id"])
    names = repo.display_names(ids_by_type)

    grouped: dict[str, list[dict[str, str]]] = {}
    for row in impacted:
        grouped.setdefault(row["type"], []).append(
            {"id": row["id"], "name": names.get(f"{row['type']}::{row['id']}") or row["id"]}
        )
    for items in grouped.values():
        items.sort(key=lambda x: x["name"].lower())

    totals = {t["type"]: t["total"] for t in meta.get("_totals_by_type", [])}

    # Connections/owners/streams are only ever edge targets, so the nodes that depend
    # on them are reached by traversing inbound. Presenting those as "upstream" would
    # be backwards - apps do not feed a connection, they consume it.
    inbound_only = node_type in {"Connection", "Owner", "Stream", "Schedule"}
    if inbound_only:
        upstream: list[dict[str, object]] = []
        downstream = _named_chains(repo, neo.upstream(node_type, node_id, depth, max_paths=_REPORT_LIMIT))
    else:
        upstream = _named_chains(repo, neo.upstream(node_type, node_id, depth, max_paths=_REPORT_LIMIT))
        downstream = _named_chains(repo, neo.downstream(node_type, node_id, depth, max_paths=_REPORT_LIMIT))

    return {
        "status": "ok",
        "node": {"type": node_type, "id": node_id, "name": resolved.get("name") or node_id},
        "summary": {
            "total_impacted": sum(totals.values()),
            "by_type": totals,
            "upstream_paths": len(upstream),
            "downstream_paths": len(downstream),
            "truncated": sum(totals.values()) > len(impacted),
        },
        "impacted": grouped,
        "upstream": upstream,
        "downstream": downstream,
    }


def _named_chains(repo, chains: list[dict[str, object]]) -> list[dict[str, object]]:
    """Attach display names to every node in a traversal chain."""
    ids_by_type: dict[str, list[str]] = {}
    for row in chains:
        for node in row.get("chain") or []:
            ids_by_type.setdefault(node["type"], []).append(node["id"])
    names = repo.display_names(ids_by_type)
    return [
        {
            "depth": row.get("depth"),
            "chain": [
                {
                    "type": n["type"],
                    "id": n["id"],
                    "name": names.get(f"{n['type']}::{n['id']}") or n["id"],
                }
                for n in (row.get("chain") or [])
            ],
        }
        for row in chains
    ]


@router.get("/neighborhood")
def neighborhood(node_type: str, node_id: str, depth: int = Query(default=2, ge=1, le=5)) -> dict[str, object]:
    return get_neo4j().neighborhood(node_type, node_id, depth)


@router.get("/lineage")
def lineage(
    node_type: str,
    node_id: str,
    up_depth: int = Query(default=3, ge=1, le=10),
    down_depth: int = Query(default=3, ge=1, le=10),
) -> dict[str, object]:
    """Directed upstream + downstream of one node, with display names resolved.

    Preferred over `/graph/neighborhood` for visualization: that endpoint walks
    undirected hops and returns the surrounding cluster, which is both slow to
    render and hard to read. Node ids are opaque GUIDs for apps and tasks, so
    names are looked up in Postgres and attached here rather than in the client.
    """
    scope = get_neo4j().lineage_scope(node_type, node_id, up_depth, down_depth)
    nodes = scope["nodes"]

    ids_by_type: dict[str, list[str]] = {}
    for node in nodes:
        ids_by_type.setdefault(node["type"], []).append(node["id"])
    names = get_repository().display_names(ids_by_type)

    for node in nodes:
        node["name"] = names.get(f"{node['type']}::{node['id']}") or node["id"]

    return {
        "node": {"type": node_type, "id": node_id},
        "nodes": nodes,
        "edges": scope["edges"],
        "counts": {
            "nodes": len(nodes),
            "edges": len(scope["edges"]),
            "upstream": sum(1 for n in nodes if n["direction"] == "upstream"),
            "downstream": sum(1 for n in nodes if n["direction"] == "downstream"),
        },
    }


class CypherRequest(BaseModel):
    statement: str


@router.post("/cypher")
def cypher(payload: CypherRequest) -> dict[str, object]:
    try:
        rows = get_neo4j().run_cypher(payload.statement)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {"rows": rows, "count": len(rows)}
