"""Shared storage utilities for JSON data files.

Extracted to avoid duplication between reactor, commenter, and follower.
All modules need to load/save bounded sets of IDs.

Note: Hashnode uses string IDs (ObjectId), not integers like dev.to.

Atomic writes: Uses temp file + rename to prevent corruption if process
crashes mid-write. This is critical for cron jobs that run concurrently.
"""

import json
import logging
import os
import tempfile
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
        if not isinstance(data, dict):
            logger.warning("Unexpected JSON format in %s", path.name)
            return set()
        return set(str(id_) for id_ in data.get(key, []))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load %s: %s", path.name, e)
        return set()


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON data atomically using temp file + rename.

    Prevents data corruption if the process crashes mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to temp file in same directory (same filesystem for atomic rename)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, suffix=".tmp", prefix=f".{path.stem}_",
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)  # Atomic on POSIX
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_json_ids(
    path: Path, ids: set[str], max_count: int, key: str = "post_ids",
) -> None:
    """Save a bounded set of string IDs to a JSON file.

    Uses atomic write to prevent corruption. Keeps only the most recent
    entries if over max_count. Preserves insertion order rather than
    sorting alphabetically — alphabetical sort kept wrong entries when trimming.
    """
    # Load existing ordered list to preserve insertion order
    existing: list[str] = []
    if path.exists():
        try:
            data = json.loads(path.read_text())
            existing = data.get(key, [])
        except (json.JSONDecodeError, OSError):
            existing = []

    # Add new IDs (preserve existing order, append new at end)
    existing_set = set(existing)
    for id_ in ids:
        if id_ not in existing_set:
            existing.append(id_)

    # Trim to max, keeping most recent (end of list)
    if len(existing) > max_count:
        existing = existing[-max_count:]

    _atomic_write_json(path, {key: existing, "count": len(existing)})
    logger.info("Saved %d IDs to %s.", len(existing), path.name)


def trim_jsonl_file(path: Path, max_lines: int) -> None:
    """Trim a JSONL file to max_lines entries. Atomic write."""
    if not path.exists():
        return
    lines = [l for l in path.read_text().strip().split("\n") if l.strip()]
    if len(lines) > max_lines:
        trimmed = lines[-max_lines:]
        atomic_write_json_lines(path, trimmed)


def atomic_write_json_lines(path: Path, lines: list[str]) -> None:
    """Atomically write a list of JSON lines to a JSONL file."""
    content = "\n".join(lines) + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
