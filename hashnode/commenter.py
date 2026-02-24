"""CommentEngine — post comments on Hashnode articles.

Used by nathan-team comment cycles (3x daily). Nathan reads articles
and writes the comments — this module handles posting and dedup.

Comments are 1-2 sentences, specific to the article, natural.
Pure API — addComment mutation, no browser needed.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from hashnode.client import HashnodeClient, HashnodeError
from hashnode.config import HashnodeConfig
from hashnode.storage import load_json_ids, save_json_ids, trim_jsonl_file

logger = logging.getLogger(__name__)


class CommentEngine:
    """Posts comments and manages comment history with dedup.

    All operations go through Hashnode GraphQL API directly.
    """

    def __init__(
        self,
        client: HashnodeClient,
        config: HashnodeConfig,
    ) -> None:
        self.client = client
        self.config = config
        self.data_dir = config.abs_data_dir

    def load_commented_ids(self) -> set[str]:
        """Load post IDs we already commented on."""
        return load_json_ids(self.data_dir / "commented.json")

    def save_commented_ids(self, commented_ids: set[str]) -> None:
        """Save commented IDs, bounded to max_commented_history."""
        save_json_ids(
            self.data_dir / "commented.json", commented_ids,
            max_count=self.config.max_commented_history,
        )

    def load_commented_details(self) -> list[dict]:
        """Load detailed comment history (for performance tracking)."""
        path = self.data_dir / "comment_history.jsonl"
        if not path.exists():
            return []
        entries: list[dict] = []
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            logger.warning("Skipping malformed line in comment_history.jsonl: %s", line[:50])
        except OSError as e:
            logger.warning("Failed to load comment_history.jsonl: %s", e)
        return entries

    def post_comment(
        self,
        post_id: str,
        body: str,
        article_title: str = "",
        author: str = "",
    ) -> dict | None:
        """Post a comment and log it.

        Returns result dict on success, None on failure.
        """
        # Validate comment quality before posting
        if not self._validate_comment(body):
            logger.warning(
                "Comment rejected by quality gate for post %s: '%s'",
                post_id, body[:50],
            )
            return None

        try:
            result = self.client.add_comment(post_id, body)

            logger.info(
                "Comment posted on post %s (%s): '%s'",
                post_id, author, body[:60],
            )

            # Log to comment history (for learner)
            self._log_comment(post_id, body, article_title, author, result)

            # Log to engagement log
            self._log_engagement(post_id, body, article_title, author)

            return result

        except HashnodeError as e:
            logger.exception("Failed to post comment on post %s: %s", post_id, e)
            return None
        except Exception as e:
            logger.exception(
                "Unexpected error posting comment on post %s: %s", post_id, e,
            )
            return None

    def _validate_comment(self, body: str) -> bool:
        """Quality gate: reject comments that violate our rules.

        Returns True if comment passes, False if rejected.
        """
        # Must not be empty
        if not body or not body.strip():
            logger.warning("Empty comment rejected.")
            return False

        # Must be short (1-2 sentences, roughly under 280 chars)
        if len(body) > 280:
            logger.warning("Comment too long (%d chars). Max 280.", len(body))
            return False

        # Must be 1-2 sentences
        sentences = [s for s in re.split(r"[.!?]+", body) if s.strip()]
        if not (1 <= len(sentences) <= 2):
            logger.warning("Comment must be 1-2 sentences (found %d).", len(sentences))
            return False

        # Must not contain multiple paragraphs
        if "\n\n" in body:
            logger.warning("Comment contains multiple paragraphs. Rejected.")
            return False

        # Must not contain generic phrases
        generic_phrases = [
            "great article", "thanks for sharing", "well written",
            "very insightful", "i totally agree", "nice post",
            "awesome article", "love this", "game-changer",
            "thanks for writing", "great read", "well done",
        ]
        body_lower = body.lower()
        for phrase in generic_phrases:
            if phrase in body_lower:
                logger.warning("Generic phrase detected: '%s'", phrase)
                return False

        # Must not contain self-promotion
        promo_terms = ["netanel", "our product", "check out my", "my article"]
        for term in promo_terms:
            if term in body_lower:
                logger.warning("Self-promotion detected: '%s'", term)
                return False

        return True

    def _log_comment(
        self,
        post_id: str,
        body: str,
        article_title: str,
        author: str,
        api_result: dict,
    ) -> None:
        """Log comment details for performance tracking by learner."""
        path = self.data_dir / "comment_history.jsonl"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        comment = api_result.get("comment", {})
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "post_id": post_id,
            "article_title": article_title[:100],
            "author": author,
            "comment_text": body,
            "comment_id": comment.get("id", ""),
            "char_count": len(body),
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _log_engagement(
        self,
        post_id: str,
        body: str,
        article_title: str,
        author: str,
    ) -> None:
        """Append to shared engagement_log.jsonl."""
        path = self.data_dir / "engagement_log.jsonl"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "comment",
            "post_id": post_id,
            "title": article_title[:100],
            "author": author,
            "comment_length": len(body),
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        trim_jsonl_file(self.data_dir / "engagement_log.jsonl", self.config.max_engagement_log)
