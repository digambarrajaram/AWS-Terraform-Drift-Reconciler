"""Check the possibly-renamed matcher:
- replaced instance (same Name tag, different AWS ID) → strong match
- name-family variants ("prod-web" vs "prod-web-2") → strong match
- numbered pairs ("web-01" vs "web-02") → strong match
- shared identity tags (Role/Environment/…) → strong match
- unrelated same-type, different type, no tags → no match
- diff_unmanaged emits status=possibly_renamed carrying BOTH the live
  resource and the matched managed resource

Run: python -m unittest tests.test_unmanaged_matcher
"""
import unittest

from drift_reconciler import unmanaged_scanner as us


def _live(**overrides):
    base = {
        "type": "aws_instance",
        "id": "i-0abc",
        "arn": "arn:aws:ec2:us-east-1:123456789012:instance/i-0abc",
        "tags": {"Name": "prod-web"},
        "is_default": False,
        "raw_name": "prod-web",
    }
    base.update(overrides)
    return base


def _managed(**overrides):
    base = {
        "type": "aws_instance",
        "name": "web",
        "arn": "arn:aws:ec2:us-east-1:123456789012:instance/i-0xyz",
        "tags": {"Name": "prod-web"},
    }
    base.update(overrides)
    return base


class StrongManagedMatchTests(unittest.TestCase):
    def test_replaced_instance_same_name_tag(self):
        m = us._strong_managed_match(_live(), [_managed()])
        self.assertIsNotNone(m)
        self.assertEqual(m["match_reason"], "name family")

    def test_name_family_suffix(self):
        live = _live(raw_name="prod-web-2", tags={"Name": "prod-web-2"})
        m = us._strong_managed_match(live, [_managed()])
        self.assertIsNotNone(m)
        self.assertEqual(m["match_reason"], "name family")

    def test_numbered_pair_family(self):
        live = _live(raw_name="web-02", tags={"Name": "web-02"})
        m = us._strong_managed_match(live, [_managed(tags={"Name": "web-01"})])
        self.assertIsNotNone(m)

    def test_identity_tag_match(self):
        live = _live(raw_name="other-name", tags={"Name": "other-name", "Role": "web-server"})
        m = us._strong_managed_match(live, [_managed(tags={"Name": "prod-web", "Role": "web-server"})])
        self.assertIsNotNone(m)
        self.assertEqual(m["match_reason"], "identity tags")

    def test_no_match(self):
        managed = [_managed()]
        self.assertIsNone(
            us._strong_managed_match(_live(raw_name="unrelated", tags={"Name": "unrelated"}), managed))
        self.assertIsNone(
            us._strong_managed_match(_live(type="aws_vpc", raw_name="prod-web", tags={"Name": "prod-web"}), managed))
        self.assertIsNone(
            us._strong_managed_match(_live(raw_name="", tags={}), [_managed(tags={})]))


class DiffEmitsPossiblyRenamedTests(unittest.TestCase):
    def test_finding_carries_both_live_and_managed(self):
        findings = us.diff_unmanaged(
            [_live()],
            [_managed()],
            region="us-east-1",
        )
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["status"], "possibly_renamed")
        self.assertEqual(f["resource_id"], "aws_instance.prod-web")
        self.assertEqual(f["possible_match"]["resource_id"], "aws_instance.web")
        self.assertEqual(f["possible_match"]["match_reason"], "name family")
        self.assertIn("managed resource", f["drift_summary"])

    def test_exact_address_match_still_skipped(self):
        # Same ARN in state → tracked, not flagged as possibly renamed.
        findings = us.diff_unmanaged(
            [_live(arn="arn:aws:ec2:us-east-1:123456789012:instance/i-0xyz")],
            [_managed()],
            region="us-east-1",
        )
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
