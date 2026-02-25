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


def atomic_write_json(path: Path, data: dict | list) -> None:
    """Write JSON data atomically using temp file + rename.

    Prevents data corruption if the process crashes mid-write.
    Accepts both dict and list payloads.
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


# Keep private alias for backward compatibility with existing call sites.
_atomic_write_json = atomic_write_json


def trim_jsonl(path: Path, max_lines: int) -> None:
    """Trim a JSONL file to at most max_lines entries. Best-effort atomic write.

    Reads the file, keeps the most recent max_lines non-empty lines, and
    atomically replaces the file. Never raises — filesystem errors are logged
    as warnings so callers can continue safely.

    Args:
        path: Path to the JSONL file to trim.
        max_lines: Maximum number of lines to retain (keeps newest).
    """
    if not path.exists():
        return
    try:
        lines = [line for line in path.read_text().strip().split("\n") if line.strip()]
        if len(lines) <= max_lines:
            return
        trimmed = lines[-max_lines:]
        content = "\n".join(trimmed) + "\n"
        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=path.parent, suffix=".tmp", prefix=f".{path.stem}_",
            )
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp_path, path)
            tmp_path = None  # consumed by replace — no cleanup needed
        except Exception:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise
        logger.info(
            "Trimmed %s: %d -> %d entries.",
            path.name, len(lines), len(trimmed),
        )
    except Exception as e:
        logger.warning("trim_jsonl(%s) failed — non-fatal: %s", path.name, e)


def save_json_ids(
    path: Path, ids: set[str], max_count: int, key: str = "post_ids",
) -> None:
    """Save a bounded set of string IDs to a JSON file.

    Uses atomic write to prevent corruption. Keeps only the most recent
    entries if over max_count.
    """
    ids_list = sorted(ids)
    if len(ids_list) > max_count:
        ids_list = ids_list[-max_count:]
    atomic_write_json(path, {key: ids_list, "count": len(ids_list)})
    logger.info("Saved %d IDs to %s.", len(ids_list), path.name)
