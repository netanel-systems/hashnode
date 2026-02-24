"""FollowEngine — auto-follow article authors for reciprocity.

Follows authors of articles we reacted to or commented on.
Tracks followed usernames to never follow the same person twice.
Uses toggleFollowUser mutation — idempotent but we track to avoid wasting calls.
"""

import json
import logging
import time
from datetime import datetime, timezone

from hashnode.client import HashnodeClient, HashnodeError
from hashnode.config import HashnodeConfig
from hashnode.storage import load_json_ids, save_json_ids

logger = logging.getLogger(__name__)


class FollowEngine:
    """Auto-follows article authors for reciprocity growth."""

    def __init__(self, client: HashnodeClient, config: HashnodeConfig) -> None:
        self.client = client
        self.config = config
        self.data_dir = config.abs_data_dir

    def load_followed_usernames(self) -> set[str]:
        """Load usernames we already followed."""
        return load_json_ids(self.data_dir / "followed.json", key="usernames")

    def save_followed_usernames(self, usernames: set[str]) -> None:
        """Save followed usernames, bounded to max_followed_history."""
        save_json_ids(
            self.data_dir / "followed.json", usernames,
            max_count=self.config.max_followed_history,
            key="usernames",
        )

    # Max accidental unfollows before aborting cycle (safety guard)
    MAX_UNFOLLOWS_BEFORE_ABORT = 2

    def follow_cycle(self, articles: list[dict]) -> dict:
        """Follow authors of recently engaged articles.

        Args:
            articles: List of article dicts from scout (must have author field)

        Returns:
            Summary dict with counts

        Safety: If toggleFollowUser accidentally unfollows more than
        MAX_UNFOLLOWS_BEFORE_ABORT users (tracked set was lost/corrupted),
        the cycle aborts to prevent mass-unfollowing.
        """
        logger.info("=== Follow cycle starting ===")
        followed_usernames = self.load_followed_usernames()
        max_follows = self.config.max_follows_per_cycle

        followed_count = 0
        skipped_count = 0
        failed_count = 0
        unfollow_count = 0
        new_follows: set[str] = set()

        for article in articles:
            if followed_count >= max_follows:
                break

            author = article.get("author", {})
            user_id = (author.get("id") or "").strip()
            username = (author.get("username") or "").strip()

            if not user_id and not username:
                logger.warning(
                    "Author has neither id nor username — skipping follow: %s",
                    author.get("name", "unknown"),
                )
                skipped_count += 1
                continue

            if not username:
                skipped_count += 1
                continue

            # Skip ourselves
            if username == self.config.username:
                skipped_count += 1
                continue

            # Skip already followed
            if username in followed_usernames:
                skipped_count += 1
                continue

            # Quality check
            if not self._should_follow(author):
                skipped_count += 1
                continue

            try:
                # Pass only one identifier — prefer id if available
                result = self.client.toggle_follow_user(
                    user_id=user_id if user_id else None,
                    username=username if not user_id else None,
                )
                user_data = result.get("user", {})
                if user_data.get("following", False):
                    followed_count += 1
                    new_follows.add(username)
                    self._log_follow(username, article)
                    logger.info("Followed %s", username)
                else:
                    # toggleFollowUser toggled OFF — we were already following
                    # but username wasn't in our tracked set (data loss/corruption)
                    unfollow_count += 1
                    logger.warning(
                        "toggleFollowUser UNFOLLOWED %s (was already following "
                        "but not in tracked set). Adding to prevent re-toggle. "
                        "(%d/%d unfollow safety limit)",
                        username, unfollow_count, self.MAX_UNFOLLOWS_BEFORE_ABORT,
                    )
                    new_follows.add(username)
                    skipped_count += 1

                    if unfollow_count >= self.MAX_UNFOLLOWS_BEFORE_ABORT:
                        logger.error(
                            "SAFETY ABORT: %d accidental unfollows detected. "
                            "Tracked set may be corrupted. Stopping cycle.",
                            unfollow_count,
                        )
                        break
            except HashnodeError as e:
                failed_count += 1
                logger.warning("Follow failed for %s: %s", username, e)

            # Delay between follows
            if followed_count < max_follows:
                time.sleep(self.config.follow_delay)

        # Save updated followed usernames
        followed_usernames.update(new_follows)
        self.save_followed_usernames(followed_usernames)

        summary = {
            "followed": followed_count,
            "skipped": skipped_count,
            "failed": failed_count,
        }
        logger.info(
            "=== Follow cycle complete: %d followed, %d skipped, %d failed ===",
            followed_count, skipped_count, failed_count,
        )
        return summary

    def _should_follow(self, author: dict) -> bool:
        """Quality check — should we follow this author?

        Basic filter: skip empty profiles. Can be extended with
        more sophisticated checks later.
        """
        username = author.get("username", "")
        if not username:
            return False
        # All authors we engage with are worth following for reciprocity
        return True

    def _log_follow(self, username: str, article: dict) -> None:
        """Append to engagement_log.jsonl."""
        path = self.data_dir / "engagement_log.jsonl"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "follow",
            "username": username,
            "source_post_id": article.get("id", ""),
            "source_title": (article.get("title") or "")[:100],
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
