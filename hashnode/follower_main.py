"""Entry point for autonomous follow cycle on Hashnode.

Cron-friendly: loads config, scouts articles, runs follow cycle, exits 0/1.
No LLM calls -- following is mechanical (reciprocity check + API call).

Usage:
    python -m hashnode.follower_main
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from hashnode.client import HashnodeClient
from hashnode.config import load_config
from hashnode.follower import FollowEngine
from hashnode.scout import ArticleScout

sys.path.insert(0, str(Path.home() / "netanel" / ".nathan" / "scripts"))
from atomic_state import atomic_write_state, read_state_safe  # noqa: E402

logger = logging.getLogger(__name__)

STATE_PATH = str(
    Path.home() / "netanel" / ".nathan" / "teams" / "hashnode" / "state.json"
)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    try:
        config = load_config()
        client = HashnodeClient(config)
        scout = ArticleScout(client, config)
        engine = FollowEngine(client, config)

        # Scout articles from multiple feeds for follow candidates
        relevant = scout.find_relevant_articles(count=20)
        recent = scout.find_recent_articles(count=20)

        # Merge and deduplicate
        seen_ids: set[str] = set()
        articles: list[dict] = []
        for article in relevant + recent:
            aid = article.get("id", "")
            if aid and aid not in seen_ids:
                seen_ids.add(aid)
                articles.append(article)

        logger.info("Scouted %d unique articles for follow candidates.", len(articles))

        # Run the follow cycle (engine handles reciprocity checks internally)
        summary = engine.follow_cycle(articles)

        # Update team state.json with follow stats
        state = read_state_safe(STATE_PATH)
        total_follows = (state.get("total_follows_given") or 0) + summary["followed"]
        state["total_follows_given"] = total_follows
        state["last_follow_cycle"] = datetime.now(timezone.utc).isoformat()
        state["last_follow_summary"] = summary
        atomic_write_state(STATE_PATH, state)

        print(json.dumps(summary, indent=2))
    except Exception as e:
        logger.error("Follow cycle failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
