"""Hashnode Growth Engine — content publishing + engagement automation.

Pure API-native. No browser automation needed (unlike dev.to).
Everything goes through Hashnode's GraphQL API at gql.hashnode.com.

Modules:
- config: HashnodeConfig (pydantic-settings, HASHNODE_ prefix)
- client: HashnodeClient (GraphQL mutations + queries)
- scout: ArticleScout — discover engagement targets from feeds
- reactor: ReactionEngine — like articles (standalone cron)
- commenter: CommentEngine — post genuine comments with quality gate
- follower: FollowEngine — auto-follow engaged authors
- covers: CoverGenerator — animated GIF cover images
- publisher: ArticlePublisher — full article creation pipeline
- learner: GrowthLearner — track what works, adapt
- tracker: GrowthTracker — follower growth, reciprocity, reports
- storage: Shared JSON storage utilities
"""

__version__ = "0.1.0"
