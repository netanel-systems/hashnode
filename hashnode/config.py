"""Hashnode Growth Engine configuration — all settings from .env, never hardcoded.

Uses pydantic-settings with HASHNODE_ prefix. Every value is configurable
via environment variables. Validates at startup — fail fast, fail loud.
"""

import logging
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class HashnodeConfig(BaseSettings):
    """Central configuration for Hashnode Growth Engine.

    All settings loaded from .env with HASHNODE_ prefix.
    Every default is safe for local development.
    """

    # --- Auth (required for production) ---
    pat: str = Field(default="", description="Personal Access Token from hashnode.com/settings/developer")
    publication_id: str = Field(default="", description="Publication ID for publishing articles")
    username: str = Field(default="", description="Our Hashnode username (to skip own articles)")
    publication_host: str = Field(
        default="",
        description=(
            "Custom domain or subdomain for our publication "
            "(e.g. blog.example.com or username.hashnode.dev). "
            "Used by OwnPostResponder to fetch our own posts. "
            "Falls back to {username}.hashnode.dev if not set."
        ),
    )

    # --- API ---
    graphql_endpoint: str = "https://gql.hashnode.com/"
    request_timeout: int = Field(default=30, ge=5, le=120)

    # --- Engagement ---
    max_reactions_per_run: int = Field(default=25, ge=1, le=50)
    max_comments_per_cycle: int = Field(default=5, ge=1, le=15)
    max_follows_per_cycle: int = Field(default=5, ge=1, le=20)
    reaction_delay: float = Field(
        default=2.0, ge=0.5, le=10.0,
        description="Seconds between reactions (rate limit safety)",
    )
    comment_delay: float = Field(
        default=3.0, ge=1.0, le=15.0,
        description="Seconds between comments (rate limit safety)",
    )
    follow_delay: float = Field(
        default=2.0, ge=0.5, le=10.0,
        description="Seconds between follows",
    )
    min_reactions_to_comment: int = Field(
        default=3, ge=0, le=100,
        description="Minimum reactions on article before we comment (quality filter)",
    )

    # --- Publishing ---
    max_articles_per_day: int = Field(default=3, ge=1, le=5)
    cover_style: str = Field(default="neon", description="Cover image style: neon, matrix, gradient")

    # --- History Bounds (prevent unbounded file growth) ---
    max_reacted_history: int = Field(default=2000, ge=100, le=10000)
    max_commented_history: int = Field(default=1000, ge=100, le=5000)
    max_followed_history: int = Field(default=1000, ge=100, le=5000)
    max_published_history: int = Field(default=500, ge=50, le=5000)
    max_engagement_log: int = Field(default=10000, ge=1000, le=100000)
    max_learnings: int = Field(default=200, ge=10, le=1000)

    # --- Paths ---
    project_root: Path = Field(
        default_factory=Path.cwd,
    )
    data_dir: Path = Field(default_factory=lambda: Path("data"))

    model_config = {
        "env_file": ".env",
        "env_prefix": "HASHNODE_",
        "extra": "ignore",
    }

    @field_validator("pat")
    @classmethod
    def validate_pat(cls, v: str) -> str:
        """Warn if PAT is empty."""
        if not v:
            logger.warning(
                "HASHNODE_PAT not set. "
                "Generate one at hashnode.com/settings/developer"
            )
        return v

    @property
    def abs_data_dir(self) -> Path:
        """Absolute path to data directory."""
        return self.project_root / self.data_dir


def load_config() -> HashnodeConfig:
    """Load config from .env file. Fails fast on invalid values."""
    config = HashnodeConfig()
    logger.info(
        "Hashnode config loaded: project_root=%s, max_reactions=%d",
        config.project_root, config.max_reactions_per_run,
    )
    return config
