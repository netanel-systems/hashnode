"""CommentEngine — post comments on Hashnode articles.

Used by nathan-team comment cycles (3x daily). Nathan reads articles
and writes the comments — this module handles posting and dedup.

Comments are 2-4 sentences, specific to the article, content-aware.
Template categories guide LLM generation. Pure API — addComment mutation.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from hashnode.client import HashnodeClient, HashnodeError
from hashnode.config import HashnodeConfig
from hashnode.engagement_state import EngagementState
from hashnode.learner import GrowthLearner
from hashnode.schema import build_engagement_entry
from hashnode.storage import load_json_ids, save_json_ids, trim_jsonl

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
        self.learner = GrowthLearner(config)
        self.data_dir = config.abs_data_dir
        self.engagement_state = EngagementState(config.abs_data_dir)

    def should_comment(self, author_username: str) -> bool:
        """Check engagement state: should we comment on this author's post? (H4)

        Returns True if we have liked first and target is not deprioritized.
        Always returns True if author_username is empty (defensive).
        """
        if not author_username:
            return True
        return self.engagement_state.should_comment(author_username)

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

    def get_prompt_insights(self, max_insights: int = 5) -> list[str]:
        """Return learner insights for injection into the comment generation prompt.

        Call this BEFORE generating a comment so the LLM has context on what
        engagement patterns have worked historically.

        Returns bullet-point strings or an empty list if learner is unavailable.
        All errors are caught — never crashes the comment cycle.
        """
        try:
            return self.learner.get_insights_for_prompt(max_insights=max_insights)
        except Exception as e:
            logger.warning("get_prompt_insights failed — returning empty: %s", e)
            return []

    def run_post_cycle_analysis(self) -> int:
        """Trigger learner pattern extraction after a comment cycle completes.

        Returns number of new learnings stored (0 on any error).
        All errors are caught — never crashes the comment cycle.
        """
        try:
            count = self.learner.analyze()
            logger.info("Post-cycle learner analysis: %d new patterns.", count)
            return count
        except Exception as e:
            logger.warning("run_post_cycle_analysis failed — non-fatal: %s", e)
            return 0

    def post_comment(
        self,
        post_id: str,
        body: str,
        article_title: str = "",
        author: str = "",
        comment_template_category: str | None = None,
    ) -> dict | None:
        """Post a comment and log it.

        Args:
            post_id: Hashnode post ID to comment on.
            body: Comment text (2-4 sentences, max 600 chars).
            article_title: Title of the article (for logging).
            author: Author username (for logging).
            comment_template_category: Template category used to guide LLM
                generation (H3). Logged to engagement log for A/B testing.

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

            # Detect question for engagement log tagging
            from hashnode.comment_templates import has_question
            _has_question = has_question(body)

            # Log to comment history (for learner)
            self._log_comment(
                post_id, body, article_title, author, result,
                comment_template_category=comment_template_category,
                comment_has_question=_has_question,
            )

            # Log to engagement log
            self._log_engagement(
                post_id, body, article_title, author,
                comment_template_category=comment_template_category,
                comment_has_question=_has_question,
            )

            # Record comment in engagement state (H4)
            if author:
                self.engagement_state.record_comment(author)

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

        # Must be under 600 chars (H3: 2-4 sentences need more room)
        if len(body) > 600:
            logger.warning("Comment too long (%d chars). Max 600.", len(body))
            return False

        # Must be 2-4 sentences (H3: content-aware comments are longer)
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
        if not (2 <= len(sentences) <= 4):
            logger.warning("Comment must be 2-4 sentences (found %d).", len(sentences))
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
        comment_template_category: str | None = None,
        comment_has_question: bool | None = None,
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
            "comment_template_category": comment_template_category,
            "comment_has_question": comment_has_question,
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _log_engagement(
        self,
        post_id: str,
        body: str,
        article_title: str,
        author: str,
        cycle_id: str | None = None,
        comment_template_category: str | None = None,
        comment_has_question: bool | None = None,
    ) -> None:
        """Append to shared engagement_log.jsonl with enhanced X1 schema."""
        path = self.data_dir / "engagement_log.jsonl"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        entry = build_engagement_entry(
            action="comment",
            platform="hashnode",
            target_username=author,
            target_post_id=post_id,
            target_followers_at_engagement=None,  # Populated when scout targeting ships
            target_post_reactions_at_engagement=None,
            target_post_age_hours=None,
            comment_template_category=comment_template_category,
            comment_has_question=comment_has_question,
            cycle_id=cycle_id,
            # Existing fields preserved
            post_id=post_id,
            title=article_title[:100],
            author_username=author,
            comment_length=len(body),
        )
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        self._trim_engagement_log()

    def _trim_engagement_log(self) -> None:
        """Trim engagement log to max_engagement_log entries. Delegates to storage.trim_jsonl."""
        trim_jsonl(self.data_dir / "engagement_log.jsonl", self.config.max_engagement_log)
