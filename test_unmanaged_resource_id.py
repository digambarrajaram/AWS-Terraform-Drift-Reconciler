"""Check: _make_resource_id never produces trailing-dot addresses."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "drift_reconciler"))

from unmanaged_scanner import _make_resource_id

# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------
PASS = 0
FAIL = 0

def check(label: str, got: str, expect: str) -> None:
    global PASS, FAIL
    ok = got == expect and not got.endswith(".")
    if ok:
        PASS += 1
        print(f"  PASS  {label}:  {got}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}:  got={got!r}  expected={expect!r}")

def check_raises(label: str, rtype: str, raw_name: str, aws_id: str) -> None:
    global PASS, FAIL
    try:
        rid = _make_resource_id(rtype, raw_name, aws_id)
        FAIL += 1
        print(f"  FAIL  {label}:  expected ValueError, got {rid!r}")
    except ValueError:
        PASS += 1
        print(f"  PASS  {label}:  raised ValueError")

print("=== Normal cases ===")
check("Name tag present",  _make_resource_id("aws_instance", "web", "i-abc"), "aws_instance.web")
check("No Name tag",       _make_resource_id("aws_internet_gateway", "", "igw-abc123"), "aws_internet_gateway.igw-abc123")
check("ID-only resource",  _make_resource_id("aws_s3_bucket", "my-bucket", "my-bucket"), "aws_s3_bucket.my-bucket")
check("Empty raw_name",    _make_resource_id("aws_nat_gateway", "", "nat-xyz"), "aws_nat_gateway.nat-xyz")

print()
print("=== Edge cases ===")
check("raw_name is None",  _make_resource_id("aws_vpc", None, "vpc-123"), "aws_vpc.vpc-123")
check_raises("both empty", "aws_igw", "", "")
check_raises("raw_name=None, id empty", "aws_nat", None, "")

print()
print(f"  {PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
