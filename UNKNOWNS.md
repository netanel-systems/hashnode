# Hashnode — Unknowns

*What we don't know yet. Check here BEFORE saying "I don't know."*

---

## Resolved

| Date | Question | Answer |
|------|----------|--------|
| 2026-02-23 | How do Hashnode feed tags work? | Feed filter `tags` field expects `[ObjectId!]` strings, not slug objects. Resolve via `get_tag(slug)` first. |
| 2026-02-23 | What format do publishPost tags need? | `TagInput` needs either existing tag `id` OR both `slug` and `name` to create new. Client resolves plain slug strings via `get_tag()` lookup. |
| 2026-02-23 | Does Hashnode show who liked an article? | No — only total reaction count + author notification. Individual likers not publicly visible. |
| 2026-02-23 | What does likesCount 1-5 mean? | Like Medium claps — weighted reactions. 5 = max enthusiasm. We randomize for authenticity. |

## Open

| Date | Question | Context |
|------|----------|---------|
| 2026-02-23 | What is Hashnode's daily article publishing limit? | We're set to 3/day. Is there a platform limit? |
| 2026-02-23 | How to upload cover images to Hashnode CDN? | **Resolved 2026-02-23:** Hashnode has NO GraphQL upload mutation. `publishPost` requires a pre-hosted URL via `coverImageOptions.coverImageURL`. Need external CDN (GitHub raw, imgbb, or own S3). Articles publish fine without covers. Low priority until revenue. |
| 2026-02-23 | Does Hashnode have anti-automation detection? | We're well under rate limits but unclear if they flag accounts with high automated activity patterns. |
