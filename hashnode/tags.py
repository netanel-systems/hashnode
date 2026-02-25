"""Tag selector for Hashnode articles — content-aware, learner-informed.

Selects 2-5 tags per article using keyword matching against Hashnode's
curated tag vocabulary, boosted by engagement data from GrowthLearner.

Design:
1. Score candidate tags by keyword matches in title + content.
2. Boost tags with high engagement from GrowthLearner.get_engagement_by_tag().
3. Penalize tags marked as "skip" in learner patterns.
4. Return top N (2-5 tags), falling back to ["programming", "ai"] if
   no keywords matched.

Hashnode tag rules:
- Tags passed as slug strings (e.g. "python", "machine-learning")
- publisher.py and client.publish_post() resolve slugs to {id, slug, name}
- 2-5 tags recommended per article
- Canonical slugs from CURATED_TAGS in scout.py + tag_cache.json

Usage:
    from hashnode.tags import TagSelector
    from hashnode.learner import GrowthLearner
    from hashnode.config import load_config

    config = load_config()
    learner = GrowthLearner(config)
    selector = TagSelector(config, learner)
    tags = selector.select_tags(title="...", content="...", max_tags=4)
"""

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hashnode.config import HashnodeConfig
    from hashnode.learner import GrowthLearner

logger = logging.getLogger(__name__)


# Hashnode canonical tag vocabulary. Slugs must match what Hashnode's
# GraphQL API recognizes (same as CURATED_TAGS in scout.py plus additional
# publishing-specific tags from tag_cache.json).
#
# Format: tag_slug -> list of trigger keywords (all lowercase).
TAG_KEYWORD_MAP: dict[str, list[str]] = {
    "python": ["python", "django", "flask", "fastapi", "pydantic", "asyncio", "pip", "pytest"],
    "ai": ["ai", "artificial intelligence", "agent", "llm", "gpt", "claude", "gemini", "chatbot",
           "langchain", "langgraph", "openai", "anthropic", "prompt", "inference", "embedding"],
    "machine-learning": ["machine learning", "ml", "model training", "neural", "deep learning",
                         "sklearn", "tensorflow", "pytorch", "hugging face", "transformer",
                         "classification", "regression", "gradient"],
    "deep-learning": ["deep learning", "neural network", "pytorch", "tensorflow", "cnn", "rnn",
                      "lstm", "transformer", "bert", "gpt", "fine-tuning"],
    "llm": ["llm", "large language model", "gpt", "claude", "gemini", "mistral",
            "fine-tuning", "rag", "retrieval augmented", "context window", "token"],
    "web-development": ["web", "html", "css", "frontend", "backend", "http", "rest api",
                        "browser", "dom", "react", "next.js", "vue", "angular", "svelte"],
    "javascript": ["javascript", "typescript", "node.js", "nodejs", "npm", "yarn", "bun",
                   "react", "next.js", "vue", "angular", "deno", "es6"],
    "typescript": ["typescript", "ts", "type-safe", "typed javascript", "zod", "interface",
                   "generic", "enum"],
    "programming": ["programming", "software", "developer", "code", "coding", "refactor",
                    "design pattern", "algorithm", "data structure", "complexity"],
    "tutorial": ["tutorial", "how to", "step by step", "getting started", "build a",
                 "walkthrough", "guide", "introduction to", "learn"],
    "beginners": ["beginner", "learn", "getting started", "introduction", "fundamentals",
                  "basics", "first time", "new to", "start with", "easy"],
    "devops": ["devops", "cicd", "ci/cd", "deploy", "deployment", "pipeline", "github actions",
               "gitlab ci", "infrastructure", "sre", "reliability", "release"],
    "docker": ["docker", "container", "dockerfile", "docker-compose", "podman", "oci"],
    "kubernetes": ["kubernetes", "k8s", "kubectl", "helm", "pod", "cluster", "deployment",
                   "service mesh", "ingress"],
    "cloud": ["cloud", "aws", "azure", "gcp", "serverless", "lambda", "s3", "ec2",
              "terraform", "pulumi", "iac"],
    "productivity": ["productivity", "workflow", "automation", "tool", "efficiency",
                     "time saving", "shortcut", "claude code", "vscode", "ide"],
    "career": ["career", "job", "interview", "hiring", "salary", "resume", "cv",
               "job search", "promotion", "developer career", "soft skills"],
    "security": ["security", "auth", "authentication", "oauth", "jwt", "vulnerability",
                 "cve", "owasp", "injection", "exploit", "harden", "encryption", "secret"],
    "testing": ["test", "testing", "tdd", "pytest", "unittest", "mock", "coverage",
                "qa", "quality", "assertion", "integration test", "e2e"],
    "automation": ["automation", "automate", "bot", "script", "cron", "scheduled",
                   "workflow automation", "rpa"],
    "data-science": ["data science", "data analysis", "pandas", "numpy", "visualization",
                     "jupyter", "analytics", "statistics", "eda"],
    "database": ["database", "sql", "nosql", "schema", "migration", "orm", "query"],
    "postgresql": ["postgresql", "postgres", "pg", "psql", "pgvector"],
    "api": ["api", "rest api", "graphql", "endpoint", "openapi", "swagger", "webhook"],
    "linux": ["linux", "bash", "shell", "terminal", "unix", "command line", "cli", "systemd"],
    "open-source": ["open source", "oss", "contribute", "pull request", "github", "open-source"],
    "startup": ["startup", "saas", "indie hacker", "side project", "building in public",
                "product", "launch", "mvp"],
    "git": ["git", "github", "gitlab", "version control", "commit", "branch", "merge",
            "pull request", "rebase"],
    "nextjs": ["next.js", "nextjs", "app router", "vercel", "server components"],
    "nodejs": ["node.js", "nodejs", "express", "npm", "bun"],
    "rust": ["rust", "cargo", "ownership", "borrow", "trait", "async rust"],
    "go": ["golang", "go lang", "goroutine", "channel", "go module"],
}

# Minimum score to include a tag.
MIN_TAG_SCORE: int = 1

# Min/max tags for Hashnode.
MIN_TAGS: int = 2
MAX_TAGS: int = 5

# Default fallback tags when no keyword matches are found.
_FALLBACK_TAGS: list[str] = ["programming", "ai"]


class TagSelector:
    """Selects relevant Hashnode tags for an article.

    Uses keyword matching against TAG_KEYWORD_MAP, boosted/penalized by
    GrowthLearner engagement data. Thread-safe (stateless per call).

    Args:
        config: HashnodeConfig instance.
        learner: GrowthLearner instance for engagement-informed selection.
                 Optional — if None, only keyword matching is used.
    """

    # Characters of content to scan. Title + intro covers the topic well.
    CONTENT_SCAN_CHARS: int = 2000

    # Boost factor for tags with high engagement.
    BOOST_MULTIPLIER: float = 2.0

    # Penalty factor for skip-listed tags.
    PENALTY_MULTIPLIER: float = 0.1

    # Engagement count threshold to qualify as "high engagement".
    HIGH_ENGAGEMENT_THRESHOLD: int = 5

    def __init__(
        self,
        config: "HashnodeConfig",
        learner: "GrowthLearner | None" = None,
    ) -> None:
        self.config = config
        self.learner = learner

    def select_tags(
        self,
        title: str,
        content: str,
        max_tags: int = MAX_TAGS,
    ) -> list[str]:
        """Select the best tags for this Hashnode article.

        Args:
            title: Article title.
            content: Article body (markdown). Only first CONTENT_SCAN_CHARS used.
            max_tags: Maximum tags to return (default 5, Hashnode recommended max).

        Returns:
            List of 2-5 tag slugs sorted by relevance score descending.
            Always returns at least MIN_TAGS tags (fallback applied if needed).
        """
        max_tags = max(MIN_TAGS, min(max_tags, MAX_TAGS))

        # Build search text: title (full) + truncated content, lowercase
        search_text = (title + " " + content[:self.CONTENT_SCAN_CHARS]).lower()

        # Step 1: Score by keyword matches
        scores: dict[str, float] = {}
        for tag, keywords in TAG_KEYWORD_MAP.items():
            score = 0.0
            for kw in keywords:
                pattern = r"\b" + re.escape(kw) + r"\b"
                matches = len(re.findall(pattern, search_text))
                score += matches
            if score >= MIN_TAG_SCORE:
                scores[tag] = score

        logger.debug(
            "Tag keyword scores for '%s': %s",
            title[:50],
            {k: v for k, v in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:8]},
        )

        # Step 2: Apply learner boosts and penalties
        scores = self._apply_learner_adjustments(scores)

        # Step 3: Select top N with minimum guarantee
        if scores:
            ranked = sorted(scores.keys(), key=lambda t: scores[t], reverse=True)
            selected = ranked[:max_tags]
            # Pad to MIN_TAGS if we got fewer results than required
            if len(selected) < MIN_TAGS:
                selected = self._pad_to_minimum(selected, max_tags)
        else:
            # Fallback: no keyword matches
            selected = self._get_fallback_tags(max_tags)

        logger.info(
            "Selected %d tags for '%s': %s",
            len(selected), title[:50], selected,
        )
        return selected

    def _apply_learner_adjustments(self, scores: dict[str, float]) -> dict[str, float]:
        """Boost high-engagement tags. Penalize skip-listed tags.

        Reads GrowthLearner engagement data and learnings. Non-fatal:
        if learner is None or raises, returns scores unmodified.
        """
        if self.learner is None:
            return scores

        adjusted = dict(scores)

        # Boost from engagement data
        try:
            engagement = self.learner.get_engagement_by_tag()
            for tag, stats in engagement.items():
                total = stats.get("total", 0)
                if total >= self.HIGH_ENGAGEMENT_THRESHOLD:
                    if tag in adjusted:
                        adjusted[tag] *= self.BOOST_MULTIPLIER
                    elif tag in TAG_KEYWORD_MAP:
                        # Tag has high engagement but no keyword match — add it
                        # with a base score so it can be considered.
                        adjusted[tag] = 1.0 * self.BOOST_MULTIPLIER
        except Exception as e:
            logger.warning("TagSelector: could not load engagement data: %s", e)

        # Penalize from skip learnings
        try:
            learnings = self.learner.load_learnings()
            for learning in learnings:
                pattern = learning.get("pattern", "").lower()
                confidence = learning.get("confidence", 0)
                if "skip " in pattern and confidence >= 0.7:
                    # Extract tag from "skip <tag> — ..."
                    skip_match = re.match(r"skip\s+([\w-]+)", pattern)
                    if skip_match:
                        tag = skip_match.group(1)
                        if tag in adjusted:
                            adjusted[tag] *= self.PENALTY_MULTIPLIER
        except Exception as e:
            logger.warning("TagSelector: could not load learnings for penalties: %s", e)

        return adjusted

    def _pad_to_minimum(self, selected: list[str], max_tags: int) -> list[str]:
        """Add fallback tags to meet MIN_TAGS requirement.

        Appends fallback tags not already in the selected list.
        """
        result = list(selected)
        for fallback_tag in _FALLBACK_TAGS:
            if len(result) >= MIN_TAGS:
                break
            if fallback_tag not in result:
                result.append(fallback_tag)
        return result[:max_tags]

    def _get_fallback_tags(self, max_tags: int) -> list[str]:
        """Return fallback tags when keyword matching finds nothing."""
        result = list(_FALLBACK_TAGS)[:max_tags]
        logger.info("TagSelector: no keyword matches — using fallback tags: %s", result)
        return result
