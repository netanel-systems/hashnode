# Hashnode — Violations

*What went wrong. Every mistake logged, every lesson learned.*

---

| Date | ID | What Happened | Rule Broken | Impact | Correction |
|------|----|---------------|-------------|--------|------------|
| 2026-02-23 | HN-V001 | Feed query with tags returned 400 — passed `{slug: "python"}` objects instead of ObjectId strings | Always verify API schema before building queries | Feed queries with tag filters failed until fixed | Added `resolve_tag_ids()` method, rewrote `get_feed()` to use GraphQL variables |
| 2026-02-23 | HN-V002 | publishPost failed — tags need `{slug, name}` or `{id}`, we passed `{slug}` only | Read API error messages carefully, test mutations before deploying | First publish attempt failed | Added tag resolution in `publish_post()` — resolves slugs to full `{id, slug, name}` objects |
| 2026-02-23 | HN-V003 | Comments/follows never ran — first day, no log files exist | Set up monitoring from day 1, not after problems appear | Zero comments and follows on launch day | Schedule monitor now checks all jobs, crons verified for tomorrow |
| 2026-02-23 | HN-V004 | Gemini references left in hashnode config.py and publisher.py docstring | Remove deprecated code immediately, don't leave dead references | Confusion about which LLM is used (we use Claude via nathan-team, not Gemini) | Removed `gemini_api_keys` from config, updated publisher docstring |
| 2026-02-23 | HN-V005 | Daily limit check used `date.today()` (local) vs UTC `published_at` — could allow extra publishes near midnight | Use consistent timezone (UTC) for all date comparisons | Potential to exceed daily article limit by 1 article on timezone boundary | Changed to `datetime.now(timezone.utc).date()` for consistent UTC comparison |
| 2026-02-23 | HN-V006 | `save_json_ids` and `_save_published_history` used non-atomic writes — crash mid-write corrupts data | All file writes must be atomic (temp + rename) | Data loss risk on crash | Added `_atomic_write_json()` using tempfile + os.replace() |
