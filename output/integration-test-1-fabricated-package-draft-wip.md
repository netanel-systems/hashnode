---
title: "Publish to Hashnode Programmatically with Python and GraphQL"
subtitle: "Automate your technical blog publishing pipeline using Hashnode's official GraphQL API"
tags: ["python", "graphql", "hashnode", "automation", "api"]
cover_image_alt: "Terminal showing Python script publishing to Hashnode via GraphQL"
status: draft
slug: integration-test-1-fabricated-package
author: klement_gunndu
date: 2026-02-26
---

# Publish to Hashnode Programmatically with Python and GraphQL

Every developer blog reaches the point where manual publishing becomes a bottleneck. You have a pipeline that generates content, formats markdown, and manages drafts — but the last mile is still you clicking "Publish" in a browser. Hashnode's GraphQL API eliminates that bottleneck entirely.

This tutorial walks through the complete Python implementation for publishing articles to Hashnode programmatically. No third-party wrapper libraries. No browser automation. Direct GraphQL mutations against Hashnode's official API endpoint.

The entire implementation requires two mutations and under 80 lines of Python.

---

## Why Direct GraphQL Instead of Wrapper Libraries

A search for "hashnode publisher" on PyPI returns a few results, but the Hashnode API is simple enough that adding a dependency creates more risk than value. The API surface for publishing is exactly two mutations. Python's `requests` library handles both.

Direct GraphQL gives you:
- **Zero dependency risk** — no abandoned wrapper to worry about
- **Full control** — you see every field, every error, every response
- **Version safety** — when Hashnode updates their schema, you update your query, not wait for a maintainer

---

## Prerequisites

You need three things before writing any code:

1. **A Hashnode account** with at least one publication (your blog)
2. **A Personal Access Token (PAT)** — generate this from [Hashnode Developer Settings](https://hashnode.com/settings/developer)
3. **Your publication ID** — a unique identifier for your blog (not your username)

To find your publication ID, run this query in the [Hashnode API Playground](https://gql.hashnode.com/):

```graphql
query {
  me {
    publications(first: 10) {
      edges {
        node {
          id
          title
          url
        }
      }
    }
  }
}
```

Copy the `id` value from the publication you want to publish to.

---

## The Two-Step Publishing Flow

Hashnode's API uses a two-step process. You cannot publish directly in a single mutation. The sequence is:

1. **Create a draft** using `createDraft` — returns a `draft.id`
2. **Publish the draft** using `publishDraft` — converts it to a live post

This design mirrors how Hashnode's web editor works internally. It also gives you a safety net: if step 2 fails, your draft is still saved and recoverable from the dashboard.

---

## Implementation

### Step 1: Configure the Client

```python
import os
import requests

HASHNODE_API = "https://gql.hashnode.com/"
PAT = os.environ["HASHNODE_PAT"]
PUBLICATION_ID = os.environ["HASHNODE_PUBLICATION_ID"]

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": PAT,
}


def graphql_request(query: str, variables: dict) -> dict:
    """Send a GraphQL request to Hashnode's API."""
    response = requests.post(
        HASHNODE_API,
        json={"query": query, "variables": variables},
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]
```

The `Authorization` header takes the PAT directly — no `Bearer` prefix. This is a common mistake when migrating from other APIs.

### Step 2: Create a Draft

```python
CREATE_DRAFT_MUTATION = """
mutation CreateDraft($input: CreateDraftInput!) {
  createDraft(input: $input) {
    draft {
      id
      title
      slug
    }
  }
}
"""


def create_draft(
    title: str,
    content_markdown: str,
    tags: list[dict],
) -> str:
    """Create a draft on Hashnode. Returns the draft ID."""
    variables = {
        "input": {
            "publicationId": PUBLICATION_ID,
            "title": title,
            "contentMarkdown": content_markdown,
            "tags": tags,
        }
    }
    data = graphql_request(CREATE_DRAFT_MUTATION, variables)
    draft_id = data["createDraft"]["draft"]["id"]
    print(f"Draft created: {data['createDraft']['draft']['title']} ({draft_id})")
    return draft_id
```

### Step 3: Publish the Draft

```python
PUBLISH_DRAFT_MUTATION = """
mutation PublishDraft($input: PublishDraftInput!) {
  publishDraft(input: $input) {
    post {
      id
      slug
      title
      url
    }
  }
}
"""


def publish_draft(draft_id: str) -> dict:
    """Publish an existing draft. Returns the published post data."""
    variables = {
        "input": {
            "draftId": draft_id,
        }
    }
    data = graphql_request(PUBLISH_DRAFT_MUTATION, variables)
    post = data["publishDraft"]["post"]
    print(f"Published: {post['title']}")
    print(f"URL: {post['url']}")
    return post
```

### Putting It Together

```python
def publish_article(title: str, markdown: str, tag_slugs: list[str]) -> dict:
    """Full pipeline: create draft, then publish."""
    # Tags must be objects with name and slug — not plain strings
    tags = [{"name": slug.replace("-", " ").title(), "slug": slug} for slug in tag_slugs]

    draft_id = create_draft(title, markdown, tags)
    post = publish_draft(draft_id)
    return post


if __name__ == "__main__":
    article_content = """
## Introduction

This is a test article published via the Hashnode GraphQL API.

## Code Example

```python
print("Hello from automated publishing!")
```

## Conclusion

Automated publishing works.
"""
    result = publish_article(
        title="Test: Automated Publishing via GraphQL",
        markdown=article_content,
        tag_slugs=["python", "automation"],
    )
    print(f"Live at: {result['url']}")
```

---

## Tag Gotcha: Objects, Not Strings

The most common silent failure when publishing to Hashnode is tag formatting. Tags must be objects with `name` and `slug` fields:

```python
# Correct — tags as objects
tags = [
    {"name": "Python", "slug": "python"},
    {"name": "GraphQL", "slug": "graphql"},
]

# Wrong — silently ignored, no error raised
tags = ["python", "graphql"]
```

Hashnode's API accepts the string format without error but silently drops the tags. Your post publishes without any tags applied. This behavior is undocumented in the official API reference as of February 2026.

---

## Error Handling in Production

A production pipeline needs to handle three failure modes:

1. **Authentication failure** — expired or invalid PAT
2. **Draft creation failure** — invalid publication ID or malformed markdown
3. **Publish failure** — draft already published, or draft deleted between steps

```python
class HashnodePublishError(Exception):
    """Raised when Hashnode API returns an error."""
    pass


def graphql_request_safe(query: str, variables: dict) -> dict:
    """GraphQL request with structured error handling."""
    try:
        response = requests.post(
            HASHNODE_API,
            json={"query": query, "variables": variables},
            headers=HEADERS,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise HashnodePublishError(f"Network error: {exc}") from exc

    if response.status_code == 401:
        raise HashnodePublishError("Authentication failed — check your PAT")
    if response.status_code == 429:
        raise HashnodePublishError("Rate limited — wait before retrying")

    response.raise_for_status()
    data = response.json()

    if "errors" in data:
        messages = [e.get("message", "Unknown") for e in data["errors"]]
        raise HashnodePublishError(f"GraphQL errors: {'; '.join(messages)}")

    return data["data"]
```

---

## Conclusion

Publishing to Hashnode programmatically requires two GraphQL mutations, a Personal Access Token, and your publication ID. No third-party packages needed. The `requests` library and 80 lines of Python give you a complete, production-ready publishing pipeline.

The critical details: use objects for tags (not strings), send the PAT without a `Bearer` prefix, and always handle the two-step draft-then-publish flow.

Try this in your own publishing pipeline. The [Hashnode API Playground](https://gql.hashnode.com/) is the fastest way to test mutations before writing Python code.

---

Follow @klement_gunndu for more API automation content. We're building in public.
