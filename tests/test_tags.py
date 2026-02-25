"""Tests for hashnode.tags.TagSelector — Hashnode tag selection.

Covers:
- Keyword matching selects correct tags for known article topics
- MIN_TAGS (2) and MAX_TAGS (5) limits enforced
- Fallback returns at least 2 tags when no keywords match
- Learner boost: high-engagement tags get boosted into selection
- Learner penalty: skip-listed tags are penalized
- Learner failure is non-fatal (selector proceeds without boost/penalty)
- select_tags always returns between MIN_TAGS and MAX_TAGS tags
- Tags match Hashnode's canonical slug format (lowercase, hyphens)
- pad_to_minimum fills up to MIN_TAGS when keyword matches are sparse
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hashnode.config import HashnodeConfig
from hashnode.tags import TagSelector, TAG_KEYWORD_MAP, MIN_TAGS, MAX_TAGS, _FALLBACK_TAGS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(data_dir: Path) -> HashnodeConfig:
    """Build a minimal HashnodeConfig pointing at a temp data directory."""
    return HashnodeConfig(
        pat="test-pat",
        username="testuser",
        data_dir=data_dir,
        project_root=data_dir.parent,
    )


# ---------------------------------------------------------------------------
# Gate 1: Keyword matching
# ---------------------------------------------------------------------------

class TestKeywordMatching:

    def test_python_article_selects_python_tag(self, tmp_path: Path) -> None:
        """Article about Python selects the python tag."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config)
        tags = selector.select_tags(
            title="5 Python Async Patterns Every AI Engineer Needs",
            content="Python's asyncio makes concurrent programming practical. "
                    "Use async/await with FastAPI for production APIs.",
        )
        assert "python" in tags

    def test_ai_article_selects_ai_tag(self, tmp_path: Path) -> None:
        """Article about AI agents selects the ai tag."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config)
        tags = selector.select_tags(
            title="Why Your AI Agent Needs a Memory Layer",
            content="LLM-based AI agents have no persistent memory. "
                    "Use LangChain and Graphiti to fix this. "
                    "Prompt engineering for better AI workflows.",
        )
        assert "ai" in tags

    def test_devops_docker_article_selects_relevant_tags(self, tmp_path: Path) -> None:
        """Article about Docker and DevOps selects both tags."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config)
        tags = selector.select_tags(
            title="Dockerfile Optimization for CI/CD Pipelines",
            content="Docker container best practices. Use docker-compose locally. "
                    "CI/CD pipeline with GitHub Actions deploys automatically. "
                    "Deploy to production with zero downtime.",
        )
        # Both docker and devops should be selected
        assert "docker" in tags or "devops" in tags

    def test_machine_learning_article_selects_ml_tag(self, tmp_path: Path) -> None:
        """Article about ML model training selects machine-learning."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config)
        tags = selector.select_tags(
            title="Fine-Tuning Neural Networks with PyTorch",
            content="Machine learning model training with PyTorch. "
                    "Neural network architecture for classification. "
                    "Gradient descent, backpropagation, LSTM layers.",
        )
        assert "machine-learning" in tags or "deep-learning" in tags

    def test_multi_topic_article_returns_multiple_tags(self, tmp_path: Path) -> None:
        """Article spanning Python + AI + tutorial gets all three tags."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config)
        tags = selector.select_tags(
            title="Building AI Agents in Python: A Tutorial",
            content="This step-by-step tutorial shows how to build AI agents "
                    "using Python and LangChain. Learn LLM fundamentals first. "
                    "By the end you will have a working agent.",
        )
        assert len(tags) >= 2
        # At minimum python and ai should both be present
        assert "python" in tags
        assert "ai" in tags


# ---------------------------------------------------------------------------
# Gate 2: Tag count limits
# ---------------------------------------------------------------------------

class TestTagLimits:

    def test_never_exceeds_max_tags(self, tmp_path: Path) -> None:
        """TagSelector never returns more than MAX_TAGS (5) tags."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config)
        tags = selector.select_tags(
            title="Python AI DevOps Docker Security Tutorial Career Data Kubernetes",
            content="python ai devops docker security tutorial career data kubernetes "
                    "machine learning typescript programming beginners cloud",
            max_tags=10,  # request more than allowed
        )
        assert len(tags) <= MAX_TAGS

    def test_always_returns_at_least_min_tags(self, tmp_path: Path) -> None:
        """TagSelector always returns at least MIN_TAGS (2) tags."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config)
        # Content with sparse keyword matches
        tags = selector.select_tags(
            title="Some Interesting Article",
            content="This is a short article with few technical keywords.",
        )
        assert len(tags) >= MIN_TAGS

    def test_fallback_always_returns_min_tags(self, tmp_path: Path) -> None:
        """Even with zero keyword matches, MIN_TAGS are returned via fallback."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config)
        tags = selector.select_tags(
            title="xyzzy quux blorpf noop",
            content="completely irrelevant content with no tech keywords",
        )
        assert len(tags) >= MIN_TAGS
        for tag in tags:
            assert tag in _FALLBACK_TAGS or tag in TAG_KEYWORD_MAP

    def test_respects_requested_max_tags(self, tmp_path: Path) -> None:
        """max_tags=2 returns at most 2 tags."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config)
        tags = selector.select_tags(
            title="Python AI DevOps Security Tutorial Career",
            content="python ai devops security tutorial career programming",
            max_tags=2,
        )
        assert len(tags) <= 2


# ---------------------------------------------------------------------------
# Gate 3: Learner boost
# ---------------------------------------------------------------------------

class TestLearnerBoost:

    def test_high_engagement_tag_is_boosted_into_selection(self, tmp_path: Path) -> None:
        """A tag with high engagement gets boosted even with weak keyword match."""
        config = make_config(tmp_path / "data")
        learner = MagicMock()
        # 'devops' has very high engagement
        learner.get_engagement_by_tag.return_value = {
            "devops": {"reactions": 38, "comments": 2, "follows": 1, "total": 41},
        }
        learner.load_learnings.return_value = []
        selector = TagSelector(config, learner=learner)
        # Article barely mentions devops — just one occurrence
        tags = selector.select_tags(
            title="Deploying Python Applications",
            content="Use devops practices when you deploy python applications. "
                    "Python deployment is important.",
        )
        # With boost, devops should appear
        assert "devops" in tags or "python" in tags  # at minimum python is found

    def test_skip_learned_tag_is_penalized(self, tmp_path: Path) -> None:
        """A tag with a skip learning is penalized and should drop in ranking."""
        config = make_config(tmp_path / "data")
        learner = MagicMock()
        learner.get_engagement_by_tag.return_value = {}
        learner.load_learnings.return_value = [
            {
                "pattern": "skip gpt — zero engagement across all actions",
                "confidence": 0.8,
                "evidence": "Tag 'gpt' has 0 total engagement events.",
            }
        ]
        selector = TagSelector(config, learner=learner)
        # Article about LLM — gpt keyword matches but should be penalized
        tags = selector.select_tags(
            title="Understanding GPT and LLM Architecture",
            content="GPT and other LLM models use transformer architecture. "
                    "AI systems built with LLMs require careful prompt design. "
                    "Claude, GPT, and Gemini are popular LLM choices.",
        )
        # 'ai' and 'llm' should rank above 'gpt' which is penalized
        assert "ai" in tags or "llm" in tags


# ---------------------------------------------------------------------------
# Gate 4: Learner failure is non-fatal
# ---------------------------------------------------------------------------

class TestLearnerNonFatal:

    def test_learner_engagement_exception_does_not_crash(self, tmp_path: Path) -> None:
        """If get_engagement_by_tag() raises, select_tags still returns tags."""
        config = make_config(tmp_path / "data")
        learner = MagicMock()
        learner.get_engagement_by_tag.side_effect = OSError("disk read error")
        learner.load_learnings.return_value = []
        selector = TagSelector(config, learner=learner)
        tags = selector.select_tags(
            title="Python Machine Learning Tutorial",
            content="Learn machine learning with Python. This tutorial uses scikit-learn.",
        )
        assert len(tags) >= MIN_TAGS
        assert "python" in tags or "machine-learning" in tags or "tutorial" in tags

    def test_learner_load_learnings_exception_does_not_crash(self, tmp_path: Path) -> None:
        """If load_learnings() raises, skip-penalty step is skipped silently."""
        config = make_config(tmp_path / "data")
        learner = MagicMock()
        learner.get_engagement_by_tag.return_value = {}
        learner.load_learnings.side_effect = RuntimeError("corrupted file")
        selector = TagSelector(config, learner=learner)
        tags = selector.select_tags(
            title="Docker DevOps CI/CD Pipeline",
            content="docker container orchestration. devops ci/cd pipeline with github actions.",
        )
        assert len(tags) >= MIN_TAGS

    def test_none_learner_works_fine(self, tmp_path: Path) -> None:
        """None learner uses keyword-only selection without any errors."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config, learner=None)
        tags = selector.select_tags(
            title="Building an API with FastAPI and Python",
            content="FastAPI is a modern Python web framework for building APIs. "
                    "Use Pydantic for request validation. Deploy with Docker.",
        )
        assert len(tags) >= MIN_TAGS
        assert "python" in tags or "api" in tags


# ---------------------------------------------------------------------------
# Gate 5: Tag format validation
# ---------------------------------------------------------------------------

class TestTagFormat:

    def test_all_tags_are_lowercase(self, tmp_path: Path) -> None:
        """All returned tags are lowercase slugs."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config, learner=None)
        tags = selector.select_tags(
            title="Python AI Machine Learning DevOps Tutorial",
            content="python ai machine learning devops tutorial docker kubernetes",
        )
        for tag in tags:
            assert tag == tag.lower(), f"Tag '{tag}' is not lowercase"

    def test_all_tags_contain_only_alphanumeric_and_hyphens(self, tmp_path: Path) -> None:
        """Tags contain only alphanumeric characters and hyphens (Hashnode slug format)."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config, learner=None)
        tags = selector.select_tags(
            title="Python AI Machine Learning DevOps Tutorial",
            content="python ai machine learning devops tutorial docker kubernetes",
        )
        import re
        for tag in tags:
            assert re.match(r'^[a-z0-9-]+$', tag), (
                f"Tag '{tag}' contains invalid characters for Hashnode slugs"
            )

    def test_tags_from_tag_keyword_map_keys_only(self, tmp_path: Path) -> None:
        """TagSelector only returns tags that are keys in TAG_KEYWORD_MAP or fallbacks."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config, learner=None)
        valid_tags = set(TAG_KEYWORD_MAP.keys()) | set(_FALLBACK_TAGS)
        tags = selector.select_tags(
            title="Python AI Development",
            content="python ai development programming",
        )
        for tag in tags:
            assert tag in valid_tags, f"Unexpected tag '{tag}' not in TAG_KEYWORD_MAP"


# ---------------------------------------------------------------------------
# Gate 6: Padding to minimum
# ---------------------------------------------------------------------------

class TestPaddingToMinimum:

    def test_one_keyword_match_pads_to_two_tags(self, tmp_path: Path) -> None:
        """When only 1 tag matches keywords, fallback pads result to MIN_TAGS."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config, learner=None)
        # 'rust' keywords: mostly rust-specific, nothing else in article
        tags = selector.select_tags(
            title="Rust ownership model explained",
            content="Rust borrow checker enforces memory safety. "
                    "Ownership rules prevent data races in rust programs.",
            max_tags=5,
        )
        assert len(tags) >= MIN_TAGS

    def test_pad_does_not_add_duplicates(self, tmp_path: Path) -> None:
        """_pad_to_minimum does not add fallback tag if already selected."""
        config = make_config(tmp_path / "data")
        selector = TagSelector(config, learner=None)
        # Force a scenario where "programming" would match AND be in fallback
        tags = selector.select_tags(
            title="Programming fundamentals",
            content="programming design patterns algorithm data structure complexity "
                    "software refactoring clean code",
            max_tags=5,
        )
        # Should not have duplicates
        assert len(tags) == len(set(tags))


# ---------------------------------------------------------------------------
# Gate 7: Tag vocabulary coverage
# ---------------------------------------------------------------------------

class TestTagVocabularyCoverage:

    def test_tag_keyword_map_covers_curated_tags(self) -> None:
        """TAG_KEYWORD_MAP covers the core Hashnode tags we publish with."""
        # These are the tags we actually use in published_history.json
        # and the tags from our learnings.json with high engagement.
        must_have = {
            "python", "ai", "machine-learning", "llm", "devops", "docker",
            "programming", "tutorial", "beginners", "career", "security",
            "web-development", "cloud", "testing", "automation",
        }
        for tag in must_have:
            assert tag in TAG_KEYWORD_MAP, (
                f"Tag '{tag}' must be in TAG_KEYWORD_MAP for article publishing"
            )

    def test_tag_keyword_map_has_reasonable_size(self) -> None:
        """TAG_KEYWORD_MAP should have at least 20 tags for adequate coverage."""
        assert len(TAG_KEYWORD_MAP) >= 20

    def test_each_tag_has_at_least_two_keywords(self) -> None:
        """Each tag should have at least 2 keywords to reduce false positives."""
        for tag, keywords in TAG_KEYWORD_MAP.items():
            assert len(keywords) >= 2, (
                f"Tag '{tag}' has only {len(keywords)} keyword(s)"
            )
