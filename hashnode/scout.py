"""ArticleScout — discover engagement targets from Hashnode feeds.

Uses Hashnode's feed query with different feed types:
- RELEVANT — algorithmic, best for engagement targets
- RECENT — newest, best for early reactions (author still online)
- FEATURED — Hashnode-picked, high visibility

Hashnode API can't list all tags. We maintain a curated list of ~50
relevant tag slugs. Randomly sample 10 per cycle for diversity.
"""

import logging
import random
from datetime import datetime, timezone

from hashnode.client import HashnodeClient, HashnodeError
from hashnode.config import (
    NICHE_CLUSTERS_PRIMARY,
    NICHE_CLUSTERS_SECONDARY,
    HashnodeConfig,
)

logger = logging.getLogger(__name__)

# Curated tag slugs — relevant to our audience. Hashnode has no list-all-tags endpoint.
# Updated manually when expanding to new niches.
CURATED_TAGS: list[str] = [
    "python", "javascript", "typescript", "react", "nextjs",
    "nodejs", "web-development", "devops", "docker", "kubernetes",
    "ai", "machine-learning", "deep-learning", "llm", "gpt",
    "programming", "tutorial", "beginners", "open-source", "github",
    "css", "html", "tailwindcss", "vue", "angular",
    "rust", "go", "java", "csharp", "aws",
    "linux", "git", "database", "mongodb", "postgresql",
    "api", "graphql", "rest-api", "microservices", "cloud",
    "security", "testing", "automation", "ci-cd", "data-science",
    "career", "productivity", "startup", "saas", "indie-hacker",
]

# Number of random tags to sample per cycle
TAGS_PER_CYCLE = 10


class ArticleScout:
    """Finds articles worth engaging with on Hashnode.

    Prioritizes:
    1. Relevant articles — algorithmic feed, best engagement targets
    2. Recent articles — brand new, author likely online
    3. Featured articles — Hashnode-picked, high visibility
    """

    def __init__(self, client: HashnodeClient, config: HashnodeConfig) -> None:
        self.client = client
        self.config = config
        self._cycle_tags: list[str] | None = None

    @property
    def cycle_tags(self) -> list[str]:
        """Random tags for this cycle. Sampled once, cached per instance."""
        if self._cycle_tags is None:
            sample_size = min(TAGS_PER_CYCLE, len(CURATED_TAGS))
            self._cycle_tags = random.sample(CURATED_TAGS, sample_size)
            logger.info("Cycle tags: %s", ", ".join(self._cycle_tags))
        return self._cycle_tags

    def refresh_tags(self) -> None:
        """Force new random tag sample for next query."""
        self._cycle_tags = None

    def find_relevant_articles(self, count: int = 10) -> list[dict]:
        """Find articles from RELEVANT feed (algorithmic).

        Best for finding high-engagement articles that match our interests.
        """
        return self._fetch_feed("RELEVANT", count)

    def find_recent_articles(self, count: int = 10) -> list[dict]:
        """Find newest articles — react while author is still online.

        Fresh articles have the highest chance of the author seeing
        our reaction notification immediately.
        """
        return self._fetch_feed("RECENT", count)

    def find_featured_articles(self, count: int = 10) -> list[dict]:
        """Find Hashnode-featured articles — high visibility.

        Good for commenting — these articles have wide readership.
        """
        return self._fetch_feed("FEATURED", count)

    def find_commentable_articles(
        self,
        reacted_ids: set[str],
        commented_ids: set[str],
        count: int = 5,
    ) -> list[dict]:
        """Find best articles to comment on right now.

        Combines RELEVANT + FEATURED feeds, applies all filters.
        Returns articles sorted by engagement potential.
        """
        relevant = self.find_relevant_articles(count=count * 2)
        featured = self.find_featured_articles(count=count * 2)

        # Merge and dedupe
        seen_ids: set[str] = set()
        combined: list[dict] = []
        for article in relevant + featured:
            aid = article.get("id", "")
            if aid and aid not in seen_ids:
                seen_ids.add(aid)
                combined.append(article)

        # Apply all filters
        combined = self.filter_own_articles(combined)
        combined = self.filter_already_engaged(combined, reacted_ids, commented_ids)
        combined = self.filter_quality(
            combined, min_reactions=self.config.min_reactions_to_comment,
        )

        # Sort by reactions (best engagement potential first)
        combined.sort(
            key=lambda a: a.get("reactionCount", 0), reverse=True,
        )
        logger.info(
            "Found %d commentable articles (after filters).", min(len(combined), count),
        )
        return combined[:count]

    def get_article_content(self, post_id: str) -> dict:
        """Fetch full article content for reading before commenting."""
        return self.client.get_post(post_id)

    def filter_own_articles(self, articles: list[dict]) -> list[dict]:
        """Remove our own articles — don't engage with ourselves."""
        if not self.config.username:
            return articles
        filtered = [
            a for a in articles
            if a.get("author", {}).get("username") != self.config.username
        ]
        skipped = len(articles) - len(filtered)
        if skipped > 0:
            logger.info("Filtered %d own articles.", skipped)
        return filtered

    def filter_already_engaged(
        self,
        articles: list[dict],
        reacted_ids: set[str],
        commented_ids: set[str],
    ) -> list[dict]:
        """Remove articles we already reacted to or commented on."""
        filtered = [
            a for a in articles
            if a.get("id", "") not in reacted_ids
            and a.get("id", "") not in commented_ids
        ]
        skipped = len(articles) - len(filtered)
        if skipped > 0:
            logger.info("Filtered %d already-engaged articles.", skipped)
        return filtered

    def filter_quality(
        self, articles: list[dict], min_reactions: int = 0,
    ) -> list[dict]:
        """Filter articles by minimum quality (reaction count)."""
        return [
            a for a in articles
            if a.get("reactionCount", 0) >= min_reactions
        ]

    def filter_by_target_profile(self, articles: list[dict]) -> list[dict]:
        """Filter articles by target author profile and post metrics (H1).

        Keeps articles where:
        - Author has fewer than max_target_followers followers
        - Post has fewer than max_target_reactions reactions
        - Post is younger than max_post_age_hours

        Articles missing author data pass through (defensive — do not drop
        articles just because the API did not return follower count).
        """
        max_followers = self.config.max_target_followers
        max_reactions = self.config.max_target_reactions
        max_age_hours = self.config.max_post_age_hours
        now = datetime.now(timezone.utc)

        filtered: list[dict] = []
        skipped_count = 0

        for article in articles:
            author = article.get("author", {})
            followers = author.get("followersCount")
            reactions = article.get("reactionCount", 0)

            # Follower filter (skip if author has too many followers)
            if followers is not None and followers > max_followers:
                skipped_count += 1
                continue

            # Reaction filter (skip over-engaged posts)
            if reactions > max_reactions:
                skipped_count += 1
                continue

            # Post age filter
            published_at = article.get("publishedAt", "")
            if published_at:
                try:
                    pub_time = datetime.fromisoformat(
                        published_at.replace("Z", "+00:00"),
                    )
                    age_hours = (now - pub_time).total_seconds() / 3600
                    if age_hours > max_age_hours:
                        skipped_count += 1
                        continue
                except (ValueError, TypeError):
                    pass  # Malformed date — keep article

            filtered.append(article)

        if skipped_count:
            logger.info(
                "Target profile filter: %d articles removed (followers>%d, reactions>%d, age>%dh).",
                skipped_count, max_followers, max_reactions, max_age_hours,
            )
        return filtered

    def filter_by_niche(self, articles: list[dict]) -> list[dict]:
        """Filter articles by niche keyword matching against tags (H1).

        Keeps articles that have at least one tag matching our niche clusters.
        Articles with no tags pass through (defensive).
        """
        all_niches = set(NICHE_CLUSTERS_PRIMARY + NICHE_CLUSTERS_SECONDARY)
        filtered: list[dict] = []
        skipped_count = 0

        for article in articles:
            tags = article.get("tags", [])
            if not tags:
                filtered.append(article)
                continue

            tag_slugs = {
                (t.get("slug", "") if isinstance(t, dict) else str(t)).lower()
                for t in tags
                if t
            }
            if tag_slugs & all_niches:
                filtered.append(article)
            else:
                skipped_count += 1

        if skipped_count:
            logger.info("Niche filter: %d articles removed (no matching tags).", skipped_count)
        return filtered

    def sort_by_priority(self, articles: list[dict]) -> list[dict]:
        """Sort articles by engagement priority (H1).

        Priority order:
        1. Authors with < 100 followers (highest reciprocity chance)
        2. Authors with 100-500 followers
        3. Posts with primary niche tags ranked above secondary
        4. Within same tier, newer posts first
        """
        primary_set = set(NICHE_CLUSTERS_PRIMARY)

        def _priority_key(article: dict) -> tuple:
            author = article.get("author", {})
            followers = author.get("followersCount") or 999
            # Tier: 0 = <100, 1 = 100-500, 2 = 500+
            if followers < 100:
                tier = 0
            elif followers <= 500:
                tier = 1
            else:
                tier = 2

            # Niche score: 0 = primary match, 1 = secondary only, 2 = no match
            tags = article.get("tags", [])
            tag_slugs = {
                (t.get("slug", "") if isinstance(t, dict) else str(t)).lower()
                for t in tags
                if t
            }
            if tag_slugs & primary_set:
                niche_score = 0
            elif tag_slugs:
                niche_score = 1
            else:
                niche_score = 2

            # Recency: negative timestamp for newest-first sort
            published_at = article.get("publishedAt", "")
            try:
                pub_ts = datetime.fromisoformat(
                    published_at.replace("Z", "+00:00"),
                ).timestamp()
            except (ValueError, TypeError):
                pub_ts = 0.0

            return (tier, niche_score, -pub_ts)

        return sorted(articles, key=_priority_key)

    def _fetch_feed(self, feed_type: str, count: int) -> list[dict]:
        """Fetch articles from a specific feed type with tag sampling.

        Queries with tag subsets for diversity, dedupes across queries.
        """
        seen_ids: set[str] = set()
        results: list[dict] = []

        # Try with tags first, then without if not enough results
        tag_batches = [self.cycle_tags[i:i+3] for i in range(0, len(self.cycle_tags), 3)]

        for batch in tag_batches:
            if len(results) >= count:
                break
            try:
                articles = self.client.get_feed(
                    feed_type=feed_type,
                    first=min(count, 20),
                    tag_slugs=batch,
                )
                for article in articles:
                    aid = article.get("id", "")
                    if aid and aid not in seen_ids:
                        seen_ids.add(aid)
                        results.append(article)
            except HashnodeError as e:
                logger.warning(
                    "Failed to fetch %s feed with tags %s: %s",
                    feed_type, batch, e,
                )
                continue

        # Fallback: no tags if we didn't get enough
        if len(results) < count:
            try:
                articles = self.client.get_feed(
                    feed_type=feed_type,
                    first=count,
                )
                for article in articles:
                    aid = article.get("id", "")
                    if aid and aid not in seen_ids:
                        seen_ids.add(aid)
                        results.append(article)
            except HashnodeError as e:
                logger.warning("Failed to fetch %s feed (no tags): %s", feed_type, e)

        logger.info(
            "Found %d %s articles.", len(results[:count]), feed_type,
        )
        return results[:count]
