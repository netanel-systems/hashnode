# Hashnode Growth Engine — Team Context

> Hashnode content publishing + engagement automation via GraphQL API.
> Pure API-native — no browser automation needed (unlike dev.to).

---

## Quick Reference

- **API:** `https://gql.hashnode.com/` (POST, GraphQL)
- **Auth:** PAT via `Authorization` header (no Bearer prefix)
- **Auth scope:** Mutations (likePost, addComment, publishPost, etc.) require PAT. Queries (feed, post, tag) are public but PAT is still sent for consistency.
- **Rate limit:** 500 req/min authenticated
- **Tags in feed filter:** Must be ObjectId strings (resolve via `get_tag(slug)` first), NOT `{slug: "python"}` objects
- **Tags in publishPost:** Must be `[{slug: "python"}]` objects

## Modules

| Module | Purpose | Cron? |
|--------|---------|-------|
| config | HashnodeConfig (pydantic-settings) | - |
| client | GraphQL mutations + queries | - |
| scout | Discover articles from feeds | - |
| reactor | Like articles (standalone) | Every hour |
| commenter | Post genuine comments | 3x daily (Nathan) |
| follower | Auto-follow authors | With comments |
| covers | Animated GIF generation | With publisher |
| publisher | Full article pipeline | 1x daily (Nathan) |
| learner | Analytics + insights | On-demand |
| tracker | Follower growth + reports | Weekly |

## Key Patterns

- All IDs are strings (ObjectId), not integers
- Dedup: reacted.json, commented.json, followed.json, published_history.json
- Filter chain: scout -> filter_own -> filter_engaged -> filter_quality
- Quality gate on comments: max 280 chars, 1-2 sentences, no generic phrases
- Tags as objects: `[{"slug": "python"}]` not `["python"]`

## Context

Nathan: load compact pattern files before writing any code.
See ~/netanel/CLAUDE.md -> Agent Project Context section for full list.
