# Hashnode — Do's

*What we must ALWAYS do when working on Hashnode. Check BEFORE every action.*

---

## API

1. Auth header: `Authorization: <PAT>` — no Bearer prefix
2. Rate limit: 500 req/min authenticated — throttle at 0.5s writes, 0.2s reads
3. Tags require ObjectId resolution — never pass raw slugs to feed filter
4. publishPost tags need `{id, slug, name}` — client resolves plain slug strings

## Publishing

1. Check `published_history.json` before publishing — title uniqueness (>80% similarity blocked)
2. Content hash dedup — identical content blocked
3. Daily limit enforced: count articles published today vs `max_articles_per_day`
4. Cover images: local GIF generation works, CDN upload not yet implemented

## Engagement

1. Reactions: weighted 1-5 likes for authenticity (random distribution)
2. Comments: quality gate — 280+ chars, 1-2 sentences, no generic phrases
3. Follows: dedup via `followed.json`
4. Filter own articles — never engage with ourselves
5. Filter already-engaged — check reacted/commented sets

## Cron

1. Reactions via `python -m hashnode.reactor` — direct Python, no nathan-team
2. Comments/follows via `nathan-team hashnode --run` — needs Claude for content generation
3. Publishing via `nathan-team hashnode --run` — needs Claude for article writing
4. All logs to `data/logs/` with weekly rotation

## Data

1. All state in `data/` — reacted.json, commented.json, followed.json, engagement_log.jsonl
2. Published history in `data/published_history.json` — bounded to 500 entries
3. Follower snapshots in `data/follower_snapshots.jsonl` — bounded to 365 entries

## Troubleshooting Publication Issues (V021 Pattern)

**Before claiming "publication doesn't exist":**

1. **Check .env FIRST**
   ```bash
   grep PUBLICATION ~/.../hashnode/.env
   # Look for: HASHNODE_PUBLICATION_ID, HASHNODE_PUBLICATION_HOST
   ```

2. **Query by ID, not by host**
   ```graphql
   query($id: ObjectId!) {
       publication(id: $id) {
           id
           title
           url
       }
   }
   ```
   Use `HASHNODE_PUBLICATION_ID` from .env, NOT derived `{username}.hashnode.dev`

3. **Verify host value**
   - Actual: `klementgunndu1.hashnode.dev` (note the "1")
   - Derived: `klementgunnduai.hashnode.dev` (wrong - missing "1")

**Never tell Klement to "create X" without checking .env for existing credentials/IDs first.**
