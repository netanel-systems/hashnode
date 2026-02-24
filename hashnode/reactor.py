"""ReactionEngine — like articles on Hashnode.

Standalone cron entry point. Runs every hour via Python (no LLM needed).
Finds articles from RELEVANT + RECENT feeds, likes with weighted counts,
logs everything to engagement_log.jsonl.

Pure API — no browser automation needed (unlike dev.to).

Usage:
    python -m hashnode.reactor
"""

import json
import logging
import random
import sys
import time
from datetime import datetime, timezone

from hashnode.client import HashnodeClient, HashnodeError
from hashnode.config import HashnodeConfig, load_config
from hashnode.learner import GrowthLearner
from hashnode.scout import ArticleScout
from hashnode.storage import load_json_ids, save_json_ids

logger = logging.getLogger(__name__)

# Weighted like counts (1-5). Higher counts for better articles.
# Weighted toward 3-4 for authentic feel.
LIKE_WEIGHTS: list[tuple[int, int]] = [
    (1, 10),   # 10% — minimal engagement
    (2, 15),   # 15%
    (3, 35),   # 35% — most common
    (4, 25),   # 25%
    (5, 15),   # 15% — strong appreciation
]


def pick_like_count() -> int:
    """Weighted random pick of like count (1-5)."""
    counts, weights = zip(*LIKE_WEIGHTS)
    return random.choices(counts, weights=weights, k=1)[0]


class ReactionEngine:
    """Likes trending Hashnode articles. Runs as standalone cron job.

    Pure API — calls likePost mutation directly. No browser needed.
    """

    def __init__(self, config: HashnodeConfig) -> None:
        self.config = config
        self.client = HashnodeClient(config)
        self.scout = ArticleScout(self.client, config)
        self.learner = GrowthLearner(config)
        self.data_dir = config.abs_data_dir

    def load_reacted_ids(self) -> set[str]:
        """Load post IDs we already reacted to."""
        return load_json_ids(self.data_dir / "reacted.json")

    def save_reacted_ids(self, reacted_ids: set[str]) -> None:
        """Save reacted IDs, bounded to max_reacted_history."""
        save_json_ids(
            self.data_dir / "reacted.json", reacted_ids,
            max_count=self.config.max_reacted_history,
        )

    def load_commented_ids(self) -> set[str]:
        """Load post IDs we already commented on (for filtering)."""
        return load_json_ids(self.data_dir / "commented.json")

    def filter_learner_candidates(self, candidates: list[dict]) -> list[dict]:
        """Filter out articles whose tags are marked skip by the learner.

        Checks every tag slug on each article. If ALL tags are skip-listed,
        the article is dropped. If any tag is not skip-listed, it stays.
        Articles with no tags are kept (no basis to filter).

        All learner errors are caught — never crashes the main cycle.
        """
        try:
            filtered: list[dict] = []
            skipped_count = 0
            for article in candidates:
                tags = article.get("tags", [])
                tag_slugs = [
                    t.get("slug", t.get("name", str(t))) if isinstance(t, dict) else str(t)
                    for t in tags
                    if t
                ]
                if not tag_slugs:
                    filtered.append(article)
                    continue
                # Keep article if at least one tag is NOT skip-listed
                try:
                    all_skipped = all(
                        self.learner.should_skip_tag(slug) for slug in tag_slugs
                    )
                except Exception as lerr:
                    logger.warning("Learner tag check failed — keeping article: %s", lerr)
                    all_skipped = False
                if all_skipped:
                    skipped_count += 1
                else:
                    filtered.append(article)
            if skipped_count:
                logger.info(
                    "Learner filtered %d low-performing-tag articles.", skipped_count,
                )
            return filtered
        except Exception as e:
            logger.warning("filter_learner_candidates failed — returning unfiltered: %s", e)
            return candidates

    def log_engagement(self, action: str, article: dict, details: dict) -> None:
        """Append to engagement_log.jsonl — full audit trail."""
        path = self.data_dir / "engagement_log.jsonl"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tags = article.get("tags", [])
        tag_names = [
            t.get("slug", t.get("name", str(t))) if isinstance(t, dict) else str(t)
            for t in tags
        ]
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "post_id": article.get("id", ""),
            "title": (article.get("title") or "")[:100],
            "author": article.get("author", {}).get("username", ""),
            "tags": tag_names,
            **details,
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def trim_engagement_log(self) -> None:
        """Trim engagement log to max_engagement_log entries. Atomic write."""
        import os
        import tempfile

        path = self.data_dir / "engagement_log.jsonl"
        if not path.exists():
            return
        lines = [l for l in path.read_text().strip().split("\n") if l.strip()]
        if len(lines) > self.config.max_engagement_log:
            trimmed = lines[-self.config.max_engagement_log:]
            content = "\n".join(trimmed) + "\n"
            fd, tmp_path = tempfile.mkstemp(
                dir=path.parent, suffix=".tmp", prefix=".engagement_",
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
            logger.info(
                "Trimmed engagement log: %d -> %d entries.",
                len(lines), len(trimmed),
            )

    def run(self) -> dict:
        """Main entry point for cron. Finds articles, likes, logs.

        Returns summary dict with counts for monitoring.
        """
        logger.info("=== Hashnode reaction cycle starting ===")
        start = time.time()

        try:
            reacted_ids = self.load_reacted_ids()
            commented_ids = self.load_commented_ids()
            max_reactions = self.config.max_reactions_per_run

            # Collect candidates from multiple feed types
            seen_ids: set[str] = set()
            candidates: list[dict] = []
            max_attempts = 3

            for attempt in range(max_attempts):
                if attempt > 0:
                    self.scout.refresh_tags()

                relevant = self.scout.find_relevant_articles(count=max_reactions)
                recent = self.scout.find_recent_articles(count=max_reactions)

                for article in relevant + recent:
                    aid = article.get("id", "")
                    if aid and aid not in seen_ids:
                        seen_ids.add(aid)
                        candidates.append(article)

                candidates = self.scout.filter_own_articles(candidates)
                candidates = self.scout.filter_already_engaged(
                    candidates, reacted_ids, commented_ids,
                )
                candidates = self.filter_learner_candidates(candidates)

                if len(candidates) >= max_reactions:
                    break

                logger.info(
                    "Attempt %d: %d candidates (need %d). Refreshing tags...",
                    attempt + 1, len(candidates), max_reactions,
                )

            reacted_count = 0
            skipped_count = 0
            failed_count = 0
            new_reacted: set[str] = set()

            for idx, article in enumerate(candidates[:max_reactions]):
                aid = article.get("id", "")
                if not aid:
                    skipped_count += 1
                    continue

                likes = pick_like_count()

                try:
                    self.client.like_post(aid, likes_count=likes)
                    reacted_count += 1
                    new_reacted.add(aid)
                    self.log_engagement("reaction", article, {
                        "likes_count": likes,
                    })
                except HashnodeError as e:
                    failed_count += 1
                    error_str = str(e).lower()
                    if "rate" in error_str or "429" in error_str:
                        logger.info("Rate limited. Stopping early.")
                        break
                    logger.warning("Like failed on %s: %s", aid, e)

                # Delay between reactions for rate-limit safety
                if idx < len(candidates[:max_reactions]) - 1:
                    time.sleep(self.config.reaction_delay)

            # Save updated reacted IDs
            reacted_ids.update(new_reacted)
            self.save_reacted_ids(reacted_ids)

            # Periodic log trimming
            self.trim_engagement_log()

            # Extract patterns from this cycle's data
            new_learnings = 0
            try:
                new_learnings = self.learner.analyze()
                logger.info("Learner extracted %d new patterns.", new_learnings)
            except Exception as e:
                logger.warning("Learner analyze failed — non-fatal: %s", e)

            elapsed = time.time() - start
            summary = {
                "reacted": reacted_count,
                "skipped": skipped_count,
                "failed": failed_count,
                "candidates": len(candidates),
                "new_learnings": new_learnings,
                "elapsed_seconds": round(elapsed, 1),
            }
            logger.info(
                "=== Reaction cycle complete: %d reacted, %d failed, %.1fs ===",
                reacted_count, failed_count, elapsed,
            )
            return summary

        except HashnodeError as e:
            logger.exception("Reaction engine failed: %s", e)
            return {"error": str(e), "elapsed_seconds": round(time.time() - start, 1)}


def main() -> None:
    """CLI entry point for cron."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        config = load_config()
        engine = ReactionEngine(config)
        summary = engine.run()
        print(json.dumps(summary, indent=2))
        if "error" in summary:
            logger.error("Cycle completed with error.")
            sys.exit(1)
    except HashnodeError as e:
        logger.error("Reaction engine failed: %s", e)
        sys.exit(1)
    except Exception as e:
        logger.error("Unexpected error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
