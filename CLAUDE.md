# Hashnode Growth Engine — Team Context

> Hashnode content publishing + engagement automation via GraphQL API.
> Pure API-native — no browser automation needed (unlike dev.to).

---

## Own-Post Engagement Rules (NON-NEGOTIABLE)

These rules govern how we interact with comments on our own Hashnode posts.

1. **One comment per post on others' content — no thread continuation.** We reply
   once to someone else's post, then stop. Never continue a thread we started.
2. **Every comment received on our own posts = like + reply.** No silent reads.
   Every comment deserves both a likeComment (show appreciation) and a specific reply.
3. **Like the post before replying to it.** When commenting on someone else's content,
   likePost first, then addComment. This applies in commenter.py cycles.
4. **Max engagement depth: 1 reply per incoming comment.** We respond to the first
   comment from any person via addReply. We do not reply to our own reply.
5. **Dedup is strict.** responded_comments.json is the source of truth.
6. **Reply must be specific.** Generic replies ("Thanks for reading!") are a CRITICAL violation.

### Responder Cron (2x daily)

```cron
# Own-post comment engagement — 9 AM UTC
0 9 * * * cd ~/netanel/teams/hashnode && .venv/bin/python -m hashnode.responder_main

# Own-post comment engagement — 3 PM UTC
0 15 * * * cd ~/netanel/teams/hashnode && .venv/bin/python -m hashnode.responder_main
```

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
