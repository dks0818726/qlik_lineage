from __future__ import annotations

from app.models.entities import ScanObject


def detect_changes(
    previous: list[ScanObject], current: list[ScanObject]
) -> dict[str, list[ScanObject]]:
    previous_by_id = {item.object_id: item for item in previous}
    current_by_id = {item.object_id: item for item in current}

    created = [item for key, item in current_by_id.items() if key not in previous_by_id]
    deleted = [item for key, item in previous_by_id.items() if key not in current_by_id]

    updated: list[ScanObject] = []
    for key, current_item in current_by_id.items():
        prev_item = previous_by_id.get(key)
        if prev_item and prev_item.hash_value != current_item.hash_value:
            updated.append(current_item)

    return {"created": created, "updated": updated, "deleted": deleted}
