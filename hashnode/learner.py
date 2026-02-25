"""GrowthLearner — tracks what works and adapts strategy.

Analyzes:
- Which comment styles get engagement
- Which tags yield reciprocity (authors following back)
- Which time slots get most engagement
- Stores patterns as learnings for future cycles

Data sources:
- comment_history.jsonl — our posted comments
- engagement_log.jsonl — all engagement actions
- learnings.json — accumulated insights
"""

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from hashnode.config import HashnodeConfig

logger = logging.getLogger(__name__)


class GrowthLearner:
    """Intelligence layer for the growth engine.

    Tracks performance and generates actionable insights.
    """

    def __init__(self, config: HashnodeConfig) -> None:
        self.config = config
        self.data_dir = config.abs_data_dir

    def load_learnings(self) -> list[dict]:
        """Load accumulated learnings from disk."""
        path = self.data_dir / "learnings.json"
        if not path.exists():
            return []
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load learnings.json: %s", e)
            return []

    def save_learnings(self, learnings: list[dict]) -> None:
        """Save learnings to disk, bounded to max_learnings. Uses atomic write."""
        from hashnode.storage import _atomic_write_json

        path = self.data_dir / "learnings.json"
        if len(learnings) > self.config.max_learnings:
            learnings = learnings[-self.config.max_learnings:]
        self.data_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, learnings)
        logger.info("Saved %d learnings.", len(learnings))

    def store_learning(
        self, pattern: str, confidence: float, evidence: str,
    ) -> None:
        """Persist a single learning insight."""
        learnings = self.load_learnings()
        learnings.append({
            "pattern": pattern,
            "confidence": round(confidence, 2),
            "evidence": evidence,
            "discovered": datetime.now(timezone.utc).isoformat(),
        })
        self.save_learnings(learnings)
        logger.info("Stored learning: %s (confidence=%.2f)", pattern, confidence)

    def get_insights_for_prompt(self, max_insights: int = 5) -> list[str]:
        """Get top learnings as bullet points for comment generation prompt.

        Returns the most recent high-confidence learnings.
        """
        learnings = self.load_learnings()
        learnings.sort(
            key=lambda item: (
                item.get("confidence", 0),
                item.get("discovered", ""),
            ),
            reverse=True,
        )
        insights: list[str] = []
        for learning in learnings[:max_insights]:
            pattern = learning.get("pattern", "")
            confidence = learning.get("confidence", 0)
            if pattern and confidence >= 0.5:
                insights.append(f"- {pattern} (confidence: {confidence})")
        return insights

    def get_engagement_by_tag(self) -> dict[str, dict]:
        """Analyze engagement metrics grouped by tag.

        Returns: {tag: {reactions: N, comments: N, follows: N, total: N}}
        """
        path = self.data_dir / "engagement_log.jsonl"
        if not path.exists():
            return {}

        tag_stats: dict[str, dict] = defaultdict(
            lambda: {"reactions": 0, "comments": 0, "follows": 0, "total": 0}
        )

        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    action = entry.get("action", "")
                    tags = entry.get("tags", [])
                    for tag in tags:
                        tag_name = tag if isinstance(tag, str) else str(tag)
                        if action == "reaction":
                            tag_stats[tag_name]["reactions"] += 1
                        elif action == "comment":
                            tag_stats[tag_name]["comments"] += 1
                        elif action == "follow":
                            tag_stats[tag_name]["follows"] += 1
                        tag_stats[tag_name]["total"] += 1
        except OSError as e:
            logger.warning("Failed to read engagement_log.jsonl: %s", e)

        return dict(tag_stats)

    def get_engagement_by_day(self) -> dict[str, int]:
        """Analyze engagement counts by day of week.

        Returns: {Monday: N, Tuesday: N, ...}
        """
        path = self.data_dir / "engagement_log.jsonl"
        if not path.exists():
            return {}

        day_counts: Counter = Counter()

        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = entry.get("timestamp", "")
                    if ts:
                        try:
                            dt = datetime.fromisoformat(ts)
                            day_name = dt.strftime("%A")
                            day_counts[day_name] += 1
                        except ValueError:
                            continue
        except OSError as e:
            logger.warning("Failed to read engagement_log.jsonl: %s", e)

        return dict(day_counts)

    def get_comment_count(self) -> int:
        """Total comments we've posted."""
        path = self.data_dir / "comment_history.jsonl"
        if not path.exists():
            return 0
        try:
            with open(path) as f:
                return sum(1 for line in f if line.strip())
        except OSError:
            return 0

    def get_reaction_count(self) -> int:
        """Total reactions we've made."""
        path = self.data_dir / "reacted.json"
        if not path.exists():
            return 0
        try:
            with open(path) as f:
                data = json.load(f)
            return data.get("count", 0)
        except (json.JSONDecodeError, OSError):
            return 0

    def get_unique_authors_engaged(self) -> set[str]:
        """Get all unique author usernames we've engaged with."""
        path = self.data_dir / "engagement_log.jsonl"
        if not path.exists():
            return set()

        authors: set[str] = set()
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    author = entry.get("author", "")
                    if author:
                        authors.add(author)
        except OSError as e:
            logger.warning("Failed to read engagement_log.jsonl: %s", e)

        return authors

    def should_skip_tag(self, tag: str) -> bool:
        """Check if a tag should be deprioritized based on learnings."""
        learnings = self.load_learnings()
        for learning in learnings:
            pattern = learning.get("pattern", "").lower()
            if tag.lower() in pattern and "skip" in pattern:
                return learning.get("confidence", 0) >= 0.7
        return False

    # Minimum total engagement events before a tag is considered high-performing.
    ENGAGEMENT_THRESHOLD: int = 5

    def analyze(self, known_tags: list[str] | None = None) -> int:
        """Derive and persist patterns from engagement_log.jsonl.

        Inspects tag performance against a canonical tag list and stores
        actionable learnings:
        - Tags with zero engagement → 'skip <tag>' pattern (confidence 0.8)
        - Tags with high engagement → positive pattern (confidence 0.9)

        Args:
            known_tags: Canonical list of tag slugs to evaluate. Defaults to
                CURATED_TAGS from scout.py. Tags not seen in the engagement log
                are treated as zero-engagement.

        Returns the number of new learnings stored.
        Called automatically after each reaction or comment cycle.
        """
        from hashnode.scout import CURATED_TAGS  # local import — avoids top-level coupling

        tag_list = known_tags if known_tags is not None else CURATED_TAGS
        tag_stats = self.get_engagement_by_tag()

        if not tag_stats and not tag_list:
            logger.info("analyze: no engagement data and no known tags — skipping.")
            return 0

        existing_learnings = self.load_learnings()
        existing_patterns: set[str] = {
            learning.get("pattern", "").lower() for learning in existing_learnings
        }

        stored = 0

        # Iterate canonical tag list. Tags absent from the log have total=0.
        for tag in tag_list:
            stats = tag_stats.get(tag, {"reactions": 0, "comments": 0, "follows": 0, "total": 0})
            total = stats.get("total", 0)

            # Zero-engagement: candidate for skipping
            skip_pattern = f"skip {tag} — zero engagement across all actions"
            if total == 0 and skip_pattern.lower() not in existing_patterns:
                self.store_learning(
                    pattern=skip_pattern,
                    confidence=0.8,
                    evidence=f"Tag '{tag}' has 0 total engagement events in log.",
                )
                existing_patterns.add(skip_pattern.lower())
                stored += 1

            # High-performing: candidate for prioritization
            high_pattern = f"prioritize {tag} — high engagement tag"
            if (
                total >= self.ENGAGEMENT_THRESHOLD
                and high_pattern.lower() not in existing_patterns
            ):
                self.store_learning(
                    pattern=high_pattern,
                    confidence=0.9,
                    evidence=(
                        f"Tag '{tag}' has {total} engagement events: "
                        f"reactions={stats.get('reactions', 0)}, "
                        f"comments={stats.get('comments', 0)}, "
                        f"follows={stats.get('follows', 0)}."
                    ),
                )
                existing_patterns.add(high_pattern.lower())
                stored += 1

        logger.info("analyze: stored %d new learnings.", stored)
        return stored

    def generate_weekly_summary(self) -> dict:
        """Generate comprehensive weekly summary.

        Returns dict with all key metrics for the weekly report.
        """
        return {
            "total_comments": self.get_comment_count(),
            "total_reactions": self.get_reaction_count(),
            "unique_authors": len(self.get_unique_authors_engaged()),
            "engagement_by_tag": self.get_engagement_by_tag(),
            "engagement_by_day": self.get_engagement_by_day(),
            "learnings_count": len(self.load_learnings()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
