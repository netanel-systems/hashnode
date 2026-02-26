"""OwnPostResponder — engage with comments on our own Hashnode posts.

Runs 2x daily (9 AM and 3 PM UTC) AFTER the main engagement cycles.

For each comment received on our own posts:
1. Like the comment via likeComment mutation
2. Reply with a genuine 1-2 sentence response via LLM + addReply mutation
3. Deduplicate via responded_comments.json — each comment handled once

Rules:
- Max 1 reply per incoming comment. No thread continuation.
- Never engage with replies-to-replies (depth > 1 is ignored).
- Replies are specific to what the commenter said.
- No self-promotion in replies.
- No generic acknowledgements ("Thanks for reading!" is a violation).

Hashnode GraphQL note: posts are fetched via publication(host) query with
posts(first:N) subquery. Comments on a post are fetched via post(id) with
comments(first:N) subquery. All writes via likeComment and addReply mutations.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from hashnode.client import HashnodeClient, HashnodeError
from hashnode.config import HashnodeConfig
from hashnode.engagement_state import EngagementState

logger = logging.getLogger(__name__)

# Max comments to process per run (rate-limit safety)
MAX_COMMENTS_PER_RUN = 10
# Posts to inspect per run
MAX_OWN_POSTS = 10
# Comments to fetch per post
COMMENTS_PER_POST = 20
# Delay between comment engagement actions (seconds)
ENGAGE_DELAY = 3.0


class OwnPostResponder:
    """Engage with comments on our own Hashnode posts.

    Fetches comments on our publication's recent posts, likes each new comment
    via likeComment mutation, replies using an LLM-generated response via
    addReply mutation, and deduplicates via responded_comments.json so each
    comment is touched exactly once.

    Usage:
        responder = OwnPostResponder(client, config, llm_reply_fn)
        summary = responder.run()
    """

    def __init__(
        self,
        client: HashnodeClient,
        config: HashnodeConfig,
        llm_reply_fn,
    ) -> None:
        """Initialize the responder.

        Args:
            client: HashnodeClient for all GraphQL operations.
            config: HashnodeConfig with publication_id and username.
            llm_reply_fn: Callable[[str, str], str] — takes (comment_body,
                          post_title) and returns a 1-2 sentence reply string.
        """
        self.client = client
        self.config = config
        self.llm_reply_fn = llm_reply_fn
        self.data_dir: Path = config.abs_data_dir
        self.engagement_state = EngagementState(config.abs_data_dir)

    # ── Storage ────────────────────────────────────────────────────────────

    def load_responded_ids(self) -> set[str]:
        """Load comment IDs we have already responded to.

        Returns empty set if file is missing or corrupted.
        """
        path = self.data_dir / "responded_comments.json"
        if not path.exists():
            return set()
        try:
            data = json.loads(path.read_text())
            if isinstance(data, list):
                return {str(item) for item in data}
            logger.warning(
                "responded_comments.json: unexpected format %s, returning empty set.",
                type(data).__name__,
            )
            return set()
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load responded_comments.json: %s", exc)
            return set()

    def save_responded_ids(self, ids: set[str]) -> None:
        """Atomically save responded comment IDs. Bounded to 5,000 entries."""
        import os
        import tempfile

        path = self.data_dir / "responded_comments.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        bounded = sorted(ids)[-5000:]
        content = json.dumps(bounded, indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(
            dir=path.parent, suffix=".tmp", prefix=".responded_",
        )
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
        logger.info("Saved %d responded comment IDs.", len(bounded))

    # ── Fetch ───────────────────────────────────────────────────────────────

    def fetch_own_posts(self) -> list[dict]:
        """Fetch our own recent published posts via the publication host.

        Uses publication(host) query with posts(first:N) subquery.
        Returns list of post dicts with id, title, slug, url.
        Returns empty list on any API failure (logs warning).
        """
        host = self.config.publication_host if hasattr(self.config, "publication_host") else ""
        if not host and self.config.username:
            host = f"{self.config.username}.hashnode.dev"

        if not host:
            logger.warning(
                "Cannot determine publication host. "
                "Set HASHNODE_USERNAME or HASHNODE_PUBLICATION_HOST."
            )
            return []

        try:
            posts = self._fetch_publication_posts(host)
            logger.info("Fetched %d own posts from %s.", len(posts), host)
            return posts
        except HashnodeError as exc:
            logger.warning("Failed to fetch own posts: %s", exc)
            return []

    def _fetch_publication_posts(self, host: str) -> list[dict]:
        """GraphQL query: fetch posts from our publication by host."""
        query = """
        query($host: String!, $first: Int!) {
            publication(host: $host) {
                posts(first: $first) {
                    edges {
                        node {
                            id
                            title
                            slug
                            url
                            publishedAt
                        }
                    }
                }
            }
        }
        """
        data = self.client._graphql(query, {"host": host, "first": MAX_OWN_POSTS})

        # Handle case where publication doesn't exist yet (returns null)
        publication = data.get("publication")
        if publication is None:
            logger.warning(
                "Publication not found at host: %s. "
                "Create publication on Hashnode first or check HASHNODE_PUBLICATION_HOST.",
                host,
            )
            return []

        edges = publication.get("posts", {}).get("edges", [])
        return [e["node"] for e in edges if "node" in e]

    def fetch_post_comments(self, post_id: str) -> list[dict]:
        """Fetch top-level comments on a post via GraphQL.

        We only respond to top-level comments (depth = 0) to avoid thread
        continuation beyond 1 reply.

        Returns list of comment dicts with id, content.markdown, author.username.
        Returns empty list on any API failure (logs warning).
        """
        try:
            return self._fetch_comments_graphql(post_id)
        except HashnodeError as exc:
            logger.warning(
                "Failed to fetch comments for post %s: %s", post_id, exc,
            )
            return []

    def _fetch_comments_graphql(self, post_id: str) -> list[dict]:
        """GraphQL query: fetch top-level comments on a post."""
        query = """
        query($id: ID!, $first: Int!) {
            post(id: $id) {
                comments(first: $first) {
                    edges {
                        node {
                            id
                            content { markdown }
                            dateAdded
                            author {
                                id
                                username
                            }
                        }
                    }
                }
            }
        }
        """
        data = self.client._graphql(query, {"id": post_id, "first": COMMENTS_PER_POST})
        edges = (
            data.get("post", {}).get("comments", {}).get("edges", [])
        )
        return [e["node"] for e in edges if "node" in e]

    # ── Engagement ─────────────────────────────────────────────────────────

    def like_comment(self, comment_id: str) -> bool:
        """Like a comment via likeComment GraphQL mutation.

        Returns True on success, False on any error (non-fatal).
        """
        try:
            self.client.like_comment(comment_id, likes_count=1)
            logger.info("Liked comment %s.", comment_id)
            return True
        except HashnodeError as exc:
            logger.warning(
                "Like comment %s failed (non-fatal): %s", comment_id, exc,
            )
            return False
        except Exception as exc:
            logger.warning(
                "Unexpected error liking comment %s: %s", comment_id, exc,
            )
            return False

    def generate_reply(self, comment_body: str, post_title: str) -> str | None:
        """Generate a genuine 1-2 sentence reply using the LLM function.

        Applies the reply quality gate before returning. Returns None if the
        generated reply fails the quality gate or the LLM raises an exception.

        Args:
            comment_body: Markdown text of the incoming comment.
            post_title: Title of the post being commented on.

        Returns:
            Reply string on success, None on failure or quality rejection.
        """
        try:
            reply = self.llm_reply_fn(comment_body, post_title)
        except Exception as exc:
            logger.warning("LLM reply generation raised an exception: %s", exc)
            return None

        if not reply or not reply.strip():
            logger.warning("LLM returned empty reply. Skipping.")
            return None

        if not self._validate_reply(reply):
            logger.warning(
                "Generated reply failed quality gate: '%s'", reply[:60],
            )
            return None

        return reply.strip()

    def _validate_reply(self, body: str) -> bool:
        """Quality gate: reject replies that violate engagement rules.

        Returns True if reply passes, False if rejected.
        """
        if not body or not body.strip():
            return False

        if len(body) > 280:
            logger.warning(
                "Reply too long (%d chars, max 280). Rejected.", len(body),
            )
            return False

        sentences = [s for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
        if not (1 <= len(sentences) <= 2):
            logger.warning(
                "Reply must be 1-2 sentences (found %d). Rejected.", len(sentences),
            )
            return False

        if "\n\n" in body:
            logger.warning("Reply contains multiple paragraphs. Rejected.")
            return False

        generic_phrases = [
            "thanks for reading",
            "thanks for the comment",
            "glad you liked it",
            "great question",
            "thanks for your feedback",
            "appreciate your comment",
            "thank you for reading",
        ]
        body_lower = body.lower()
        for phrase in generic_phrases:
            if phrase in body_lower:
                logger.warning(
                    "Generic reply phrase detected: '%s'. Rejected.", phrase,
                )
                return False

        promo_terms = ["netanel", "our product", "check out my", "my article"]
        for term in promo_terms:
            if term in body_lower:
                logger.warning(
                    "Self-promotion in reply: '%s'. Rejected.", term,
                )
                return False

        return True

    # ── Core Loop ──────────────────────────────────────────────────────────

    def run(self) -> dict:
        """Main entry point. Processes comments on our own posts.

        Returns summary dict with counts for monitoring/logging.
        """
        logger.info("=== Hashnode OwnPostResponder cycle starting ===")
        start = time.time()

        responded_ids = self.load_responded_ids()
        posts = self.fetch_own_posts()

        if not posts:
            logger.info("No own posts found. Cycle complete.")
            return {
                "posts_checked": 0,
                "comments_found": 0,
                "liked": 0,
                "replied": 0,
                "skipped": 0,
                "elapsed_seconds": round(time.time() - start, 1),
            }

        total_comments = 0
        liked_count = 0
        replied_count = 0
        skipped_count = 0
        new_responded: set[str] = set()
        processed_this_run = 0

        for post in posts:
            if processed_this_run >= MAX_COMMENTS_PER_RUN:
                logger.info(
                    "Reached MAX_COMMENTS_PER_RUN (%d). Stopping.", MAX_COMMENTS_PER_RUN,
                )
                break

            post_id = post.get("id", "")
            post_title = post.get("title", "")

            if not post_id:
                continue

            comments = self.fetch_post_comments(post_id)
            total_comments += len(comments)

            for comment in comments:
                if processed_this_run >= MAX_COMMENTS_PER_RUN:
                    break

                comment_id = comment.get("id", "")
                if not comment_id:
                    continue

                # Dedup: skip already responded
                if comment_id in responded_ids:
                    skipped_count += 1
                    continue

                # Skip our own comments
                commenter_username = (
                    comment.get("author", {}).get("username", "") or ""
                )
                if (
                    self.config.username
                    and commenter_username.lower() == self.config.username.lower()
                ):
                    new_responded.add(comment_id)
                    skipped_count += 1
                    continue

                comment_body = (
                    comment.get("content", {}).get("markdown", "") or ""
                )
                logger.info(
                    "Processing comment %s on post '%s' by @%s",
                    comment_id, post_title[:50], commenter_username,
                )

                # Track target reply in engagement state (H4)
                # If this commenter is someone we previously engaged with,
                # record their reply as a reciprocity signal.
                if commenter_username:
                    target_state = self.engagement_state.get_target_state(commenter_username)
                    if target_state is not None:
                        self.engagement_state.record_target_reply(commenter_username)
                        logger.info(
                            "Engagement state: recorded reply from target @%s",
                            commenter_username,
                        )

                # Step 1: Like the comment
                liked = self.like_comment(comment_id)
                if liked:
                    liked_count += 1
                    self._log_action(
                        "like_comment", comment_id, post_id,
                        post_title, commenter_username,
                    )

                time.sleep(ENGAGE_DELAY * 0.5)

                # Step 2: Generate reply
                reply_text = self.generate_reply(comment_body, post_title)
                if reply_text is None:
                    logger.warning(
                        "Could not generate reply for comment %s. Marking responded.",
                        comment_id,
                    )
                    new_responded.add(comment_id)
                    processed_this_run += 1
                    continue

                # Step 3: Post the reply via addReply mutation
                try:
                    result = self.client.add_reply(comment_id, reply_text)
                    if result:
                        replied_count += 1
                        new_responded.add(comment_id)
                        processed_this_run += 1
                        self._log_action(
                            "reply_comment", comment_id, post_id,
                            post_title, commenter_username,
                            reply_text=reply_text,
                        )
                        logger.info(
                            "Replied to comment %s: '%s'",
                            comment_id, reply_text[:60],
                        )
                    else:
                        logger.warning(
                            "add_reply returned empty result for comment %s.",
                            comment_id,
                        )
                except HashnodeError as exc:
                    logger.warning(
                        "Failed to reply to comment %s: %s", comment_id, exc,
                    )
                except Exception as exc:
                    logger.warning(
                        "Unexpected error replying to comment %s: %s",
                        comment_id, exc,
                    )

                # Rate-limit safety
                time.sleep(ENGAGE_DELAY)

        # Save updated responded IDs
        responded_ids.update(new_responded)
        self.save_responded_ids(responded_ids)

        elapsed = time.time() - start
        summary = {
            "posts_checked": len(posts),
            "comments_found": total_comments,
            "liked": liked_count,
            "replied": replied_count,
            "skipped": skipped_count,
            "elapsed_seconds": round(elapsed, 1),
        }
        logger.info(
            "=== Hashnode OwnPostResponder complete: %d liked, %d replied, %.1fs ===",
            liked_count, replied_count, elapsed,
        )
        return summary

    # ── Logging ────────────────────────────────────────────────────────────

    def _log_action(
        self,
        action: str,
        comment_id: str,
        post_id: str,
        post_title: str,
        commenter: str,
        reply_text: str = "",
    ) -> None:
        """Append engagement action to engagement_log.jsonl."""
        path = self.data_dir / "engagement_log.jsonl"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "comment_id": comment_id,
            "post_id": post_id,
            "post_title": post_title[:100],
            "commenter": commenter,
        }
        if reply_text:
            entry["reply_text"] = reply_text[:200]
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
