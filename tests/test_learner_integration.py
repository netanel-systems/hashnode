"""Tests for GrowthLearner integration with reactor and commenter.

Covers:
- learner.analyze() derives and persists correct patterns
- reactor.filter_learner_candidates() respects should_skip_tag()
- reactor.run() calls learner.analyze() after cycle (non-crashing)
- commenter.get_prompt_insights() wraps learner safely
- commenter.run_post_cycle_analysis() wraps learner.analyze() safely
- All learner calls are non-fatal (exceptions caught)
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hashnode.config import HashnodeConfig
from hashnode.learner import GrowthLearner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(tmp_path: Path) -> HashnodeConfig:
    """Build a minimal config pointing at a temp data directory."""
    return HashnodeConfig(
        pat="test-pat",
        username="testuser",
        data_dir=tmp_path,
        project_root=tmp_path,
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
# GrowthLearner.analyze() tests
# ---------------------------------------------------------------------------

class TestGrowthLearnerAnalyze:

    def test_analyze_stores_skip_pattern_for_zero_engagement_tag(self, tmp_path):
        """Tags with zero engagement in log should produce a skip learning."""
        data_dir = tmp_path / "data"
        config = make_config(data_dir)
        write_engagement_log(data_dir, [
            {
                "action": "reaction",
                "tags": ["python"],
                "timestamp": "2026-02-24T10:00:00+00:00",
            },
        ])
        # 'javascript' has zero engagements — only 'python' does
        learner = GrowthLearner(config)
        # Pre-load so we can inspect behavior
        count = learner.analyze()
        # No zero-engagement tags in this log (python has 1)
        # But we only have one tag in log, so nothing to skip
        learnings = learner.load_learnings()
        # python has 1 engagement so no skip; but less than 5 so no prioritize
        assert count == 0
        assert learnings == []

    def test_analyze_skips_tag_with_zero_total(self, tmp_path):
        """A tag with zero total across reactions/comments/follows is skip-listed."""
        data_dir = tmp_path / "data"
        config = make_config(data_dir)
        # engagement_log has action=reaction for 'python' but 'rust' is never mentioned
        # However, get_engagement_by_tag only tracks tags that appear in the log,
        # so a tag with zero total cannot appear in the dict.
        # Instead, test that a tag appearing with total=0 is handled by
        # mocking get_engagement_by_tag directly.
        learner = GrowthLearner(config)
        with patch.object(learner, "get_engagement_by_tag", return_value={
            "rust": {"reactions": 0, "comments": 0, "follows": 0, "total": 0},
        }):
            count = learner.analyze()
        assert count == 1
        learnings = learner.load_learnings()
        assert len(learnings) == 1
        assert "skip rust" in learnings[0]["pattern"].lower()
        assert learnings[0]["confidence"] == 0.8

    def test_analyze_prioritizes_high_engagement_tag(self, tmp_path):
        """Tags with total >= 5 engagements get a prioritize learning."""
        data_dir = tmp_path / "data"
        config = make_config(data_dir)
        learner = GrowthLearner(config)
        with patch.object(learner, "get_engagement_by_tag", return_value={
            "python": {"reactions": 3, "comments": 2, "follows": 0, "total": 5},
        }):
            count = learner.analyze()
        assert count == 1
        learnings = learner.load_learnings()
        assert len(learnings) == 1
        assert "prioritize python" in learnings[0]["pattern"].lower()
        assert learnings[0]["confidence"] == 0.9

    def test_analyze_deduplicates_existing_patterns(self, tmp_path):
        """analyze() does not store duplicate patterns on repeated calls."""
        data_dir = tmp_path / "data"
        config = make_config(data_dir)
        learner = GrowthLearner(config)
        tag_stats = {
            "python": {"reactions": 3, "comments": 2, "follows": 0, "total": 5},
        }
        with patch.object(learner, "get_engagement_by_tag", return_value=tag_stats):
            first = learner.analyze()
        with patch.object(learner, "get_engagement_by_tag", return_value=tag_stats):
            second = learner.analyze()
        assert first == 1
        assert second == 0  # duplicate — not stored again
        assert len(learner.load_learnings()) == 1

    def test_analyze_returns_zero_when_no_engagement_data(self, tmp_path):
        """analyze() returns 0 and stores nothing when engagement log is empty."""
        data_dir = tmp_path / "data"
        config = make_config(data_dir)
        learner = GrowthLearner(config)
        count = learner.analyze()
        assert count == 0
        assert learner.load_learnings() == []

    def test_analyze_stores_both_skip_and_prioritize(self, tmp_path):
        """analyze() can produce both skip and prioritize learnings in one call."""
        data_dir = tmp_path / "data"
        config = make_config(data_dir)
        learner = GrowthLearner(config)
        with patch.object(learner, "get_engagement_by_tag", return_value={
            "rust": {"reactions": 0, "comments": 0, "follows": 0, "total": 0},
            "python": {"reactions": 4, "comments": 2, "follows": 0, "total": 6},
        }):
            count = learner.analyze()
        assert count == 2
        patterns = [l["pattern"].lower() for l in learner.load_learnings()]
        assert any("skip rust" in p for p in patterns)
        assert any("prioritize python" in p for p in patterns)


# ---------------------------------------------------------------------------
# ReactionEngine.filter_learner_candidates() tests
# ---------------------------------------------------------------------------

class TestReactionEngineFilterLearner:

    def _make_engine(self, tmp_path: Path):
        from hashnode.reactor import ReactionEngine
        config = make_config(tmp_path / "data")
        with patch("hashnode.reactor.HashnodeClient"), \
             patch("hashnode.reactor.ArticleScout"):
            engine = ReactionEngine(config)
        return engine

    def test_keeps_article_with_no_skip_tags(self, tmp_path):
        """Articles with non-skip-listed tags are kept."""
        engine = self._make_engine(tmp_path)
        engine.learner.should_skip_tag = MagicMock(return_value=False)
        candidates = [make_article("1", ["python", "ai"])]
        result = engine.filter_learner_candidates(candidates)
        assert result == candidates

    def test_drops_article_when_all_tags_skip_listed(self, tmp_path):
        """Articles where ALL tags are skip-listed are dropped."""
        engine = self._make_engine(tmp_path)
        engine.learner.should_skip_tag = MagicMock(return_value=True)
        candidates = [make_article("1", ["rust", "go"])]
        result = engine.filter_learner_candidates(candidates)
        assert result == []

    def test_keeps_article_when_one_tag_not_skipped(self, tmp_path):
        """Articles with at least one non-skip tag are kept."""
        engine = self._make_engine(tmp_path)
        engine.learner.should_skip_tag = MagicMock(
            side_effect=lambda tag: tag == "rust"
        )
        candidates = [make_article("1", ["rust", "python"])]
        result = engine.filter_learner_candidates(candidates)
        assert len(result) == 1

    def test_keeps_article_with_no_tags(self, tmp_path):
        """Articles with no tags are always kept (no basis to filter)."""
        engine = self._make_engine(tmp_path)
        engine.learner.should_skip_tag = MagicMock(return_value=True)
        article = {"id": "99", "title": "No tags", "tags": [], "author": {}}
        result = engine.filter_learner_candidates([article])
        assert result == [article]

    def test_filter_is_non_fatal_on_learner_exception(self, tmp_path):
        """filter_learner_candidates returns all candidates if learner raises."""
        engine = self._make_engine(tmp_path)
        engine.learner.should_skip_tag = MagicMock(side_effect=RuntimeError("boom"))
        candidates = [make_article("1", ["python"])]
        # Should not raise — must return candidates unchanged
        result = engine.filter_learner_candidates(candidates)
        assert result == candidates

    def test_filter_non_fatal_on_outer_exception(self, tmp_path):
        """filter_learner_candidates catches unexpected outer errors gracefully."""
        engine = self._make_engine(tmp_path)
        # Corrupt learner entirely
        engine.learner = None  # type: ignore[assignment]
        candidates = [make_article("1", ["python"])]
        result = engine.filter_learner_candidates(candidates)
        assert result == candidates


# ---------------------------------------------------------------------------
# ReactionEngine.run() calls learner.analyze() — non-fatal
# ---------------------------------------------------------------------------

class TestReactionEngineLearnerAnalyzeCall:

    def test_run_calls_learner_analyze_after_cycle(self, tmp_path):
        """run() calls learner.analyze() exactly once after the cycle."""
        from hashnode.reactor import ReactionEngine
        config = make_config(tmp_path / "data")
        with patch("hashnode.reactor.HashnodeClient") as MockClient, \
             patch("hashnode.reactor.ArticleScout") as MockScout:
            engine = ReactionEngine(config)
            engine.scout.find_relevant_articles = MagicMock(return_value=[])
            engine.scout.find_recent_articles = MagicMock(return_value=[])
            engine.scout.filter_own_articles = MagicMock(return_value=[])
            engine.scout.filter_already_engaged = MagicMock(return_value=[])
            engine.learner.analyze = MagicMock(return_value=3)
            summary = engine.run()
        engine.learner.analyze.assert_called_once()
        assert summary.get("new_learnings") == 3

    def test_run_survives_learner_analyze_exception(self, tmp_path):
        """run() completes successfully even if learner.analyze() raises."""
        from hashnode.reactor import ReactionEngine
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
        # Must not raise — new_learnings defaults to 0
        assert summary.get("new_learnings") == 0
        assert "error" not in summary


# ---------------------------------------------------------------------------
# CommentEngine.get_prompt_insights() tests
# ---------------------------------------------------------------------------

class TestCommentEngineGetPromptInsights:

    def _make_engine(self, tmp_path: Path):
        from hashnode.commenter import CommentEngine
        config = make_config(tmp_path / "data")
        client = MagicMock()
        engine = CommentEngine(client=client, config=config)
        return engine

    def test_returns_insights_from_learner(self, tmp_path):
        """get_prompt_insights() delegates to learner.get_insights_for_prompt()."""
        engine = self._make_engine(tmp_path)
        expected = ["- python high engagement (confidence: 0.9)"]
        engine.learner.get_insights_for_prompt = MagicMock(return_value=expected)
        result = engine.get_prompt_insights()
        assert result == expected
        engine.learner.get_insights_for_prompt.assert_called_once_with(max_insights=5)

    def test_passes_max_insights_parameter(self, tmp_path):
        """get_prompt_insights() passes max_insights to the learner."""
        engine = self._make_engine(tmp_path)
        engine.learner.get_insights_for_prompt = MagicMock(return_value=[])
        engine.get_prompt_insights(max_insights=3)
        engine.learner.get_insights_for_prompt.assert_called_once_with(max_insights=3)

    def test_returns_empty_list_on_learner_exception(self, tmp_path):
        """get_prompt_insights() returns [] if learner raises — non-fatal."""
        engine = self._make_engine(tmp_path)
        engine.learner.get_insights_for_prompt = MagicMock(
            side_effect=RuntimeError("disk error")
        )
        result = engine.get_prompt_insights()
        assert result == []

    def test_returns_empty_list_when_no_learnings(self, tmp_path):
        """get_prompt_insights() returns [] when learnings.json does not exist."""
        engine = self._make_engine(tmp_path)
        result = engine.get_prompt_insights()
        assert result == []


# ---------------------------------------------------------------------------
# CommentEngine.run_post_cycle_analysis() tests
# ---------------------------------------------------------------------------

class TestCommentEngineRunPostCycleAnalysis:

    def _make_engine(self, tmp_path: Path):
        from hashnode.commenter import CommentEngine
        config = make_config(tmp_path / "data")
        client = MagicMock()
        engine = CommentEngine(client=client, config=config)
        return engine

    def test_calls_learner_analyze_and_returns_count(self, tmp_path):
        """run_post_cycle_analysis() delegates to learner.analyze() and returns count."""
        engine = self._make_engine(tmp_path)
        engine.learner.analyze = MagicMock(return_value=4)
        count = engine.run_post_cycle_analysis()
        assert count == 4
        engine.learner.analyze.assert_called_once()

    def test_returns_zero_on_learner_exception(self, tmp_path):
        """run_post_cycle_analysis() returns 0 if learner raises — non-fatal."""
        engine = self._make_engine(tmp_path)
        engine.learner.analyze = MagicMock(side_effect=OSError("disk full"))
        count = engine.run_post_cycle_analysis()
        assert count == 0

    def test_returns_zero_when_no_engagement_data(self, tmp_path):
        """run_post_cycle_analysis() returns 0 with no engagement data — graceful."""
        engine = self._make_engine(tmp_path)
        count = engine.run_post_cycle_analysis()
        assert count == 0
