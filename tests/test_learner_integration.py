"""Tests for GrowthLearner integration with reactor and commenter.

Covers:
- learner.analyze() derives and persists correct patterns
- reactor.filter_learner_candidates() respects should_skip_tag()
- reactor.run() calls learner.analyze() after cycle (non-crashing)
- commenter.get_prompt_insights() wraps learner safely
- commenter.run_post_cycle_analysis() wraps learner.analyze() safely
- All learner calls are non-fatal (exceptions caught)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from hashnode.commenter import CommentEngine
from hashnode.config import HashnodeConfig
from hashnode.learner import GrowthLearner
from hashnode.reactor import ReactionEngine

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def make_config(data_dir: Path) -> HashnodeConfig:
    """Build a minimal config pointing at a temp data directory."""
    return HashnodeConfig(
        pat="test-pat",
        username="testuser",
        data_dir=data_dir,
        project_root=data_dir,
    )


def write_engagement_log(data_dir: Path, entries: list[dict]) -> None:
    """Write synthetic engagement_log.jsonl for test fixtures."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "engagement_log.jsonl"
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def make_article(post_id: str, tags: list[str]) -> dict:
    """Build a minimal article dict with tag slugs."""
    return {
        "id": post_id,
        "title": f"Article {post_id}",
        "author": {"username": "otheruser"},
        "reactionCount": 5,
        "tags": [{"slug": t} for t in tags],
    }


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def reaction_engine(tmp_path: Path) -> ReactionEngine:
    """ReactionEngine with mocked client and scout."""
    config = make_config(tmp_path / "data")
    with patch("hashnode.reactor.HashnodeClient"), \
         patch("hashnode.reactor.ArticleScout"):
        engine = ReactionEngine(config)
    return engine


@pytest.fixture()
def comment_engine(tmp_path: Path) -> CommentEngine:
    """CommentEngine with mocked client."""
    config = make_config(tmp_path / "data")
    client = MagicMock()
    return CommentEngine(client=client, config=config)


# ---------------------------------------------------------------------------
# GrowthLearner.analyze() tests
# ---------------------------------------------------------------------------

class TestGrowthLearnerAnalyze:

    def test_analyze_neither_skip_nor_prioritize_for_single_engagement(self, tmp_path: Path) -> None:
        """A tag with exactly one engagement is neither skipped nor prioritized."""
        data_dir = tmp_path / "data"
        config = make_config(data_dir)
        write_engagement_log(data_dir, [
            {
                "action": "reaction",
                "tags": ["python"],
                "timestamp": "2026-02-24T10:00:00+00:00",
            },
        ])
        learner = GrowthLearner(config)
        # python has 1 engagement: above zero-skip threshold, below 5-prioritize threshold
        count = learner.analyze(known_tags=["python"])
        assert count == 0
        assert learner.load_learnings() == []

    def test_analyze_skips_tag_with_zero_total(self, tmp_path: Path) -> None:
        """A tag present in known_tags but absent from the log gets a skip learning."""
        data_dir = tmp_path / "data"
        config = make_config(data_dir)
        learner = GrowthLearner(config)
        # 'rust' never appears in engagement log — total defaults to 0
        count = learner.analyze(known_tags=["rust"])
        assert count == 1
        learnings = learner.load_learnings()
        assert len(learnings) == 1
        assert "skip rust" in learnings[0]["pattern"].lower()
        assert learnings[0]["confidence"] == 0.8

    def test_analyze_skips_tag_via_mock(self, tmp_path: Path) -> None:
        """A tag appearing in tag_stats with total=0 gets a skip learning."""
        data_dir = tmp_path / "data"
        config = make_config(data_dir)
        learner = GrowthLearner(config)
        with patch.object(learner, "get_engagement_by_tag", return_value={
            "rust": {"reactions": 0, "comments": 0, "follows": 0, "total": 0},
        }):
            count = learner.analyze(known_tags=["rust"])
        assert count == 1
        learnings = learner.load_learnings()
        assert len(learnings) == 1
        assert "skip rust" in learnings[0]["pattern"].lower()
        assert learnings[0]["confidence"] == 0.8

    def test_analyze_prioritizes_high_engagement_tag(self, tmp_path: Path) -> None:
        """Tags with total >= 5 engagements get a prioritize learning."""
        data_dir = tmp_path / "data"
        config = make_config(data_dir)
        learner = GrowthLearner(config)
        with patch.object(learner, "get_engagement_by_tag", return_value={
            "python": {"reactions": 3, "comments": 2, "follows": 0, "total": 5},
        }):
            count = learner.analyze(known_tags=["python"])
        assert count == 1
        learnings = learner.load_learnings()
        assert len(learnings) == 1
        assert "prioritize python" in learnings[0]["pattern"].lower()
        assert learnings[0]["confidence"] == 0.9

    def test_analyze_deduplicates_existing_patterns(self, tmp_path: Path) -> None:
        """analyze() does not store duplicate patterns on repeated calls."""
        data_dir = tmp_path / "data"
        config = make_config(data_dir)
        learner = GrowthLearner(config)
        tag_stats = {
            "python": {"reactions": 3, "comments": 2, "follows": 0, "total": 5},
        }
        with patch.object(learner, "get_engagement_by_tag", return_value=tag_stats):
            first = learner.analyze(known_tags=["python"])
        with patch.object(learner, "get_engagement_by_tag", return_value=tag_stats):
            second = learner.analyze(known_tags=["python"])
        assert first == 1
        assert second == 0  # duplicate — not stored again
        assert len(learner.load_learnings()) == 1

    def test_analyze_deduplicates_within_same_call(self, tmp_path: Path) -> None:
        """Two tags both producing skip patterns don't clobber each other's dedup."""
        data_dir = tmp_path / "data"
        config = make_config(data_dir)
        learner = GrowthLearner(config)
        # Both rust and go are unseen → both should get skip patterns
        count = learner.analyze(known_tags=["rust", "go"])
        assert count == 2
        patterns = [learning["pattern"].lower() for learning in learner.load_learnings()]
        assert any("skip rust" in p for p in patterns)
        assert any("skip go" in p for p in patterns)

    def test_analyze_returns_zero_when_no_engagement_data_and_no_known_tags(
        self, tmp_path: Path
    ) -> None:
        """analyze() returns 0 when both engagement data and known_tags are empty."""
        data_dir = tmp_path / "data"
        config = make_config(data_dir)
        learner = GrowthLearner(config)
        count = learner.analyze(known_tags=[])
        assert count == 0
        assert learner.load_learnings() == []

    def test_analyze_stores_both_skip_and_prioritize(self, tmp_path: Path) -> None:
        """analyze() can produce both skip and prioritize learnings in one call."""
        data_dir = tmp_path / "data"
        config = make_config(data_dir)
        learner = GrowthLearner(config)
        with patch.object(learner, "get_engagement_by_tag", return_value={
            "python": {"reactions": 4, "comments": 2, "follows": 0, "total": 6},
        }):
            # rust is in known_tags but absent from tag_stats → total=0 → skip
            count = learner.analyze(known_tags=["rust", "python"])
        assert count == 2
        patterns = [learning["pattern"].lower() for learning in learner.load_learnings()]
        assert any("skip rust" in p for p in patterns)
        assert any("prioritize python" in p for p in patterns)


# ---------------------------------------------------------------------------
# ReactionEngine.filter_learner_candidates() tests
# ---------------------------------------------------------------------------

class TestReactionEngineFilterLearner:

    def test_keeps_article_with_no_skip_tags(self, reaction_engine: ReactionEngine) -> None:
        """Articles with non-skip-listed tags are kept."""
        reaction_engine.learner.should_skip_tag = MagicMock(return_value=False)
        candidates = [make_article("1", ["python", "ai"])]
        result = reaction_engine.filter_learner_candidates(candidates)
        assert result == candidates

    def test_drops_article_when_all_tags_skip_listed(
        self, reaction_engine: ReactionEngine
    ) -> None:
        """Articles where ALL tags are skip-listed are dropped."""
        reaction_engine.learner.should_skip_tag = MagicMock(return_value=True)
        candidates = [make_article("1", ["rust", "go"])]
        result = reaction_engine.filter_learner_candidates(candidates)
        assert result == []

    def test_keeps_article_when_one_tag_not_skipped(
        self, reaction_engine: ReactionEngine
    ) -> None:
        """Articles with at least one non-skip tag are kept."""
        reaction_engine.learner.should_skip_tag = MagicMock(
            side_effect=lambda tag: tag == "rust"
        )
        candidates = [make_article("1", ["rust", "python"])]
        result = reaction_engine.filter_learner_candidates(candidates)
        assert len(result) == 1

    def test_keeps_article_with_no_tags(self, reaction_engine: ReactionEngine) -> None:
        """Articles with no tags are always kept (no basis to filter)."""
        reaction_engine.learner.should_skip_tag = MagicMock(return_value=True)
        article = {"id": "99", "title": "No tags", "tags": [], "author": {}}
        result = reaction_engine.filter_learner_candidates([article])
        assert result == [article]

    def test_filter_is_non_fatal_on_per_article_learner_exception(
        self, reaction_engine: ReactionEngine
    ) -> None:
        """filter_learner_candidates returns all candidates if per-article learner raises."""
        reaction_engine.learner.should_skip_tag = MagicMock(side_effect=RuntimeError("boom"))
        candidates = [make_article("1", ["python"])]
        result = reaction_engine.filter_learner_candidates(candidates)
        assert result == candidates

    def test_filter_non_fatal_on_outer_exception(
        self, reaction_engine: ReactionEngine
    ) -> None:
        """filter_learner_candidates catches unexpected outer errors gracefully."""
        reaction_engine.learner = None  # type: ignore[assignment]
        candidates = [make_article("1", ["python"])]
        result = reaction_engine.filter_learner_candidates(candidates)
        assert result == candidates


# ---------------------------------------------------------------------------
# ReactionEngine.run() calls learner.analyze() — non-fatal
# ---------------------------------------------------------------------------

class TestReactionEngineLearnerAnalyzeCall:

    def test_run_calls_learner_analyze_after_cycle(self, tmp_path: Path) -> None:
        """run() calls learner.analyze() exactly once after the cycle."""
        config = make_config(tmp_path / "data")
        with patch("hashnode.reactor.HashnodeClient"), \
             patch("hashnode.reactor.ArticleScout"):
            engine = ReactionEngine(config)
            engine.scout.find_relevant_articles = MagicMock(return_value=[])
            engine.scout.find_recent_articles = MagicMock(return_value=[])
            engine.scout.filter_own_articles = MagicMock(return_value=[])
            engine.scout.filter_already_engaged = MagicMock(return_value=[])
            engine.learner.analyze = MagicMock(return_value=3)
            summary = engine.run()
        engine.learner.analyze.assert_called_once()
        assert summary.get("new_learnings") == 3

    def test_run_survives_learner_analyze_exception(self, tmp_path: Path) -> None:
        """run() completes successfully even if learner.analyze() raises."""
        config = make_config(tmp_path / "data")
        with patch("hashnode.reactor.HashnodeClient"), \
             patch("hashnode.reactor.ArticleScout"):
            engine = ReactionEngine(config)
            engine.scout.find_relevant_articles = MagicMock(return_value=[])
            engine.scout.find_recent_articles = MagicMock(return_value=[])
            engine.scout.filter_own_articles = MagicMock(return_value=[])
            engine.scout.filter_already_engaged = MagicMock(return_value=[])
            engine.learner.analyze = MagicMock(side_effect=RuntimeError("boom"))
            summary = engine.run()
        assert summary.get("new_learnings") == 0
        assert "error" not in summary


# ---------------------------------------------------------------------------
# CommentEngine.get_prompt_insights() tests
# ---------------------------------------------------------------------------

class TestCommentEngineGetPromptInsights:

    def test_returns_insights_from_learner(self, comment_engine: CommentEngine) -> None:
        """get_prompt_insights() delegates to learner.get_insights_for_prompt()."""
        expected = ["- python high engagement (confidence: 0.9)"]
        comment_engine.learner.get_insights_for_prompt = MagicMock(return_value=expected)
        result = comment_engine.get_prompt_insights()
        assert result == expected
        comment_engine.learner.get_insights_for_prompt.assert_called_once_with(max_insights=5)

    def test_passes_max_insights_parameter(self, comment_engine: CommentEngine) -> None:
        """get_prompt_insights() passes max_insights to the learner."""
        comment_engine.learner.get_insights_for_prompt = MagicMock(return_value=[])
        comment_engine.get_prompt_insights(max_insights=3)
        comment_engine.learner.get_insights_for_prompt.assert_called_once_with(max_insights=3)

    def test_returns_empty_list_on_learner_exception(
        self, comment_engine: CommentEngine
    ) -> None:
        """get_prompt_insights() returns [] if learner raises — non-fatal."""
        comment_engine.learner.get_insights_for_prompt = MagicMock(
            side_effect=RuntimeError("disk error")
        )
        result = comment_engine.get_prompt_insights()
        assert result == []

    def test_returns_empty_list_when_no_learnings(
        self, comment_engine: CommentEngine
    ) -> None:
        """get_prompt_insights() returns [] when learnings.json does not exist."""
        result = comment_engine.get_prompt_insights()
        assert result == []


# ---------------------------------------------------------------------------
# CommentEngine.run_post_cycle_analysis() tests
# ---------------------------------------------------------------------------

class TestCommentEngineRunPostCycleAnalysis:

    def test_calls_learner_analyze_and_returns_count(
        self, comment_engine: CommentEngine
    ) -> None:
        """run_post_cycle_analysis() delegates to learner.analyze() and returns count."""
        comment_engine.learner.analyze = MagicMock(return_value=4)
        count = comment_engine.run_post_cycle_analysis()
        assert count == 4
        comment_engine.learner.analyze.assert_called_once()

    def test_returns_zero_on_learner_exception(self, comment_engine: CommentEngine) -> None:
        """run_post_cycle_analysis() returns 0 if learner raises — non-fatal."""
        comment_engine.learner.analyze = MagicMock(side_effect=OSError("disk full"))
        count = comment_engine.run_post_cycle_analysis()
        assert count == 0

    def test_runs_analysis_gracefully_with_no_engagement_data(
        self, comment_engine: CommentEngine
    ) -> None:
        """run_post_cycle_analysis() completes without error when engagement log is absent.

        All CURATED_TAGS are unseen (total=0) so they each get a skip learning.
        The count equals the number of CURATED_TAGS known to the scout.
        """
        from hashnode.scout import CURATED_TAGS

        count = comment_engine.run_post_cycle_analysis()
        # All unseen tags get skip learnings on first run
        assert count == len(CURATED_TAGS)
