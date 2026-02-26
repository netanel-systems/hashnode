"""Hashnode GraphQL API client — all read and write operations.

Single endpoint: https://gql.hashnode.com/ (POST, GraphQL)
Auth: Authorization header with PAT (no Bearer prefix).
Rate limit: 500 req/min authenticated.

All mutations and queries verified via live schema introspection.
"""

import logging
import time

import requests

from hashnode.config import HashnodeConfig

logger = logging.getLogger(__name__)


class HashnodeError(Exception):
    """Hashnode API error with response details."""


class HashnodeClient:
    """GraphQL client for Hashnode API — growth operations.

    Handles retries, rate limiting, and error parsing.
    All methods return typed dicts/lists or raise HashnodeError.
    """

    MAX_RETRIES: int = 3
    BASE_RETRY_DELAY: float = 1.0

    def __init__(self, config: HashnodeConfig) -> None:
        if not config.pat:
            raise HashnodeError(
                "HASHNODE_PAT not set. "
                "Generate one at hashnode.com/settings/developer"
            )
        self.config = config
        self.endpoint = config.graphql_endpoint
        self.headers = {
            "Authorization": config.pat,
            "Content-Type": "application/json",
        }
        self._last_request_at: float = 0.0
        self._tag_id_cache: dict[str, str] = {}  # slug -> ObjectId
        logger.info("HashnodeClient initialized: endpoint=%s", self.endpoint)

    def _throttle(self, *, is_write: bool) -> None:
        """Proactive rate limiting: stay well under 500/min."""
        min_interval = 0.5 if is_write else 0.2
        now = time.monotonic()
        sleep_for = min_interval - (now - self._last_request_at)
        if sleep_for > 0:
            time.sleep(sleep_for)
        self._last_request_at = time.monotonic()

    def _graphql(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query/mutation with retry and error handling.

        Returns the 'data' portion of the response.
        Raises HashnodeError on API errors or network failures.
        """
        is_write = query.strip().startswith("mutation")
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        for attempt in range(self.MAX_RETRIES):
            try:
                self._throttle(is_write=is_write)
                response = requests.post(
                    self.endpoint,
                    headers=self.headers,
                    json=payload,
                    timeout=self.config.request_timeout,
                )

                if response.status_code == 429:
                    wait = min(2 ** (attempt + 1), 10)
                    logger.warning(
                        "Rate limited. Waiting %ds (attempt %d/%d).",
                        wait, attempt + 1, self.MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue

                if response.status_code >= 400:
                    raise HashnodeError(
                        f"HTTP {response.status_code}: {response.text[:500]}"
                    )

                result = response.json()

                # GraphQL can return 200 with errors
                if "errors" in result and result["errors"]:
                    error_msgs = "; ".join(
                        e.get("message", str(e)) for e in result["errors"]
                    )
                    raise HashnodeError(f"GraphQL errors: {error_msgs}")

                return result.get("data", {})

            except requests.RequestException as e:
                if attempt == self.MAX_RETRIES - 1:
                    raise HashnodeError(
                        f"Request failed after {self.MAX_RETRIES} attempts: {e}"
                    ) from e
                logger.warning(
                    "Request error (attempt %d/%d): %s",
                    attempt + 1, self.MAX_RETRIES, e,
                )
                time.sleep(self.BASE_RETRY_DELAY * (attempt + 1))

        raise HashnodeError(f"All {self.MAX_RETRIES} retries exhausted")

    # --- Queries ---

    def get_me(self) -> dict:
        """Get authenticated user's profile and publications."""
        query = """
        query {
            me {
                id
                username
                name
                bio { text }
                followersCount
                followingsCount
                publications(first: 5) {
                    edges {
                        node {
                            id
                            title
                            url
                        }
                    }
                }
            }
        }
        """
        data = self._graphql(query)
        return data.get("me", {})

    def resolve_tag_ids(self, slugs: list[str]) -> list[str]:
        """Resolve tag slugs to ObjectId strings.

        Uses a cache to avoid repeated lookups. Silently skips
        tags that don't exist on Hashnode.
        """
        ids: list[str] = []
        for slug in slugs:
            if slug in self._tag_id_cache:
                ids.append(self._tag_id_cache[slug])
                continue
            try:
                tag = self.get_tag(slug)
                if tag and tag.get("id"):
                    self._tag_id_cache[slug] = tag["id"]
                    ids.append(tag["id"])
                else:
                    logger.warning("Tag not found: %s", slug)
            except HashnodeError:
                logger.warning("Failed to resolve tag: %s", slug)
        return ids

    def get_feed(
        self,
        feed_type: str = "RELEVANT",
        first: int = 10,
        tag_slugs: list[str] | None = None,
    ) -> list[dict]:
        """Fetch articles from Hashnode feed.

        Args:
            feed_type: RELEVANT, RECENT, FEATURED, FOLLOWING, PERSONALIZED
            first: Number of articles to fetch (max ~20)
            tag_slugs: Optional list of tag slugs to filter by.
                       Resolved to ObjectId via tag lookup.

        Returns:
            List of article dicts with id, title, author, tags, etc.
        """
        # Resolve tag slugs to ObjectId strings
        tag_ids: list[str] = []
        if tag_slugs:
            tag_ids = self.resolve_tag_ids(tag_slugs)

        # Use variables for clean parameterization
        query = """
        query($first: Int!, $filter: FeedFilter!) {
            feed(first: $first, filter: $filter) {
                edges {
                    node {
                        id
                        title
                        brief
                        slug
                        url
                        publishedAt
                        reactionCount
                        responseCount
                        views
                        tags {
                            name
                            slug
                        }
                        author {
                            id
                            username
                            name
                            followersCount
                        }
                        coverImage {
                            url
                        }
                    }
                }
            }
        }
        """
        feed_filter: dict = {"type": feed_type}
        if tag_ids:
            feed_filter["tags"] = tag_ids

        variables = {"first": first, "filter": feed_filter}
        data = self._graphql(query, variables)
        edges = data.get("feed", {}).get("edges", [])
        return [edge["node"] for edge in edges if "node" in edge]

    def get_post(self, post_id: str) -> dict:
        """Fetch full article content by ID."""
        query = """
        query($id: ID!) {
            post(id: $id) {
                id
                title
                slug
                brief
                url
                content { markdown }
                publishedAt
                reactionCount
                responseCount
                views
                tags { name slug }
                author { id username name }
                publication { id title }
            }
        }
        """
        data = self._graphql(query, {"id": post_id})
        return data.get("post", {})

    def get_tag(self, slug: str) -> dict:
        """Look up a tag by slug."""
        query = """
        query($slug: String!) {
            tag(slug: $slug) {
                id
                name
                slug
                postsCount
            }
        }
        """
        data = self._graphql(query, {"slug": slug})
        return data.get("tag", {})

    def get_publication(self, host: str) -> dict:
        """Get publication details by host."""
        query = """
        query($host: String!) {
            publication(host: $host) {
                id
                title
                url
                posts(first: 5) {
                    edges {
                        node { id title slug publishedAt }
                    }
                }
            }
        }
        """
        data = self._graphql(query, {"host": host})
        return data.get("publication", {})

    # --- Mutations ---

    def like_post(self, post_id: str, likes_count: int = 1) -> dict:
        """Like a post (1-5 likes).

        Args:
            post_id: The post ID to like
            likes_count: Number of likes (1-5, weighted for authenticity)

        Returns:
            Result dict from mutation
        """
        query = """
        mutation($input: LikePostInput!) {
            likePost(input: $input) {
                post {
                    id
                    reactionCount
                }
            }
        }
        """
        variables = {
            "input": {
                "postId": post_id,
                "likesCount": min(max(likes_count, 1), 5),
            }
        }
        data = self._graphql(query, variables)
        logger.info("Liked post %s (%d likes)", post_id, likes_count)
        return data.get("likePost", {})

    def like_comment(self, comment_id: str, likes_count: int = 1) -> dict:
        """Like a comment."""
        query = """
        mutation($input: LikeCommentInput!) {
            likeComment(input: $input) {
                comment { id }
            }
        }
        """
        variables = {
            "input": {
                "commentId": comment_id,
                "likesCount": min(max(likes_count, 1), 5),
            }
        }
        data = self._graphql(query, variables)
        logger.info("Liked comment %s", comment_id)
        return data.get("likeComment", {})

    def add_comment(self, post_id: str, content_markdown: str) -> dict:
        """Post a comment on an article.

        Args:
            post_id: The post ID to comment on
            content_markdown: Markdown content of the comment

        Returns:
            Result dict with comment details
        """
        query = """
        mutation($input: AddCommentInput!) {
            addComment(input: $input) {
                comment {
                    id
                    content { markdown }
                    dateAdded
                    author { username }
                }
            }
        }
        """
        variables = {
            "input": {
                "postId": post_id,
                "contentMarkdown": content_markdown,
            }
        }
        data = self._graphql(query, variables)
        logger.info("Comment posted on post %s", post_id)
        return data.get("addComment", {})

    def add_reply(self, comment_id: str, content_markdown: str) -> dict:
        """Reply to a comment."""
        query = """
        mutation($input: AddReplyInput!) {
            addReply(input: $input) {
                reply {
                    id
                    content { markdown }
                    dateAdded
                }
            }
        }
        """
        variables = {
            "input": {
                "commentId": comment_id,
                "contentMarkdown": content_markdown,
            }
        }
        data = self._graphql(query, variables)
        logger.info("Reply posted on comment %s", comment_id)
        return data.get("addReply", {})

    def toggle_follow_user(self, user_id: str | None = None, username: str | None = None) -> dict:
        """Follow or unfollow a user.

        Provide either user_id or username (not both).
        Idempotent — calling on already-followed user unfollows.
        """
        if bool(user_id) == bool(username):
            raise HashnodeError("Must provide exactly one of user_id or username, not both or neither")

        query = """
        mutation($id: ID, $username: String) {
            toggleFollowUser(id: $id, username: $username) {
                user {
                    id
                    username
                    following
                }
            }
        }
        """
        variables = {}
        if user_id:
            variables["id"] = user_id
        if username:
            variables["username"] = username
        data = self._graphql(query, variables)
        result = data.get("toggleFollowUser", {})
        user = result.get("user", {})
        logger.info(
            "Toggle follow %s: following=%s",
            username or user_id, user.get("following"),
        )
        return result

    def publish_post(
        self,
        title: str,
        content_markdown: str,
        tags: list[dict],
        subtitle: str = "",
        cover_image_url: str = "",
        slug: str = "",
    ) -> dict:
        """Publish an article to our publication.

        Args:
            title: Article title
            content_markdown: Full article in markdown
            tags: List of tag objects [{slug: "python"}, ...]
            subtitle: Optional subtitle
            cover_image_url: Optional cover image URL
            slug: Optional custom slug

        Returns:
            Result dict with published post details
        """
        if not self.config.publication_id:
            raise HashnodeError("HASHNODE_PUBLICATION_ID not set")

        query = """
        mutation($input: PublishPostInput!) {
            publishPost(input: $input) {
                post {
                    id
                    title
                    slug
                    url
                    publishedAt
                }
            }
        }
        """
        # Resolve tags: ensure each has both slug and name (API requirement)
        resolved_tags: list[dict] = []
        for tag in tags:
            if isinstance(tag, dict) and "slug" in tag and "name" in tag:
                resolved_tags.append(tag)
            elif isinstance(tag, dict) and "slug" in tag:
                # Look up name from slug
                tag_info = self.get_tag(tag["slug"])
                if tag_info and tag_info.get("id"):
                    resolved_tags.append({"id": tag_info["id"], "slug": tag["slug"], "name": tag_info.get("name", tag["slug"])})
                else:
                    resolved_tags.append({"slug": tag["slug"], "name": tag["slug"].replace("-", " ").title()})
            elif isinstance(tag, str):
                # Plain slug string — resolve
                tag_info = self.get_tag(tag)
                if tag_info and tag_info.get("id"):
                    resolved_tags.append({"id": tag_info["id"], "slug": tag, "name": tag_info.get("name", tag)})
                else:
                    resolved_tags.append({"slug": tag, "name": tag.replace("-", " ").title()})
            else:
                resolved_tags.append(tag)

        input_data: dict = {
            "publicationId": self.config.publication_id,
            "title": title,
            "contentMarkdown": content_markdown,
            "tags": resolved_tags,
        }
        if subtitle:
            input_data["subtitle"] = subtitle
        if cover_image_url:
            input_data["coverImageOptions"] = {
                "coverImageURL": cover_image_url,
            }
        if slug:
            input_data["slug"] = slug

        variables = {"input": input_data}
        data = self._graphql(query, variables)
        result = data.get("publishPost", {})
        post = result.get("post", {})
        logger.info(
            "Published: '%s' at %s",
            post.get("title", title), post.get("url", ""),
        )
        return result

    # --- Utilities ---

    def verify_connection(self) -> bool:
        """Verify PAT works by fetching user profile."""
        try:
            me = self.get_me()
            logger.info(
                "Hashnode connection verified: username=%s",
                me.get("username", "?"),
            )
            return True
        except HashnodeError:
            logger.exception("Hashnode connection failed")
            return False
