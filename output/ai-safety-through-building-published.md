# Hashnode Publish Report -- AI Safety Through Building

## Article Details

- **Title:** Why Building AI Agents Made Me Care More About AI Safety
- **Subtitle:** Real safety patterns from running 9 autonomous agent teams in production. Not theory. Not policy papers. Constraints that actually work.
- **URL:** https://klementgunndu1.hashnode.dev/why-building-ai-agents-made-me-care-more-about-ai-safety
- **Post ID:** 69a0976ab81c9197a5ef9d91
- **Published At:** 2026-02-26T18:56:42.138Z
- **Tags:** ai, python, programming, software-engineering
- **Word Count:** ~1,670 words
- **Cover Image:** Unsplash (circuit board, black and white)

## Topic Selection

Assigned directly by user. Topic: "Why Building AI Agents Made Me Care More About AI Safety."

Dedup check: No previous article covers AI safety. Existing articles cover memory layers, MCP servers, vibe coding, supervisor pattern, and FalkorDB/Graphiti.

## Content Structure

1. **Hook:** Counter-intuitive opener -- "Most people who talk about AI safety have never shipped an autonomous agent to production."
2. **Context:** 9 agent teams, 1,100+ sessions, 28 documented violations
3. **Pattern 1:** Everything Is Bounded (Pydantic config with ge/le constraints)
4. **Pattern 2:** Bounded Autonomy with Clear Escalation (retry logic, backoff caps, hard timeouts)
5. **Pattern 3:** Every Failure Becomes a Rule (VIOLATIONS.md, append-only, real examples V002/V017/V018/V019)
6. **Pattern 4:** The Step Policy Enforcer (SPE pattern library, 17 patterns from real failures)
7. **Pattern 5:** Atomic Operations and Corruption Prevention (temp file + POSIX rename)
8. **Pattern 6:** Review Loops Are Non-Negotiable (publish_cycle safety gates, dedup, rate limiting)
9. **5 Takeaways:** Safety = constraints, learned not designed, separation of concerns, autonomy + safety compatible, silent failure = real danger
10. **CTA:** Follow @klement_gunndu for more AI agent architecture content

## Code Examples (All From Real System)

All code shown in the article is from actual production files:

| Code Block | Source File | Verified |
|-----------|------------|----------|
| HashnodeConfig bounds | `teams/hashnode/hashnode/config.py` | Yes |
| HashnodeClient retry logic | `teams/hashnode/hashnode/client.py` | Yes |
| SPE patterns | `.nathan/knowledge/spe-patterns.md` | Yes |
| atomic_write_json | `teams/hashnode/hashnode/storage.py` | Yes |
| publish_cycle safety gates | `teams/hashnode/hashnode/publisher.py` | Yes |
| Crontab safety rule | `.nathan/DOS.md` (V017 correction) | Yes |

No fabricated tools, APIs, or libraries.

## Brand Voice Self-Check

- [x] Hook is counter-intuitive ("Most people who talk about AI safety have never shipped...")
- [x] Specific numbers in first 3 paragraphs (9 teams, 1,100+ sessions, 28 violations, 12 jobs, 500 points)
- [x] Concrete examples with proof (real violation numbers, real code, real penalty points)
- [x] No hedging language
- [x] No warm-up sentences
- [x] No generic AI hype words
- [x] No borrowed authority without grounding ("adapted from NASA JPL's Power of 10" -- referenced, not claimed)
- [x] No faith/religious language
- [x] CTA is secular and correct
- [x] "We" used for company work

## API Details

- **Endpoint:** https://gql.hashnode.com/
- **Mutation:** publishPost
- **Publication ID:** 699d10d3cf5def0f6ad0cc78
- **HTTP Status:** 200
- **GraphQL errors:** None
- **Cover image:** Set via coverImageOptions.coverImageURL (Unsplash hosted)

## Pipeline Steps Completed

1. Read existing published articles for dedup (5 previous articles, none on AI safety)
2. Read brand voice guidelines
3. Read real system code for authentic examples (config.py, client.py, publisher.py, storage.py, spe-patterns.md, VIOLATIONS.md, DOS.md)
4. Resolved 4 tag slugs to ObjectIds via GraphQL
5. Article written (~1,670 words, 10 sections, 6 code blocks from real files)
6. Brand voice self-review (all 10 checks passed)
7. Published via GraphQL publishPost mutation
8. Verified live via get_post query
9. Updated published_history.json (6 entries)
10. Output report written
