# Hashnode Growth Engine — Team Context

> Content publishing + engagement via GraphQL API. Pure API-native (no browser automation).
> **Content under Klement's name. Generic replies = CRITICAL violation.**

---

## Own-Post Rules (NON-NEGOTIABLE)

1. One comment per post on others' content — no thread continuation.
2. Every comment on our posts = like + reply. No silent reads.
3. Like before replying.
4. Max depth: 1 reply per incoming comment. No reply to own reply.
5. Dedup strict: `responded_comments.json` is source of truth.
6. Reply must be specific. Generic = CRITICAL violation.

Responder cron: `0 9,15 * * *` via `python -m hashnode.responder_main`

---

## API Quick Reference

- **Endpoint:** `https://gql.hashnode.com/` (POST, GraphQL)
- **Auth:** PAT via `Authorization` header (no Bearer prefix)
- **Rate limit:** 500 req/min authenticated
- **Tags in feed:** ObjectId strings (resolve via `get_tag(slug)`)
- **Tags in publishPost:** `[{slug: "python"}]` objects

## Modules

config (pydantic-settings) | client (GraphQL) | scout (discover) | reactor (likes, hourly cron) | commenter (comments, 3x daily) | follower (auto-follow) | covers (animated GIF) | publisher (articles, daily) | learner (analytics) | tracker (follower growth)

## Key Patterns

- All IDs = strings (ObjectId). Dedup: reacted.json, commented.json, followed.json, published_history.json
- Filter chain: scout -> filter_own -> filter_engaged -> filter_quality
- Quality gate: max 280 chars, 1-2 sentences, no generic phrases
- Tags as objects: `[{"slug": "python"}]` not `["python"]`

---

## Brand Voice

Read `~/.nathan/knowledge/brand-voice.md` before every article. Declarative sentences, specific numbers, counter-intuitive hooks. External = secular. CTA: `Follow @klement_gunndu for more [topic] content. We're building in public.`

---

## Team Brain

Brain: `~/.nathan/teams/hashnode/` (MEMORY, DOS, VIOLATIONS, REWARDS, UNKNOWNS, state.json, logs/)
REWARDS: `~/.nathan/teams/hashnode/REWARDS.md` — read BEFORE every task. Success = +10, failure/redo = -100.
Graphiti: read `["netanel-hashnode", "netanel-decisions", "netanel"]` | write `netanel-hashnode`
