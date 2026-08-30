"""Check: _make_resource_id never produces trailing-dot addresses.

Run: python -m unittest tests.test_unmanaged_resource_id
"""
import unittest

from drift_reconciler.unmanaged_scanner import _make_resource_id


class MakeResourceIdTests(unittest.TestCase):
    def test_name_tag_present(self):
        self.assertEqual(
            _make_resource_id("aws_instance", "web", "i-abc"),
            "aws_instance.web",
        )

    def test_no_name_tag_uses_aws_id(self):
        self.assertEqual(
            _make_resource_id("aws_internet_gateway", "", "igw-abc123"),
            "aws_internet_gateway.igw-abc123",
        )

    def test_id_only_resource(self):
        self.assertEqual(
            _make_resource_id("aws_s3_bucket", "my-bucket", "my-bucket"),
            "aws_s3_bucket.my-bucket",
        )

    def test_empty_raw_name(self):
        self.assertEqual(
            _make_resource_id("aws_nat_gateway", "", "nat-xyz"),
            "aws_nat_gateway.nat-xyz",
        )

    def test_raw_name_none(self):
        self.assertEqual(
            _make_resource_id("aws_vpc", None, "vpc-123"),
            "aws_vpc.vpc-123",
        )

    def test_both_empty_raises(self):
        with self.assertRaises(ValueError):
            _make_resource_id("aws_igw", "", "")

    def test_none_name_empty_id_raises(self):
        with self.assertRaises(ValueError):
            _make_resource_id("aws_nat", None, "")

    def test_never_trailing_dot(self):
        samples = [
            _make_resource_id("aws_instance", "web", "i-abc"),
            _make_resource_id("aws_internet_gateway", "", "igw-abc123"),
            _make_resource_id("aws_vpc", None, "vpc-123"),
        ]
        for rid in samples:
            self.assertFalse(rid.endswith("."), rid)


if __name__ == "__main__":
    unittest.main()
