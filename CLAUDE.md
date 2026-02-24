# Hashnode Growth Engine — Team Context

> Hashnode content publishing + engagement automation via GraphQL API.
> Pure API-native — no browser automation needed (unlike dev.to).

---

## Quick Reference

- **API:** `https://gql.hashnode.com/` (POST, GraphQL)
- **Auth:** PAT via `Authorization` header (no Bearer prefix)
- **Rate limit:** 500 req/min authenticated
- **Tags:** Must be `{slug: "python"}` objects, not strings

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
