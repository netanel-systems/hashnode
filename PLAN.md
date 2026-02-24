# Hashnode Growth Engine — Complete Plan

## Context

**What:** Full-stack Hashnode growth engine — content publishing + engagement automation.
**Why:** Second platform after dev.to. Lessons learned: dev.to API didn't support writes, needed Playwright. Hashnode's GraphQL API supports EVERYTHING via API. No browser automation needed.
**Where:** `~/netanel/teams/hashnode/` | GitLab: `gitlab.com/netanel-systems/hashnode`
**How:** Headless Claude team (`nathan-team hashnode`). Autonomous. No manual intervention.

---

## Architecture

```
                         ┌─────────────────────┐
                         │   nathan-team        │
                         │   hashnode            │
                         │   (Claude headless)   │
                         └──────────┬────────────┘
                                    │ orchestrates
                    ┌───────────────┼───────────────┐
                    │               │               │
              ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
              │ PUBLISHER  │  │ ENGAGER   │  │ TRACKER   │
              │            │  │           │  │           │
              │ • research │  │ • scout   │  │ • followers│
              │ • generate │  │ • react   │  │ • metrics  │
              │ • covers   │  │ • comment │  │ • learner  │
              │ • publish  │  │ • follow  │  │ • reports  │
              └─────┬──────┘  └─────┬─────┘  └─────┬─────┘
                    │               │               │
                    └───────────────┼───────────────┘
                                    │
                         ┌──────────▼────────────┐
                         │  Hashnode GraphQL API  │
                         │  gql.hashnode.com      │
                         │  Auth: PAT header      │
                         └───────────────────────┘
```

**Key difference from dev.to:** Everything is API-native. No Playwright. No browser sessions. Pure Python + GraphQL.

---

## API Reference (Verified via Live Introspection)

**Endpoint:** `https://gql.hashnode.com/` (POST, GraphQL)
**Auth:** `Authorization: <PAT>` header
**Rate limit:** 500 req/min authenticated

### Mutations We Use

| Mutation | Input | Purpose |
|----------|-------|---------|
| `likePost` | `{postId, likesCount?}` | React to articles (1-5 likes) |
| `addComment` | `{postId, contentMarkdown}` | Comment on articles |
| `addReply` | `{commentId, contentMarkdown}` | Reply to comments |
| `likeComment` | `{commentId, likesCount?}` | Like a comment |
| `toggleFollowUser` | `{id?, username?}` | Follow/unfollow authors |
| `publishPost` | `{publicationId, title, contentMarkdown, tags, ...}` | Publish articles |

### Queries We Use

| Query | Purpose |
|-------|---------|
| `feed(first, filter: {type, tags})` | Discover articles (RELEVANT/RECENT/FEATURED) |
| `me` | Get our profile, publication ID, followers |
| `tag(slug)` | Look up tag by slug |
| `publication(host)` | Get publication details |

### Feed Types

- `RELEVANT` — algorithmic (best for engagement targets)
- `RECENT` — newest (best for early reactions)
- `FEATURED` — Hashnode-picked (high visibility)
- `FOLLOWING` — from followed users
- `PERSONALIZED` — AI-personalized

---

## Modules (8 files)

### 1. `hashnode/config.py` — HashnodeConfig

```python
class HashnodeConfig(BaseSettings):
    # Auth
    hashnode_pat: str = ""                    # Personal Access Token
    hashnode_publication_id: str = ""         # Publication ID for publishing
    hashnode_username: str = ""               # Our username (to skip own articles)

    # API
    graphql_endpoint: str = "https://gql.hashnode.com/"
    request_timeout: int = 30

    # Engagement
    max_reactions_per_run: int = 10           # Likes per cycle
    max_comments_per_cycle: int = 5           # Comments per cycle
    max_follows_per_cycle: int = 5            # Follows per cycle
    reaction_delay: float = 2.0              # Seconds between reactions
    comment_delay: float = 3.0              # Seconds between comments
    min_reactions_to_comment: int = 3        # Min likes before we comment

    # Publishing
    max_articles_per_day: int = 3            # Updated: increased to 3/day (commit dc22778)
    gemini_api_keys: list[str] = []          # LLM for article generation
    cover_style: str = "neon"                # Cover image style

    # Dedup bounds
    max_reacted_history: int = 2000
    max_commented_history: int = 1000
    max_published_history: int = 500
    max_engagement_log: int = 10000

    # Paths
    project_root: Path
    data_dir: Path = Path("data")

    model_config = {"env_file": ".env", "env_prefix": "HASHNODE_", "extra": "ignore"}
```

### 2. `hashnode/client.py` — HashnodeClient

GraphQL client wrapping all API operations. No REST. All mutations and queries.

```python
class HashnodeClient:
    def __init__(self, config: HashnodeConfig)

    # Queries
    def get_me(self) -> dict                     # Profile + publication ID
    def get_feed(self, type, first, tags?) -> list[dict]  # Discover articles
    def get_post(self, post_id) -> dict          # Full article content
    def get_tag(self, slug) -> dict              # Tag lookup
    def get_followers(self, page, pageSize) -> list[dict]
    def get_publication(self, host) -> dict

    # Mutations
    def like_post(self, post_id, likes_count=1) -> dict
    def like_comment(self, comment_id, likes_count=1) -> dict
    def add_comment(self, post_id, content_markdown) -> dict
    def add_reply(self, comment_id, content_markdown) -> dict
    def toggle_follow_user(self, username) -> dict
    def publish_post(self, title, content, tags, cover_url?, ...) -> dict

    # Internal
    def _graphql(self, query, variables) -> dict  # Core request handler
    def _throttle(self, is_write) -> None         # Rate limiting
```

### 3. `hashnode/scout.py` — ArticleScout

Discovers engagement targets. Same pattern as herald_growth but adapted for GraphQL feed.

```python
class ArticleScout:
    def find_relevant_articles(self, count) -> list[dict]    # RELEVANT feed
    def find_recent_articles(self, count) -> list[dict]      # RECENT feed
    def find_featured_articles(self, count) -> list[dict]    # FEATURED feed
    def find_commentable_articles(self, ...) -> list[dict]   # Best comment targets
    def filter_own_articles(self, articles) -> list[dict]    # Skip our posts
    def filter_already_engaged(self, articles, ...) -> list[dict]  # Skip engaged
    def filter_quality(self, articles, min_reactions) -> list[dict]

    # Tag management — curated list (API can't list all tags)
    CURATED_TAGS: list[str]  # ~50 relevant tag slugs
```

**Tag strategy:** Hashnode API can't list all tags. We maintain a curated list of ~50 relevant tag slugs. Randomly sample 10 per cycle. Periodically update the list manually.

### 4. `hashnode/reactor.py` — ReactionEngine

Standalone cron entry point. Likes articles, no LLM needed.

```python
class ReactionEngine:
    def run(self) -> dict                        # Main cron entry point
    def load_reacted_ids(self) -> set[str]       # Hashnode uses string IDs
    def save_reacted_ids(self, ids) -> None
    def log_engagement(self, action, article, details) -> None

def main():  # CLI entry: python -m hashnode.reactor
```

**Dedup:** `reacted.json` tracks all post IDs we've liked. Never re-like.
**Like count:** Random 1-5 likes per article (weighted toward 3-4 for authentic feel).

### 5. `hashnode/commenter.py` — CommentEngine

Posts comments. LLM generates text, this module validates and posts.

```python
class CommentEngine:
    def post_comment(self, post_id, body, ...) -> dict | None
    def load_commented_ids(self) -> set[str]
    def save_commented_ids(self, ids) -> None
    def _validate_comment(self, body) -> bool    # Quality gate (same rules as dev.to)
    def _log_comment(self, ...) -> None
```

**Rules:**
- Max 1 comment per article (unless replying to a comment on OUR article)
- 1-2 sentences, under 280 chars
- No generic phrases, no self-promo
- Must reference specific article content

### 6. `hashnode/publisher.py` — ArticlePublisher

Full article creation + publishing pipeline. Reads existing articles first.

```python
class ArticlePublisher:
    def publish_cycle(self) -> dict              # Full pipeline
    def _check_already_published_today(self) -> bool
    def _research_topic(self) -> Topic           # Trend-aware topic selection
    def _check_content_uniqueness(self, topic) -> bool  # No duplicate topics
    def _generate_article(self, topic) -> Article
    def _generate_cover(self, title) -> str      # Animated GIF cover → URL
    def _read_before_publish(self, article) -> Article  # Self-review pass
    def _publish(self, article) -> dict

    def load_published_titles(self) -> set[str]  # Dedup by title
    def load_published_slugs(self) -> set[str]   # Dedup by slug
```

**Uniqueness guarantees:**
1. `published_history.json` tracks all titles + slugs + topics
2. Before generating: check topic hasn't been covered before
3. Even if same topic: different angle, different code examples, different structure
4. Content hash stored — identical content blocked
5. Self-review pass reads the generated article before publishing

### 7. `hashnode/covers.py` — CoverGenerator

Animated GIF cover images with futuristic neon aesthetic.

```python
class CoverGenerator:
    def generate(self, title, style="neon") -> Path  # Generate animated GIF
    def _render_neon_frames(self, title) -> list[Image]  # Neon glow animation
    def _render_matrix_frames(self, title) -> list[Image]  # Matrix rain
    def _render_gradient_frames(self, title) -> list[Image]  # Gradient pulse
    def _optimize_gif(self, path) -> Path        # pygifsicle compression
    def _upload_to_hashnode(self, path) -> str    # Returns CDN URL
```

**Tech stack:** Pillow + pycairo for rendering, pygifsicle for optimization.
**Size:** 1200x630px (Hashnode recommended), optimized under 2MB.
**Styles:**
- `neon` — Glowing text on dark background, animated pulse
- `matrix` — Matrix-style rain with title overlay
- `gradient` — Smooth gradient animation with clean typography

### 8. `hashnode/follower.py` — FollowEngine

Auto-follow article authors for reciprocity.

```python
class FollowEngine:
    def follow_cycle(self, articles) -> dict     # Follow authors of articles we engaged with
    def load_followed_usernames(self) -> set[str]
    def save_followed_usernames(self, usernames) -> None
    def _should_follow(self, author) -> bool     # Quality check
```

**Rules:**
- Only follow authors of articles we reacted to or commented on
- Never follow same person twice
- Max 5 follows per cycle
- Track in `followed.json`

---

## Shared Utilities

### `hashnode/storage.py` — Same pattern as herald_growth

```python
def load_json_ids(path, key="post_ids") -> set[str]
def save_json_ids(path, ids, max_count, key="post_ids") -> None
```

Note: Hashnode uses string IDs (ObjectId), not integers like dev.to.

### `hashnode/learner.py` — GrowthLearner

Same as herald_growth. Tracks engagement by tag, day, time. Stores learnings.

### `hashnode/tracker.py` — GrowthTracker

Follower tracking, reciprocity, weekly reports. Uses `me` query for follower data.

---

## Dedup System (CRITICAL — No Duplicates Ever)

### Engagement Dedup

| What | Tracked In | Check |
|------|-----------|-------|
| Articles we liked | `data/reacted.json` | post_id in set → skip |
| Articles we commented on | `data/commented.json` | post_id in set → skip |
| Users we followed | `data/followed.json` | username in set → skip |
| Comments we liked | `data/liked_comments.json` | comment_id in set → skip |

### Publishing Dedup

| What | Tracked In | Check |
|------|-----------|-------|
| Published titles | `data/published_history.json` | title similarity > 0.8 → skip |
| Published slugs | `data/published_history.json` | exact slug match → skip |
| Published topics | `data/published_history.json` | topic + angle hash → skip |
| Content hashes | `data/published_history.json` | full content hash → skip |
| Daily limit | `data/.last_publish_date` | same date → skip |

### Filter Chain (every cycle)

```
scout finds articles
  → filter_own_articles()     — skip our posts (by username)
  → filter_already_engaged()  — skip reacted + commented
  → filter_quality()          — skip low-engagement posts
  → unique candidates only
```

---

## Cron Schedule

```cron
# Hashnode Reactions — every hour (6 AM - 11 PM MST)
0 6-23 * * * cd ~/netanel/teams/hashnode && .venv/bin/python -m hashnode.reactor >> data/logs/reactor-$(date +\%Y-\%W).log 2>&1

# Hashnode Comments — 3x daily (Nathan writes via Claude team)
30 7  * * * /home/intruder/bin/nathan-team hashnode --run "Comment cycle: scout 5 articles, read, write genuine comments, post. Follow 5 authors."
0  12 * * * /home/intruder/bin/nathan-team hashnode --run "Comment cycle: scout 5 articles, write comments, post. Follow 5 authors."
30 17 * * * /home/intruder/bin/nathan-team hashnode --run "Comment cycle + weekly report."

# Hashnode Article — 1x daily (morning, Nathan generates via Claude team)
0  7  * * * /home/intruder/bin/nathan-team hashnode --run "Publish cycle: research trending topic, generate article with animated GIF cover, self-review, publish."
```

**Daily volume:**
- Reactions: ~10/run × ~18 runs = ~180 likes/day
- Comments: 5/cycle × 3 = 15 comments/day
- Follows: 5/cycle × 3 = 15 follows/day
- Articles: 3/day (max)
- Cost: $0 reactions (no LLM), ~$3-5/month comments (Gemini), ~$2/month articles (Gemini)

---

## Rate Limit Safety

| Action | Hashnode Limit | Our Setting | Safe? |
|--------|---------------|-------------|-------|
| Likes | 500/min | 10 per hour (2s between) | Very safe |
| Comments | 500/min | 5 per cycle (3s between) | Very safe |
| Follows | 500/min | 5 per cycle (2s between) | Very safe |
| Feed reads | 500/min | ~20 per hour | Very safe |
| Publish | 500/min | 1 per day | Very safe |

---

## Cover Image Pipeline

### Approach: Animated GIF with Neon Aesthetic

1. **Render frames** using Pillow + pycairo:
   - Dark background (#0a0a0f)
   - Title text with neon glow effect (animated pulse)
   - Subtle particle/grid animation
   - 20-30 frames, 100ms per frame
2. **Optimize** with pygifsicle: reduce colors, lossy compression
3. **Upload** to image host (Hashnode CDN via URL or external like Imgur)
4. **Pass URL** to `publishPost` mutation via `coverImageOptions.coverImageURL`

**Size target:** 1600x840px (Hashnode recommended), under 2MB after optimization.

**Dependencies:** `pillow`, `pycairo`, `pygifsicle` (+ system `gifsicle`)

---

## File Structure

```
~/netanel/teams/hashnode/
├── hashnode/
│   ├── __init__.py
│   ├── config.py         # HashnodeConfig (pydantic-settings)
│   ├── client.py          # HashnodeClient (GraphQL)
│   ├── scout.py           # ArticleScout
│   ├── reactor.py         # ReactionEngine (standalone cron)
│   ├── commenter.py       # CommentEngine
│   ├── publisher.py       # ArticlePublisher
│   ├── covers.py          # CoverGenerator (animated GIFs)
│   ├── follower.py        # FollowEngine
│   ├── learner.py         # GrowthLearner
│   ├── tracker.py         # GrowthTracker
│   └── storage.py         # Shared JSON storage
├── data/
│   ├── reacted.json
│   ├── commented.json
│   ├── followed.json
│   ├── liked_comments.json
│   ├── published_history.json
│   ├── engagement_log.jsonl
│   ├── comment_history.jsonl
│   ├── follower_snapshots.jsonl
│   ├── learnings.json
│   ├── weekly_report.json
│   ├── covers/             # Generated GIF covers
│   └── logs/               # Cron logs
├── .env                    # HASHNODE_PAT, HASHNODE_PUBLICATION_ID, etc.
├── .env.example
├── .gitignore
├── pyproject.toml
├── CLAUDE.md               # Team context
└── README.md
```

---

## Dependencies

```toml
[project]
name = "hashnode-growth"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "requests>=2.31",       # HTTP client for GraphQL
    "pydantic>=2.0",        # Config validation
    "pydantic-settings>=2.0",
    "Pillow>=10.0",         # Image generation
    "pycairo>=1.25",        # Neon text rendering
    "pygifsicle>=1.1",      # GIF optimization
]
```

System dependency: `sudo apt install gifsicle libcairo2-dev`

---

## Team Registration

Add to `~/.claude/teams.json`:

```json
"hashnode": {
  "description": "Hashnode — content publishing, engagement automation, community growth via GraphQL API",
  "group_id": "netanel-hashnode",
  "agents": ["publisher", "engager", "tracker"],
  "skills": [],
  "focus": "Grow Hashnode presence through quality content and genuine engagement. Publish 1 article/day. React, comment, follow via API.",
  "does": "Publish articles with animated GIF covers, react to trending posts, write genuine comments, follow authors, track growth metrics",
  "does_not": "Spam, duplicate content, self-promote in comments, exceed rate limits"
}
```

---

## Build Order

1. `config.py` — HashnodeConfig with all settings
2. `storage.py` — Shared JSON storage (reuse pattern)
3. `client.py` — HashnodeClient (GraphQL, all mutations/queries)
4. `scout.py` — ArticleScout (feed discovery, filtering, dedup)
5. `reactor.py` — ReactionEngine (standalone cron, likes)
6. `commenter.py` — CommentEngine (post comments with quality gate)
7. `follower.py` — FollowEngine (auto-follow engaged authors)
8. `covers.py` — CoverGenerator (animated GIF, neon style)
9. `publisher.py` — ArticlePublisher (full pipeline with dedup)
10. `learner.py` — GrowthLearner (analytics, patterns)
11. `tracker.py` — GrowthTracker (followers, reciprocity, reports)
12. Wire crons + test each module
13. Register team in `teams.json`
14. Commit, push, deploy

---

## Edge Cases Handled

| Edge Case | How |
|-----------|-----|
| Same article in multiple feed types | Dedup by post_id in filter_already_engaged |
| Re-liking same article | reacted.json check before every like |
| Multiple comments on same article | commented.json check (1 comment max per article) |
| Commenting on our own articles | filter_own_articles by username |
| Duplicate article topics | published_history.json title similarity check |
| Same topic, different content | Content hash + angle hash tracking |
| Daily publish limit exceeded | .last_publish_date date check |
| API rate limit hit | Exponential backoff, cycle abort, next cron retry |
| PAT expired | Clear error message, cycle abort |
| Tag not found | Silently skip, log warning |
| Empty feed results | Try next feed type, log and continue |
| GIF generation fails | Fallback to static PNG cover |
| Already followed user | followed.json check, toggleFollowUser is idempotent anyway |
| Stale Stellate CDN cache | Wait 2s after write before read (if needed) |

---

## What Klement Needs To Do

1. Create Hashnode account at hashnode.com (Google OAuth)
2. Set up blog (publication)
3. Generate PAT at hashnode.com/settings/developer
4. Give Nathan: PAT, publication ID (or blog URL), username
5. Install CodeRabbit on GitLab repo for review

---

## Comparison: This vs Dev.to Setup

| Aspect | Dev.to (herald_growth) | Hashnode (this) |
|--------|----------------------|-----------------|
| Write operations | Playwright browser | Pure API (GraphQL) |
| Login/session | Browser cookies | PAT header |
| Reactions | Click buttons via DOM | `likePost` mutation |
| Comments | Fill textarea via DOM | `addComment` mutation |
| Follows | Not automated | `toggleFollowUser` mutation |
| Publishing | Separate project (herald) | Same project (integrated) |
| Cover images | Static PNG (Gemini) | Animated GIF (pycairo) |
| Complexity | High (browser debug) | Low (API calls) |
| Reliability | Medium (selectors break) | High (schema is stable) |
| Setup time | ~6 hours | ~3 hours estimated |
