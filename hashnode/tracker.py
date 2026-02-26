"""GrowthTracker — follower tracking, reciprocity, and weekly reports.

Tracks:
- Follower count over time (via me query, not REST)
- New followers since last check
- Reciprocity rate (of authors we engaged, who followed back?)
- Weekly growth reports

Data stored in data/weekly_report.json and data/follower_snapshots.jsonl.
"""

import json
import logging
from datetime import datetime, timezone

from hashnode.client import HashnodeClient, HashnodeError
from hashnode.config import HashnodeConfig
from hashnode.learner import GrowthLearner

logger = logging.getLogger(__name__)


class GrowthTracker:
    """Tracks follower growth and engagement reciprocity.

    Uses Hashnode's me query for follower count (no REST endpoint).
    """

    def __init__(
        self,
        client: HashnodeClient,
        config: HashnodeConfig,
        learner: GrowthLearner,
    ) -> None:
        self.client = client
        self.config = config
        self.learner = learner
        self.data_dir = config.abs_data_dir

    def check_followers(self) -> dict:
        """Get current follower count and detect changes.

        Uses me query for follower count. Attempts to fetch follower
        usernames via get_followers() for username-level attribution
        (X1-snapshots). Falls back to count-only if schema does not
        support the followers query.

        Returns: {current_count, previous_count, delta, follower_usernames}
        """
        try:
            me = self.client.get_me()
            current_count = me.get("followersCount", 0)
        except HashnodeError as e:
            logger.exception("Failed to fetch follower count")
            previous = self._load_last_snapshot()
            return {
                "current_count": previous.get("count", 0),
                "previous_count": previous.get("count", 0),
                "delta": 0,
                "follower_usernames": [],
                "error": str(e),
            }

        # Attempt to fetch follower usernames (X1-snapshots)
        follower_usernames: list[str] = []
        username = self.config.username
        if username:
            try:
                follower_usernames = self.client.get_followers(username)
            except Exception as e:
                logger.warning("Follower username fetch failed (using count only): %s", e)

        # Load previous snapshot
        previous = self._load_last_snapshot()
        previous_count = previous.get("count", 0)
        delta = current_count - previous_count

        # Save new snapshot (with usernames if available)
        self._save_snapshot(current_count, follower_usernames=follower_usernames)

        if delta > 0:
            logger.info(
                "Followers increased: %d -> %d (+%d)",
                previous_count, current_count, delta,
            )
        elif delta < 0:
            logger.info(
                "Followers decreased: %d -> %d (%d)",
                previous_count, current_count, delta,
            )
        else:
            logger.info("No follower change. Total: %d", current_count)

        return {
            "current_count": current_count,
            "previous_count": previous_count,
            "delta": delta,
            "follower_usernames": follower_usernames,
        }

    def get_reciprocity_rate(self) -> dict:
        """Of authors we engaged with, estimate reciprocity.

        Note: Hashnode doesn't expose individual follower usernames
        easily. We track follower count changes as a proxy.

        Returns: {engaged_authors: N, follower_growth: N}
        """
        engaged_authors = self.learner.get_unique_authors_engaged()
        snapshot = self._load_last_snapshot()

        result = {
            "engaged_authors": len(engaged_authors),
            "current_followers": snapshot.get("count", 0),
        }
        logger.info(
            "Reciprocity stats: %d authors engaged, %d followers",
            len(engaged_authors), snapshot.get("count", 0),
        )
        return result

    def get_weekly_report(self) -> dict:
        """Generate comprehensive weekly growth report.

        Combines engagement data, follower data, and learner insights.
        """
        follower_data = self.check_followers()
        reciprocity = self.get_reciprocity_rate()
        learner_summary = self.learner.generate_weekly_summary()

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "followers": follower_data,
            "reciprocity": reciprocity,
            "engagement": learner_summary,
        }

        # Save report
        self._save_weekly_report(report)
        logger.info("Weekly report generated and saved.")
        return report

    def _load_last_snapshot(self) -> dict:
        """Load the most recent follower snapshot."""
        path = self.data_dir / "follower_snapshots.jsonl"
        if not path.exists():
            return {"count": 0}
        try:
            last_line = ""
            with open(path) as f:
                for line in f:
                    if line.strip():
                        last_line = line.strip()
            if last_line:
                return json.loads(last_line)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load follower snapshot: %s", e)
        return {"count": 0}

    def _save_snapshot(
        self,
        count: int,
        max_snapshots: int = 365,
        follower_usernames: list[str] | None = None,
    ) -> None:
        """Append a new follower snapshot, bounded to max_snapshots entries.

        Keeps last max_snapshots entries to prevent unbounded growth.
        Default 365 = ~1 year of daily snapshots. Uses atomic write.

        Args:
            count: Current follower count.
            max_snapshots: Max entries to keep.
            follower_usernames: Optional list of follower usernames
                (X1-snapshots). Included in snapshot when available.
        """
        import os
        import tempfile

        path = self.data_dir / "follower_snapshots.jsonl"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "count": count,
        }
        if follower_usernames:
            entry["usernames"] = sorted(follower_usernames)

        # Read existing entries
        lines: list[str] = []
        if path.exists():
            try:
                lines = [l for l in path.read_text().strip().split("\n") if l.strip()]
            except OSError:
                lines = []

        # Append new entry
        lines.append(json.dumps(entry))

        # Trim to max_snapshots
        if len(lines) > max_snapshots:
            lines = lines[-max_snapshots:]
            logger.info("Trimmed follower snapshots: kept last %d entries.", max_snapshots)

        # Atomic write
        content = "\n".join(lines) + "\n"
        fd, tmp_path = tempfile.mkstemp(
            dir=path.parent, suffix=".tmp", prefix=".snapshots_",
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _save_weekly_report(self, report: dict) -> None:
        """Save the weekly report to disk. Uses atomic write."""
        from hashnode.storage import _atomic_write_json

        path = self.data_dir / "weekly_report.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, report)
