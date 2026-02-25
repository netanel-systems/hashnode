# Task Brief: Fix Hashnode Responder GraphQL Error

**Team:** Hashnode
**Priority:** MEDIUM
**Created:** 2026-02-25
**Source:** Delivery team platform verification

---

## Issue

Hashnode responder_main.py fails every 30 minutes with AttributeError when fetching own posts.

## Error Details

```
2026-02-25 13:00:01 [__main__] ERROR: Responder failed: 'NoneType' object has no attribute 'get'
Traceback (most recent call last):
  File "/home/intruder/netanel/teams/hashnode/hashnode/responder_main.py", line 59, in main
    summary = responder.run()
  File "/home/intruder/netanel/teams/hashnode/hashnode/responder.py", line 336, in run
    posts = self.fetch_own_posts()
  File "/home/intruder/netanel/teams/hashnode/hashnode/responder.py", line 144, in fetch_own_posts
    posts = self._fetch_publication_posts(host)
  File "/home/intruder/netanel/teams/hashnode/hashnode/responder.py", line 172, in _fetch_publication_posts
    data.get("publication", {}).get("posts", {}).get("edges", [])
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AttributeError: 'NoneType' object has no attribute 'get'
```

## Root Cause (Suspected)

Line 172 in responder.py attempts to call `.get()` on `data` which is `None`.

This suggests:
1. GraphQL query returned None (API error or schema mismatch)
2. Missing null check before accessing `data`
3. Possible publication host configuration issue

## Impact

- **Frequency:** Every 30 minutes (responder cron runs every 30 mins)
- **Severity:** MEDIUM — Responder fails but reactor works fine
- **User impact:** Own-post comments are not being monitored/responded to

## Expected Behavior

Responder should:
1. Successfully fetch own posts from publication
2. Check for new comments
3. Like and/or reply to comments
4. Handle API errors gracefully without crashing

## Debug Steps

1. Check publication host configuration in .env or config.py
2. Add null check for `data` before calling `.get()`
3. Log the raw GraphQL response when `data` is None
4. Verify publication host exists and is accessible
5. Test GraphQL query manually via HashnodeClient

## Files Involved

- `hashnode/responder.py` (line 172)
- `hashnode/responder_main.py` (line 59)
- `hashnode/client.py` (GraphQL execution)
- `hashnode/config.py` (publication host config)

## Acceptance Criteria

1. Responder runs without AttributeError
2. Either:
   - Successfully fetches own posts and processes comments, OR
   - Gracefully handles missing publication with clear error message
3. Error logged with helpful context (publication host, API response)
4. Cron runs clean for 24 hours without crashes

---

**Source:** Delivery team found this during platform publishing verification (2026-02-25).

**Log location:** `teams/hashnode/data/cron-2026-08.log`
