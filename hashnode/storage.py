"""Shared storage utilities for JSON data files.

Extracted to avoid duplication between reactor, commenter, and follower.
All modules need to load/save bounded sets of IDs.

Note: Hashnode uses string IDs (ObjectId), not integers like dev.to.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_json_ids(path: Path, key: str = "post_ids") -> set[str]:
    """Load a set of string IDs from a JSON file.

    Returns empty set if file doesn't exist or is corrupted.
    """
    if not path.exists():
        return set()
    try:
        with open(path) as f:
            data = json.load(f)
        return set(str(id_) for id_ in data.get(key, []))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load %s: %s", path.name, e)
        return set()


def save_json_ids(
    path: Path, ids: set[str], max_count: int, key: str = "post_ids",
) -> None:
    """Save a bounded set of string IDs to a JSON file.

    Keeps only the most recent entries if over max_count.
    """
    ids_list = sorted(ids)
    if len(ids_list) > max_count:
        ids_list = ids_list[-max_count:]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({key: ids_list, "count": len(ids_list)}, f, indent=2)
    logger.info("Saved %d IDs to %s.", len(ids_list), path.name)
