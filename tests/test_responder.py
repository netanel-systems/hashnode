"""Tests for hashnode.responder — OwnPostResponder (Hashnode).

Gates 9-11 coverage:
1. responded_comments.json dedup — already-responded comments are skipped
2. New comments are processed (like + reply)
3. Reply prompt rejects generic output
4. Graceful handling of no new comments
5. Graceful handling of API failure (no crash, logs warning)
6. Own comments are skipped and marked
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from hashnode.config import HashnodeConfig
from hashnode.responder import OwnPostResponder


def _make_config(tmp_path: Path) -> HashnodeConfig:
    """Return a minimal HashnodeConfig pointing at a temp data dir."""
    config = HashnodeConfig(
        pat="test-pat",
        username="testuser",
        publication_id="pub123",
        project_root=tmp_path,
        data_dir=Path("data"),
    )
    return config


def _make_comment(comment_id: str, username: str, body: str) -> dict:
    return {
        "id": comment_id,
        "content": {"markdown": body},
        "author": {"id": f"uid_{username}", "username": username},
        "dateAdded": "2026-02-25T10:00:00Z",
    }


def _make_post(post_id: str, title: str, url: str) -> dict:
    return {
        "id": post_id,
        "title": title,
        "slug": title.lower().replace(" ", "-"),
        "url": url,
        "publishedAt": "2026-02-20T10:00:00Z",
    }


class TestOwnPostResponderDedup(unittest.TestCase):
    """Test that already-responded comments are skipped."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.config = _make_config(self.tmp)
        self.data_dir = self.config.abs_data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _make_responder(self, llm_fn=None):
        mock_client = MagicMock()
        if llm_fn is None:
            llm_fn = lambda body, title: "The point about memory allocation there is well put."
        responder = OwnPostResponder(mock_client, self.config, llm_fn)
        # Patch fetch methods directly on the instance for isolation
        responder.fetch_own_posts = MagicMock()
        responder.fetch_post_comments = MagicMock()
        return responder

    def test_already_responded_comment_is_skipped(self):
        """Comment in responded_comments.json must be skipped without API calls."""
        responded_path = self.data_dir / "responded_comments.json"
        responded_path.write_text(json.dumps(["comment-abc"]))

        responder = self._make_responder()
        responder.fetch_own_posts.return_value = [
            _make_post("post1", "My Post", "https://hashnode.dev/post1")
        ]
        responder.fetch_post_comments.return_value = [
            _make_comment("comment-abc", "someuser", "Very useful read.")
        ]

        summary = responder.run()

        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["replied"], 0)
        self.assertEqual(summary["liked"], 0)
        # Verify like_comment was NOT called on the client
        responder.client.like_comment.assert_not_called()

    def test_new_comment_is_processed(self):
        """New comment not in responded_comments.json must be liked and replied to."""
        responder = self._make_responder()
        responder.client.like_comment.return_value = {"comment": {"id": "comment-new"}}
        responder.client.add_reply.return_value = {
            "reply": {"id": "reply-1", "content": {"markdown": "reply text"}}
        }
        responder.fetch_own_posts.return_value = [
            _make_post("post1", "My Post", "https://hashnode.dev/post1")
        ]
        responder.fetch_post_comments.return_value = [
            _make_comment("comment-new", "reader1", "The section on state management was the clearest I have read.")
        ]

        summary = responder.run()

        self.assertEqual(summary["replied"], 1)
        self.assertEqual(summary["liked"], 1)
        responded_ids = responder.load_responded_ids()
        self.assertIn("comment-new", responded_ids)

    def test_own_comment_is_skipped_and_marked(self):
        """Our own comments must be skipped and marked so they are not re-checked."""
        responder = self._make_responder()
        responder.fetch_own_posts.return_value = [
            _make_post("post1", "My Post", "https://hashnode.dev/post1")
        ]
        responder.fetch_post_comments.return_value = [
            _make_comment("comment-own", "testuser", "I wrote this.")
        ]

        summary = responder.run()

        self.assertEqual(summary["replied"], 0)
        responded_ids = responder.load_responded_ids()
        self.assertIn("comment-own", responded_ids)


class TestOwnPostResponderQualityGate(unittest.TestCase):
    """Test that the reply quality gate rejects generic output."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.config = _make_config(self.tmp)
        self.data_dir = self.config.abs_data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _make_responder(self, llm_fn):
        mock_client = MagicMock()
        responder = OwnPostResponder(mock_client, self.config, llm_fn)
        responder.fetch_own_posts = MagicMock()
        responder.fetch_post_comments = MagicMock()
        return responder

    def test_generic_reply_thanks_for_reading_is_rejected(self):
        """'Thanks for reading!' must fail the quality gate — no reply posted."""
        responder = self._make_responder(
            llm_fn=lambda body, title: "Thanks for reading!"
        )
        responder.fetch_own_posts.return_value = [
            _make_post("post1", "My Post", "https://hashnode.dev/post1")
        ]
        responder.fetch_post_comments.return_value = [
            _make_comment("comment-gen", "reader2", "Really interesting writeup.")
        ]

        summary = responder.run()

        self.assertEqual(summary["replied"], 0)
        responder.client.add_reply.assert_not_called()

    def test_specific_reply_passes_quality_gate(self):
        """Specific 1-2 sentence reply must pass and be posted."""
        responder = self._make_responder(
            llm_fn=lambda body, title: "The trade-off between consistency and availability you describe maps exactly to what I hit with CockroachDB."
        )
        responder.client.add_reply.return_value = {"reply": {"id": "r1"}}
        responder.fetch_own_posts.return_value = [
            _make_post("post1", "My Post", "https://hashnode.dev/post1")
        ]
        responder.fetch_post_comments.return_value = [
            _make_comment("comment-spec", "dev3", "The CAP theorem explanation was clear.")
        ]

        summary = responder.run()

        self.assertEqual(summary["replied"], 1)

    def test_validate_reply_rejects_all_generic_phrases(self):
        """All generic phrases must be individually caught."""
        mock_client = MagicMock()
        responder = OwnPostResponder(mock_client, self.config, lambda b, t: "")
        generic_cases = [
            "Thanks for reading!",
            "Thanks for the comment.",
            "Glad you liked it, really!",
            "Great question — here is the answer.",
            "Thank you for reading my post.",
            "Thanks for your feedback on this.",
        ]
        for text in generic_cases:
            with self.subTest(text=text):
                self.assertFalse(
                    responder._validate_reply(text),
                    f"Expected quality gate to reject: '{text}'",
                )

    def test_validate_reply_rejects_self_promotion(self):
        """Self-promotion must be caught by quality gate."""
        mock_client = MagicMock()
        responder = OwnPostResponder(mock_client, self.config, lambda b, t: "")
        self.assertFalse(
            responder._validate_reply("Check out my article on Netanel for more.")
        )

    def test_validate_reply_rejects_over_280_chars(self):
        """Replies longer than 280 chars must be rejected."""
        mock_client = MagicMock()
        responder = OwnPostResponder(mock_client, self.config, lambda b, t: "")
        long_reply = "word " * 70  # well over 280 chars
        self.assertFalse(responder._validate_reply(long_reply))

    def test_validate_reply_accepts_two_sentences(self):
        """Two-sentence specific reply must pass."""
        mock_client = MagicMock()
        responder = OwnPostResponder(mock_client, self.config, lambda b, t: "")
        two_sent = (
            "Your point on lazy evaluation is accurate for most Python iterators. "
            "The edge case with generators and send() is worth noting."
        )
        self.assertTrue(responder._validate_reply(two_sent))


class TestOwnPostResponderNoComments(unittest.TestCase):
    """Test graceful handling when there are no new comments."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.config = _make_config(self.tmp)
        self.data_dir = self.config.abs_data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _make_responder(self):
        mock_client = MagicMock()
        llm_fn = lambda body, title: "That is a specific observation about the implementation."
        responder = OwnPostResponder(mock_client, self.config, llm_fn)
        responder.fetch_own_posts = MagicMock()
        responder.fetch_post_comments = MagicMock()
        return responder

    def test_no_posts_returns_zero_summary(self):
        """When fetch_own_posts() returns empty list, run() completes cleanly."""
        responder = self._make_responder()
        responder.fetch_own_posts.return_value = []

        summary = responder.run()

        self.assertEqual(summary["posts_checked"], 0)
        self.assertEqual(summary["replied"], 0)
        self.assertIn("elapsed_seconds", summary)

    def test_no_comments_on_posts_returns_zero_summary(self):
        """Posts with no comments must yield zero engagement counts."""
        responder = self._make_responder()
        responder.fetch_own_posts.return_value = [
            _make_post("post1", "My Post", "https://hashnode.dev/post1")
        ]
        responder.fetch_post_comments.return_value = []

        summary = responder.run()

        self.assertEqual(summary["comments_found"], 0)
        self.assertEqual(summary["replied"], 0)


class TestOwnPostResponderAPIFailure(unittest.TestCase):
    """Test graceful handling of API failures — no crash, logs warning."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.config = _make_config(self.tmp)
        self.data_dir = self.config.abs_data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _make_responder(self):
        mock_client = MagicMock()
        llm_fn = lambda body, title: "The approach you described handles backpressure correctly."
        responder = OwnPostResponder(mock_client, self.config, llm_fn)
        return responder

    def test_fetch_own_posts_failure_does_not_crash(self):
        """API failure during fetch_own_posts() must not crash the cycle."""
        from hashnode.client import HashnodeError

        responder = self._make_responder()
        # Patch _fetch_publication_posts to raise HashnodeError
        responder._fetch_publication_posts = MagicMock(
            side_effect=HashnodeError("API unavailable")
        )

        # Must not raise
        summary = responder.run()
        self.assertEqual(summary["posts_checked"], 0)

    def test_fetch_post_comments_failure_does_not_crash(self):
        """API failure during fetch_post_comments() must skip the post without crashing."""
        from hashnode.client import HashnodeError

        responder = self._make_responder()
        responder.fetch_own_posts = MagicMock(return_value=[
            _make_post("post1", "My Post", "https://hashnode.dev/post1")
        ])
        responder._fetch_comments_graphql = MagicMock(
            side_effect=HashnodeError("Comments unavailable")
        )

        # Must not raise
        summary = responder.run()
        self.assertEqual(summary["replied"], 0)

    def test_add_reply_failure_does_not_crash(self):
        """HashnodeError from add_reply must be caught without crashing the cycle."""
        from hashnode.client import HashnodeError

        responder = self._make_responder()
        responder.fetch_own_posts = MagicMock(return_value=[
            _make_post("post1", "My Post", "https://hashnode.dev/post1")
        ])
        responder.fetch_post_comments = MagicMock(return_value=[
            _make_comment("comment-fail", "failuser", "What about error states?")
        ])
        responder.client.like_comment.return_value = {"comment": {"id": "c1"}}
        responder.client.add_reply.side_effect = HashnodeError("Reply mutation failed")

        # Must not raise
        summary = responder.run()
        self.assertIn("replied", summary)

    def test_llm_failure_does_not_crash(self):
        """LLM exception must be caught; cycle continues without crashing."""
        def failing_llm(body, title):
            raise RuntimeError("LLM timeout")

        mock_client = MagicMock()
        responder = OwnPostResponder(mock_client, self.config, failing_llm)
        responder.fetch_own_posts = MagicMock(return_value=[
            _make_post("post1", "My Post", "https://hashnode.dev/post1")
        ])
        responder.fetch_post_comments = MagicMock(return_value=[
            _make_comment("comment-llm", "user5", "How does this scale?")
        ])

        # Must not raise
        summary = responder.run()
        self.assertEqual(summary["replied"], 0)


class TestOwnPostResponderStorage(unittest.TestCase):
    """Test load/save responded_comments.json operations."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.config = _make_config(self.tmp)
        self.data_dir = self.config.abs_data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _make_responder(self):
        mock_client = MagicMock()
        llm_fn = lambda b, t: "That implementation detail is worth highlighting."
        return OwnPostResponder(mock_client, self.config, llm_fn)

    def test_load_from_missing_file_returns_empty_set(self):
        responder = self._make_responder()
        result = responder.load_responded_ids()
        self.assertEqual(result, set())

    def test_load_from_corrupted_file_returns_empty_set(self):
        path = self.data_dir / "responded_comments.json"
        path.write_text("{bad json")
        responder = self._make_responder()
        result = responder.load_responded_ids()
        self.assertEqual(result, set())

    def test_save_and_reload_ids(self):
        responder = self._make_responder()
        ids = {"c1", "c2", "c3"}
        responder.save_responded_ids(ids)
        loaded = responder.load_responded_ids()
        self.assertEqual(loaded, ids)

    def test_save_is_bounded_to_5000(self):
        responder = self._make_responder()
        ids = {f"c{i}" for i in range(6000)}
        responder.save_responded_ids(ids)
        loaded = responder.load_responded_ids()
        self.assertLessEqual(len(loaded), 5000)


if __name__ == "__main__":
    unittest.main()
