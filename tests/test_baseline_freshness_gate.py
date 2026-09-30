"""Gate B freshness: rollback PRs must accept still-drifted live state.

Merged rollback PRs run through the Accept apply path (is_revert=False)
but store reversed_changes (before=drifted, after=IaC).  Using fix/accept
semantics (expected=after only) falsely flags live=drifted as stale.
"""
import os
import sys
import unittest

_DR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drift_reconciler")
sys.path.insert(0, _DR)

from drift_baseline import (  # noqa: E402
    check_baseline_freshness,
    freshness_expected_tokens,
)
from rollback_check import _extract_field_values  # noqa: E402


def _plan(address: str, before: dict, after: dict, actions=None):
    return {
        "resource_changes": [{
            "address": address,
            "change": {
                "before": before,
                "after": after,
                "actions": actions or ["update"],
            },
        }],
    }


class FreshnessExpectedTokensTests(unittest.TestCase):
    def test_fix_accept_expects_live_after_only(self):
        tokens = freshness_expected_tokens(
            "memory_size",
            {"before": 128, "after": 250},
            rollback_semantics=False,
        )
        self.assertEqual(tokens, {"250"})

    def test_rollback_accepts_drifted_or_restored(self):
        # Rollback PR reversed_changes: before=drifted, after=IaC
        tokens = freshness_expected_tokens(
            "memory_size",
            {"before": 250, "after": 128},
            rollback_semantics=True,
        )
        self.assertEqual(tokens, {"128", "250"})


class CheckBaselineFreshnessTests(unittest.TestCase):
    def test_rollback_pr_still_drifted_is_fresh(self):
        """The exact false-positive from PR apply logs:
        expected=128 actual=250 when applying a merged rollback PR.
        """
        plan = _plan(
            "aws_lambda_function.hello",
            before={"memory_size": 250},
            after={"memory_size": 128},
        )
        baselines = [{
            "resource_id": "aws_lambda_function.hello",
            # reversed_changes stored on the rollback PR
            "changes": {"memory_size": {"before": 250, "after": 128}},
        }]
        err = check_baseline_freshness(
            plan, baselines,
            rollback_semantics=True,
            extract_field_values=_extract_field_values,
        )
        self.assertIsNone(err, err)

    def test_rollback_pr_third_value_is_stale(self):
        plan = _plan(
            "aws_lambda_function.hello",
            before={"memory_size": 300},
            after={"memory_size": 128},
        )
        baselines = [{
            "resource_id": "aws_lambda_function.hello",
            "changes": {"memory_size": {"before": 250, "after": 128}},
        }]
        err = check_baseline_freshness(
            plan, baselines,
            rollback_semantics=True,
            extract_field_values=_extract_field_values,
        )
        self.assertIsNotNone(err)
        self.assertIn("stale field", err)
        self.assertIn("actual=300", err)

    def test_fix_accept_wrong_semantics_would_false_positive(self):
        """Document the bug: same baselines/plan with fix semantics fails."""
        plan = _plan(
            "aws_lambda_function.hello",
            before={"memory_size": 250},
            after={"memory_size": 128},
        )
        baselines = [{
            "resource_id": "aws_lambda_function.hello",
            "changes": {"memory_size": {"before": 250, "after": 128}},
        }]
        err = check_baseline_freshness(
            plan, baselines,
            rollback_semantics=False,
            extract_field_values=_extract_field_values,
        )
        self.assertIsNotNone(err)
        self.assertIn("expected=128", err)
        self.assertIn("actual=250", err)

    def test_fix_accept_live_still_matches_after(self):
        plan = _plan(
            "aws_lambda_function.hello",
            before={"memory_size": 250},
            after={"memory_size": 250},
            actions=["no-op"],
        )
        baselines = [{
            "resource_id": "aws_lambda_function.hello",
            "changes": {"memory_size": {"before": 128, "after": 250}},
        }]
        err = check_baseline_freshness(
            plan, baselines,
            rollback_semantics=False,
            extract_field_values=_extract_field_values,
        )
        self.assertIsNone(err, err)

    def test_empty_baselines_fail_closed(self):
        err = check_baseline_freshness(
            {}, [],
            rollback_semantics=True,
            extract_field_values=_extract_field_values,
        )
        self.assertIn("no usable baseline", err)


if __name__ == "__main__":
    unittest.main()
