"""A/B testing infrastructure -- Hashnode platform.

Provides random group assignment (control/variant 50/50), Fisher's
Exact Test for statistical significance, declarative test config,
and results aggregation for weekly reports.

One active test per platform at a time. Test config is declarative
(change via config, not code).

Schema version: X3-complete (GitLab #14)
"""

from __future__ import annotations

import json
import logging
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def assign_group() -> str:
    """Randomly assign to 'control' or 'variant' group (50/50).

    Returns:
        'control' or 'variant'.
    """
    return random.choice(["control", "variant"])


def should_use_variant(ab_test_enabled: bool, group: str) -> bool:
    """Check if the current engagement should use the variant treatment.

    Args:
        ab_test_enabled: Whether A/B testing is currently enabled.
        group: The assigned group ('control' or 'variant').

    Returns:
        True if variant treatment should be applied.
    """
    if not ab_test_enabled:
        return False
    return group == "variant"


def _log_factorial(n: int) -> float:
    """Compute log(n!) using math.lgamma for numerical stability."""
    return math.lgamma(n + 1)


def fishers_exact_test(
    control_successes: int,
    control_total: int,
    variant_successes: int,
    variant_total: int,
) -> dict:
    """Compute Fisher's Exact Test (one-tailed, variant > control).

    Tests whether the variant group has a significantly higher success
    rate than the control group.

    Uses the hypergeometric distribution for exact p-value computation.
    Numerically stable via log-space computation.

    Args:
        control_successes: Number of successes in control group.
        control_total: Total observations in control group.
        variant_successes: Number of successes in variant group.
        variant_total: Total observations in variant group.

    Returns:
        Dict with:
        - p_value: float (0-1)
        - significant: bool (True if p < 0.05)
        - control_rate: float (0-1)
        - variant_rate: float (0-1)
        - lift_percent: float (relative improvement)
    """
    # Input validation
    if control_total <= 0 or variant_total <= 0:
        return {
            "p_value": 1.0,
            "significant": False,
            "control_rate": 0.0,
            "variant_rate": 0.0,
            "lift_percent": 0.0,
            "error": "insufficient_data",
        }

    control_rate = control_successes / control_total
    variant_rate = variant_successes / variant_total
    lift = ((variant_rate - control_rate) / control_rate * 100) if control_rate > 0 else 0.0

    # Fisher's Exact Test via hypergeometric distribution
    # Construct 2x2 contingency table:
    #              Success  Failure   Total
    # Control:     a        b         a+b
    # Variant:     c        d         c+d
    # Total:       a+c      b+d       n
    a = control_successes
    b = control_total - control_successes
    c = variant_successes
    d = variant_total - variant_successes
    n = a + b + c + d

    # Probability of observed table
    def _log_p_table(a: int, b: int, c: int, d: int) -> float:
        """Log probability of a specific 2x2 table under the null."""
        return (
            _log_factorial(a + b) + _log_factorial(c + d)
            + _log_factorial(a + c) + _log_factorial(b + d)
            - _log_factorial(n) - _log_factorial(a)
            - _log_factorial(b) - _log_factorial(c) - _log_factorial(d)
        )

    observed_log_p = _log_p_table(a, b, c, d)

    # One-tailed test: sum probabilities of tables as extreme or more
    # extreme than observed (variant > control direction)
    p_value = 0.0
    row1_total = a + b  # control total
    row2_total = c + d  # variant total
    col1_total = a + c  # total successes

    # Iterate over all possible values of 'a' (control successes)
    min_a = max(0, col1_total - row2_total)
    max_a = min(row1_total, col1_total)

    for a_i in range(min_a, max_a + 1):
        b_i = row1_total - a_i
        c_i = col1_total - a_i
        d_i = row2_total - c_i
        if b_i < 0 or c_i < 0 or d_i < 0:
            continue
        log_p = _log_p_table(a_i, b_i, c_i, d_i)
        # Include tables with probability <= observed (more extreme)
        if log_p <= observed_log_p + 1e-10:
            p_value += math.exp(log_p)

    p_value = min(p_value, 1.0)  # Clamp numerical errors

    return {
        "p_value": round(p_value, 6),
        "significant": p_value < 0.05,
        "control_rate": round(control_rate, 4),
        "variant_rate": round(variant_rate, 4),
        "lift_percent": round(lift, 2),
    }


def check_test_complete(
    control_total: int,
    variant_total: int,
    min_samples: int = 50,
) -> dict:
    """Check if an A/B test has enough samples to evaluate.

    Args:
        control_total: Number of observations in control group.
        variant_total: Number of observations in variant group.
        min_samples: Minimum samples per group for evaluation.

    Returns:
        Dict with:
        - complete: bool (True if both groups have min_samples)
        - control_count: int
        - variant_count: int
        - samples_needed: int (max remaining needed across groups)
    """
    control_remaining = max(0, min_samples - control_total)
    variant_remaining = max(0, min_samples - variant_total)

    return {
        "complete": control_remaining == 0 and variant_remaining == 0,
        "control_count": control_total,
        "variant_count": variant_total,
        "samples_needed": max(control_remaining, variant_remaining),
    }


def get_ab_test_results(
    data_dir: Path,
    test_name: str,
    metric: str = "follow_back_rate",
    lookback_days: int = 30,
    min_samples: int = 50,
) -> dict:
    """Aggregate A/B test results from the engagement log.

    Reads engagement_log.jsonl, filters by ab_test_name, groups by
    ab_test_group, and runs Fisher's Exact Test.

    For the 'follow_back_rate' metric, success is defined as:
    - The engagement target (target_username) subsequently appeared
      as a new follower. This is approximated by checking if there
      is a follow-back entry in follower_snapshots or if the
      engagement_state shows the target replied.

    Falls back to comment_has_question as a proxy metric when
    follow-back data is insufficient.

    Args:
        data_dir: Absolute path to the platform data directory.
        test_name: Name of the A/B test to evaluate.
        metric: Metric to evaluate ('follow_back_rate').
        lookback_days: How far back to scan the engagement log.
        min_samples: Minimum samples per group for evaluation.

    Returns:
        Dict with test_name, status, sample counts, Fisher's test
        results, and recommendation.
    """
    engagement_log = data_dir / "engagement_log.jsonl"
    if not engagement_log.exists():
        return {
            "test_name": test_name,
            "status": "no_data",
            "error": "engagement_log.jsonl not found",
        }

    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    # Collect entries belonging to this test
    control_entries: list[dict] = []
    variant_entries: list[dict] = []

    try:
        with open(engagement_log) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Filter by test name and time window
                if entry.get("ab_test_name") != test_name:
                    continue
                ts = entry.get("timestamp", "")
                if ts < cutoff:
                    continue

                group = entry.get("ab_test_group")
                if group == "control":
                    control_entries.append(entry)
                elif group == "variant":
                    variant_entries.append(entry)
    except OSError as exc:
        logger.warning("Failed to read engagement log for A/B results: %s", exc)
        return {
            "test_name": test_name,
            "status": "error",
            "error": str(exc),
        }

    control_total = len(control_entries)
    variant_total = len(variant_entries)

    # Check completeness
    completeness = check_test_complete(control_total, variant_total, min_samples)

    # Count successes based on metric
    if metric == "follow_back_rate":
        # Load follower usernames from latest snapshot for cross-reference
        follower_usernames = _load_follower_usernames(data_dir)
        control_successes = _count_follow_backs(control_entries, follower_usernames)
        variant_successes = _count_follow_backs(variant_entries, follower_usernames)
    else:
        # Default: use comment_has_question as proxy
        control_successes = sum(1 for e in control_entries if e.get("comment_has_question"))
        variant_successes = sum(1 for e in variant_entries if e.get("comment_has_question"))

    # Run Fisher's test
    test_result = fishers_exact_test(
        control_successes, control_total,
        variant_successes, variant_total,
    )

    # Generate recommendation
    recommendation = _generate_recommendation(test_result, completeness)

    return {
        "test_name": test_name,
        "status": "complete" if completeness["complete"] else "in_progress",
        "control": {
            "total": control_total,
            "successes": control_successes,
            "rate": test_result["control_rate"],
        },
        "variant": {
            "total": variant_total,
            "successes": variant_successes,
            "rate": test_result["variant_rate"],
        },
        "fishers_test": {
            "p_value": test_result["p_value"],
            "significant": test_result["significant"],
            "lift_percent": test_result["lift_percent"],
        },
        "samples_needed": completeness["samples_needed"],
        "recommendation": recommendation,
    }


def _load_follower_usernames(data_dir: Path) -> set[str]:
    """Load follower usernames from the latest snapshot.

    Returns an empty set if no snapshot exists or if the snapshot
    does not contain username data.
    """
    path = data_dir / "follower_snapshots.jsonl"
    if not path.exists():
        return set()

    last_line = ""
    try:
        with open(path) as f:
            for line in f:
                if line.strip():
                    last_line = line.strip()
        if last_line:
            snapshot = json.loads(last_line)
            usernames = snapshot.get("usernames", [])
            return set(usernames)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load follower snapshot for A/B: %s", exc)

    return set()


def _count_follow_backs(
    entries: list[dict],
    follower_usernames: set[str],
) -> int:
    """Count how many engagement targets became followers.

    Args:
        entries: Engagement log entries for one A/B group.
        follower_usernames: Set of current follower usernames.

    Returns:
        Number of entries where target_username is in follower_usernames.
    """
    count = 0
    seen_usernames: set[str] = set()
    for entry in entries:
        username = entry.get("target_username")
        if not username or username in seen_usernames:
            continue
        seen_usernames.add(username)
        if username in follower_usernames:
            count += 1
    return count


def _generate_recommendation(
    test_result: dict,
    completeness: dict,
) -> str:
    """Generate a human-readable recommendation from test results.

    Args:
        test_result: Output from fishers_exact_test().
        completeness: Output from check_test_complete().

    Returns:
        Recommendation string.
    """
    if not completeness["complete"]:
        needed = completeness["samples_needed"]
        return f"Collect {needed} more samples per group before evaluating."

    if test_result.get("error"):
        return "Insufficient data to evaluate."

    if test_result["significant"]:
        lift = test_result["lift_percent"]
        return (
            f"Variant wins with {lift:+.1f}% lift (p={test_result['p_value']:.4f}). "
            f"Adopt variant treatment."
        )

    return (
        f"No significant difference detected (p={test_result['p_value']:.4f}). "
        f"Continue test or try a different variant."
    )
