"""
Enumerate live AWS resources that may exist outside of Terraform state.

Usage (standalone test):
    python drift_reconciler/unmanaged_scanner.py --region us-east-1
"""

import argparse
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
from typing import Any

import requests

import boto3


# ---------------------------------------------------------------------------
# Per-service enumerators
# ---------------------------------------------------------------------------

def _scan_ec2_security_groups(session, region: str) -> list[dict[str, Any]]:
    """Return every EC2 security group in *region*.

    Default VPC security groups (named "default") are tagged
    ``is_default=True`` so the diff engine can suppress them later."""
    ec2 = session.client("ec2", region_name=region)
    results: list[dict[str, Any]] = []

    paginator = ec2.get_paginator("describe_security_groups")
    for page in paginator.paginate():
        for sg in page["SecurityGroups"]:
            is_default = sg["GroupName"] == "default"
            tags = {t["Key"]: t["Value"] for t in sg.get("Tags", [])}
            results.append(
                {
                    "type": "aws_security_group",
                    "id": sg["GroupId"],
                    "arn": f"arn:aws:ec2:{region}:{_account_id(session)}:security-group/{sg['GroupId']}",
                    "tags": tags,
                    "is_default": is_default,
                    "raw_name": sg["GroupName"],
                    "created_at": _resolve_created_at(tags),
                }
            )

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_account_id_cache: str | None = None


def _account_id(session) -> str:
    """Return the AWS account id for *session*, cached for the process."""
    global _account_id_cache
    if _account_id_cache is None:
        _account_id_cache = session.client("sts").get_caller_identity()["Account"]
    return _account_id_cache


def _resolve_created_at(tags: dict, api_timestamp=None) -> str | None:
    """Return an ISO-8601 creation timestamp for a resource.

    Uses *api_timestamp* from the AWS API response when available,
    otherwise checks common creation-date tag keys.  Returns ``None``
    when no timestamp can be determined — the caller treats this as
    "older than the 4-hour reporting threshold"."""
    if api_timestamp is not None:
        if hasattr(api_timestamp, "isoformat"):
            return api_timestamp.isoformat()
        return str(api_timestamp)
    for key in ("CreatedAt", "creation_date", "created", "CreationDate", "created_at"):
        val = tags.get(key)
        if val:
            return str(val)
    return None


# ---------------------------------------------------------------------------
# Terraform state reader
# ---------------------------------------------------------------------------

def _walk_state_resources(module: dict, resources: list[dict]) -> None:
    """Recurse into *module* and its child modules, appending every
    resource identity to *resources* in-place."""
    for res in module.get("resources", []):
        arn = res.get("values", {}).get("arn")
        resources.append(
            {
                "type": res["type"],
                "name": res["name"],
                "arn": arn if isinstance(arn, str) else None,
            }
        )
    for child in module.get("child_modules", []):
        _walk_state_resources(child, resources)


def load_managed_resources(tf_dir: str) -> list[dict[str, Any]]:
    """Return every resource tracked in the Terraform state for *tf_dir*.

    Runs ``terraform show -json`` which works for both local and remote
    (S3) backends — no need to parse backend.tf manually."""
    result = subprocess.run(
        ["terraform", "show", "-no-color", "-json"],
        cwd=tf_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"  ⚠ terraform show -json failed: {result.stderr[:400]}")
        return []

    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"  ⚠ Failed to parse terraform state JSON: {exc}")
        return []

    root = state.get("values", {}).get("root_module")
    if root is None:
        return []

    resources: list[dict[str, Any]] = []
    _walk_state_resources(root, resources)
    return resources


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------

def _load_exceptions(scope: str) -> list[dict[str, Any]]:
    """Load unmanaged exception entries for *scope* from Supabase.

    Returns an empty list when the table is empty or unreachable
    (the scan proceeds without suppression)."""
    try:
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            return []
        resp = requests.get(
            f"{url}/rest/v1/drift_exception_registry"
            f"?select=resource_type,resource_id_pattern,reason,approved_by,max_monthly_cost_usd"
            f"&scope=eq.{scope}&exception_type=eq.unmanaged&active=eq.true",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json() if resp.text else []
        return []
    except requests.RequestException:
        return []


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

_cost_cache: dict[str, Any] | None = None


def _load_cost_cache() -> dict[str, Any]:
    """Load cost_cache.json from the same directory as this module.

    The result is cached in the module-level ``_cost_cache`` so the
    file is only read once per process."""
    global _cost_cache
    if _cost_cache is not None:
        return _cost_cache
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cost_cache.json")
    try:
        with open(path, encoding="utf-8") as f:
            _cost_cache = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  ⚠ Could not load cost cache: {exc}")
        _cost_cache = {}
    return _cost_cache


def estimate_hourly_cost(resource: dict[str, Any], region: str) -> float | None:
    """Return the estimated on-demand hourly cost in USD for *resource*,
    or ``None`` when no pricing data is available for its type or spec.

    Pricing models in ``cost_cache.json``:
    - Per resource-hour (EC2, NAT GW, RDS, ElastiCache, ALB, NLB, EKS,
      EIP): direct lookup by spec → hourly rate.
    - Per GB-month (EBS, ECR, KMS, Secrets Manager, Route 53): rate
      divided by 730 to approximate hourly cost.
    - Usage-driven (Lambda, CloudFront): returns near-zero (idle cost).
    """
    cache = _load_cost_cache()
    if not cache:
        return None

    rtype = resource.get("type", "")
    spec = resource.get("spec")  # e.g. "t3.micro", "gp3", "db.t3.medium"

    # ---- Map terraform resource type to cache key ----
    _TYPE_MAP = {
        "aws_instance":           "ec2_instance",
        "aws_nat_gateway":        "nat_gateway",
        "aws_rds_instance":       "rds_instance",        # future enumerator
        "aws_elasticache_cluster": "elasticache_node",    # future enumerator
        "aws_lb":                 "alb",
        "aws_lb_nlb":             "nlb",
        "aws_eks_cluster":        "eks_cluster",          # future enumerator
        "aws_eip":                "eip",
        "aws_ebs_volume":         "ebs_volume",           # future enumerator
        "aws_kms_key":            "kms_key",              # future enumerator
        "aws_secretsmanager_secret": "secrets_manager_secret",  # future enumerator
        "aws_ecr_repository":     "ecr",                  # future enumerator
        "aws_route53_zone":       "route53_zone",         # future enumerator
        "aws_cloudfront_distribution": "cloudfront_distribution",  # future enumerator
        "aws_lambda_function":    "lambda_function",      # future enumerator
    }
    cache_key = _TYPE_MAP.get(rtype)
    if cache_key is None:
        return None

    service = cache.get(cache_key)
    if not isinstance(service, dict):
        return None

    # ---- Services with region-level flat rates (no spec needed) ----
    _FLAT_RATE_SERVICES = {"nat_gateway", "alb", "nlb", "eks_cluster"}
    if cache_key in _FLAT_RATE_SERVICES:
        return service.get(region)

    # ---- Services with region → spec → rate ----
    _SPEC_SERVICES = {"ec2_instance", "rds_instance", "elasticache_node", "eip"}
    if cache_key in _SPEC_SERVICES:
        if not spec:
            return None
        region_pricing = service.get(region, {})
        if isinstance(region_pricing, dict):
            return region_pricing.get(spec)

    # ---- EBS: per GB-month → hourly, needs size_gb ----
    if cache_key == "ebs_volume":
        if not spec:
            return None
        gb_per_month_rate = service.get(spec, {}).get(region)
        if gb_per_month_rate is None:
            return None
        size_gb = resource.get("size_gb", 0)
        if not size_gb:
            return None
        return round((size_gb * gb_per_month_rate) / 730, 6)

    # ---- Per-month → hourly services (KMS, Secrets Manager, ECR, Route 53) ----
    _MONTHLY_SERVICES = {"kms_key", "secrets_manager_secret", "ecr"}
    if cache_key in _MONTHLY_SERVICES:
        monthly = service.get(region)
        if monthly is None:
            return None
        return round(monthly / 730, 6)

    # Route 53: public vs private zone
    if cache_key == "route53_zone":
        zone_type = resource.get("zone_type", "public")
        zone_pricing = service.get(zone_type, service.get("public", {}))
        monthly = zone_pricing.get(region)
        if monthly is None:
            return None
        return round(monthly / 730, 6)

    # Lambda / CloudFront: usage-driven, idle is near-zero
    if cache_key in ("lambda_function", "cloudfront_distribution"):
        return 0.0

    return None


def _compute_runtime_hours(created_at: str | None) -> float | None:
    """Return how many hours a resource has been running.

    If *created_at* is ``None`` the resource is assumed to have existed
    longer than the 4-hour threshold (returns ``None``, meaning "use
    the minimum 4-hour window").  Otherwise the wall-clock difference
    from now is returned."""
    if created_at is None:
        return None  # unknown age → assume >= 4 hours
    try:
        # Try ISO-8601 parsing (handles both +00:00 and Z suffixes).
        ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    hours = (datetime.now(ts.tzinfo) - ts).total_seconds() / 3600
    return max(0, hours)


def _build_cost_impact(resource: dict[str, Any], region: str) -> dict[str, Any] | None:
    """Return a cost-impact metadata dict for *resource*, or ``None``
    when no meaningful cost estimate is available."""
    hourly = estimate_hourly_cost(resource, region)
    if hourly is None or hourly == 0.0:
        return None

    runtime_hours = _compute_runtime_hours(resource.get("created_at"))
    # Minimum 4-hour window — a resource up for 10 minutes doesn't
    # generate a misleading $0.002 alert, and one up for 3 hours
    # gets a proportionally smaller estimate than one up for 4+.
    effective_hours = max(runtime_hours or 4, min(runtime_hours or 4, 4))
    if runtime_hours is not None and runtime_hours < 4:
        effective_hours = runtime_hours

    monthly_est = round(hourly * 730, 2)
    accrued = round(hourly * effective_hours, 4)
    return {
        "hourly_usd": hourly,
        "monthly_estimate_usd": monthly_est,
        "accrued_usd": accrued,
        "runtime_hours": round(runtime_hours, 1) if runtime_hours is not None else None,
    }


def diff_unmanaged(
    live_resources: list[dict[str, Any]],
    managed_resources: list[dict[str, Any]],
    region: str,
    tf_dir: str | None = None,
    scope: str | None = None,
) -> list[dict[str, Any]]:
    """Subtract *managed_resources* from *live_resources* and classify
    the remainder.

    When *scope* is provided, unmanaged exceptions for that scope are
    loaded from Supabase to suppress known / accepted resources.

    Returns a list of findings in the same shape the existing drift
    pipeline expects (``resource_id``, ``risk_level``, ``drift_summary``,
    ``plan_output``) so downstream alert / PR nodes can consume them
    without changes.
    """
    # Index managed resources by ARN (fastest match) and by type+name (fallback).
    managed_arns: set[str] = set()
    managed_keys: set[tuple[str, str]] = set()
    for r in managed_resources:
        if r.get("arn"):
            managed_arns.add(r["arn"])
        managed_keys.add((r["type"], r["name"]))

    exceptions = _load_exceptions(scope) if scope else []

    findings: list[dict[str, Any]] = []
    for live in live_resources:
        # ---- Match against state ----
        matched = False
        if live.get("arn") and live["arn"] in managed_arns:
            matched = True
        if (live["type"], live.get("raw_name", live.get("id"))) in managed_keys:
            matched = True
        if matched:
            continue

        # ---- Match against exceptions ----
        resource_label = live.get("raw_name", live.get("id"))
        suppressed = False
        for exc in exceptions:
            if exc.get("resource_type") != live["type"]:
                continue
            pattern = exc.get("resource_id_pattern", "")
            if not pattern or pattern not in resource_label:
                continue
            # If a cost cap is set, only suppress when the estimated
            # monthly spend is below the threshold — an expensive
            # resource still alerts even if it matches the pattern.
            max_cost = exc.get("max_monthly_cost_usd")
            if max_cost is not None:
                est = _build_cost_impact(live, region)
                monthly = est["monthly_estimate_usd"] if est else 0
                if monthly >= max_cost:
                    continue  # too expensive to suppress
            suppressed = True
            break
        if suppressed:
            continue

        # ---- Classify ----
        if live.get("is_default"):
            continue  # default VPC / default SG — every account has these

        tags = live.get("tags") or {}
        cost = _build_cost_impact(live, region)

        if tags.get("ManagedBy") == "Terraform":
            # IaC-managed, but NOT by this workspace.  Could be a
            # different root module or a different team — worth a
            # warning but not an alert.
            summary = (
                f"Resource exists in AWS and is tagged ManagedBy=Terraform, "
                f"but is not tracked in this Terraform workspace's state. "
                f"It may belong to a different root module or team."
            )
            if cost:
                summary += (
                    f" Estimated cost: ${cost['monthly_estimate_usd']:.2f}/mo "
                    f"(${cost['hourly_usd']:.4f}/hr). Accrued: ${cost['accrued_usd']:.2f}."
                )
            findings.append(
                {
                    "resource_id": f"{live['type']}.{live.get('raw_name', live['id'])}",
                    "risk_level": "LOW",
                    "drift_summary": summary,
                    "plan_output": json.dumps(live, indent=2, default=str),
                    "file_path": None,
                    "changes": {},
                    "status": "unmanaged_tagged",
                    "cost_impact": cost,
                }
            )
            continue

        # Genuinely unmanaged — created outside of any IaC tool.
        summary = (
            f"Resource exists in AWS but is not tracked in Terraform state "
            f"and has no ManagedBy tag. It was likely created manually or by "
            f"another tool. Consider importing it or adding a .tf resource block."
        )
        if cost:
            summary += (
                f" Estimated cost: ${cost['monthly_estimate_usd']:.2f}/mo "
                f"(${cost['hourly_usd']:.4f}/hr). Accrued: ${cost['accrued_usd']:.2f}."
            )
        findings.append(
            {
                "resource_id": f"{live['type']}.{live.get('raw_name', live['id'])}",
                "risk_level": "MEDIUM",
                "drift_summary": summary,
                "plan_output": json.dumps(live, indent=2, default=str),
                "file_path": None,
                "changes": {},
                "status": "unmanaged",
                "cost_impact": cost,
            }
        )

    return findings


def _scan_ec2_instances(session, region: str) -> list[dict[str, Any]]:
    """Return every EC2 instance in *region*."""
    ec2 = session.client("ec2", region_name=region)
    results: list[dict[str, Any]] = []

    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]
    ):
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                results.append(
                    {
                        "type": "aws_instance",
                        "id": inst["InstanceId"],
                        "arn": f"arn:aws:ec2:{region}:{_account_id(session)}:instance/{inst['InstanceId']}",
                        "tags": tags,
                        "is_default": False,
                        "raw_name": next(
                            (t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), ""
                        ),
                        "spec": inst.get("InstanceType", ""),
                        "state": inst.get("State", {}).get("Name", ""),
                        "created_at": _resolve_created_at(tags, inst.get("LaunchTime")),
                    }
                )
    return results


def _scan_vpcs(session, region: str) -> list[dict[str, Any]]:
    """Return every non-default VPC in *region*."""
    ec2 = session.client("ec2", region_name=region)
    results: list[dict[str, Any]] = []

    paginator = ec2.get_paginator("describe_vpcs")
    for page in paginator.paginate():
        for vpc in page["Vpcs"]:
            tags = {t["Key"]: t["Value"] for t in vpc.get("Tags", [])}
            results.append(
                {
                    "type": "aws_vpc",
                    "id": vpc["VpcId"],
                    "arn": f"arn:aws:ec2:{region}:{_account_id(session)}:vpc/{vpc['VpcId']}",
                    "tags": tags,
                    "is_default": vpc.get("IsDefault", False),
                    "raw_name": next(
                        (t["Value"] for t in vpc.get("Tags", []) if t["Key"] == "Name"), ""
                    ),
                    "created_at": _resolve_created_at(tags),
                }
            )
    return results


def _scan_subnets(session, region: str) -> list[dict[str, Any]]:
    """Return every non-default subnet in *region*."""
    ec2 = session.client("ec2", region_name=region)
    results: list[dict[str, Any]] = []

    paginator = ec2.get_paginator("describe_subnets")
    for page in paginator.paginate():
        for sn in page["Subnets"]:
            tags = {t["Key"]: t["Value"] for t in sn.get("Tags", [])}
            results.append(
                {
                    "type": "aws_subnet",
                    "id": sn["SubnetId"],
                    "arn": f"arn:aws:ec2:{region}:{_account_id(session)}:subnet/{sn['SubnetId']}",
                    "tags": tags,
                    "is_default": sn.get("DefaultForAz", False),
                    "raw_name": next(
                        (t["Value"] for t in sn.get("Tags", []) if t["Key"] == "Name"), ""
                    ),
                    "created_at": _resolve_created_at(tags),
                }
            )
    return results


def _scan_route_tables(session, region: str) -> list[dict[str, Any]]:
    """Return every route table in *region*."""
    ec2 = session.client("ec2", region_name=region)
    results: list[dict[str, Any]] = []

    paginator = ec2.get_paginator("describe_route_tables")
    for page in paginator.paginate():
        for rt in page["RouteTables"]:
            is_main = any(a.get("Main", False) for a in rt.get("Associations", []))
            tags = {t["Key"]: t["Value"] for t in rt.get("Tags", [])}
            results.append(
                {
                    "type": "aws_route_table",
                    "id": rt["RouteTableId"],
                    "arn": f"arn:aws:ec2:{region}:{_account_id(session)}:route-table/{rt['RouteTableId']}",
                    "tags": tags,
                    "is_default": is_main,
                    "raw_name": next(
                        (t["Value"] for t in rt.get("Tags", []) if t["Key"] == "Name"), ""
                    ),
                    "created_at": _resolve_created_at(tags),
                }
            )
    return results


def _scan_internet_gateways(session, region: str) -> list[dict[str, Any]]:
    """Return every internet gateway in *region*."""
    ec2 = session.client("ec2", region_name=region)
    results: list[dict[str, Any]] = []

    paginator = ec2.get_paginator("describe_internet_gateways")
    for page in paginator.paginate():
        for igw in page["InternetGateways"]:
            tags = {t["Key"]: t["Value"] for t in igw.get("Tags", [])}
            results.append(
                {
                    "type": "aws_internet_gateway",
                    "id": igw["InternetGatewayId"],
                    "arn": f"arn:aws:ec2:{region}:{_account_id(session)}:internet-gateway/{igw['InternetGatewayId']}",
                    "tags": tags,
                    "is_default": False,
                    "raw_name": next(
                        (t["Value"] for t in igw.get("Tags", []) if t["Key"] == "Name"), ""
                    ),
                    "created_at": _resolve_created_at(tags),
                }
            )
    return results


def _scan_nat_gateways(session, region: str) -> list[dict[str, Any]]:
    """Return every NAT gateway in *region*."""
    ec2 = session.client("ec2", region_name=region)
    results: list[dict[str, Any]] = []

    paginator = ec2.get_paginator("describe_nat_gateways")
    for page in paginator.paginate():
        for nat in page["NatGateways"]:
            if nat.get("State") in ("deleted", "deleting"):
                continue  # already gone or going — skip
            tags = {t["Key"]: t["Value"] for t in nat.get("Tags", [])}
            results.append(
                {
                    "type": "aws_nat_gateway",
                    "id": nat["NatGatewayId"],
                    "arn": f"arn:aws:ec2:{region}:{_account_id(session)}:natgateway/{nat['NatGatewayId']}",
                    "tags": tags,
                    "is_default": False,
                    "raw_name": next(
                        (t["Value"] for t in nat.get("Tags", []) if t["Key"] == "Name"), ""
                    ),
                    "created_at": _resolve_created_at(tags, nat.get("CreateTime")),
                }
            )
    return results


def _scan_s3_buckets(session, region: str) -> list[dict[str, Any]]:
    """Return every S3 bucket whose location matches *region*.

    ``list_buckets`` is cross-region, so we call ``get_bucket_location``
    on each one and only keep buckets in the target region."""
    s3 = session.client("s3", region_name=region)
    results: list[dict[str, Any]] = []

    try:
        buckets = s3.list_buckets().get("Buckets", [])
    except Exception as exc:
        print(f"  ⚠ s3:ListBuckets failed — {exc}")
        return results

    for b in buckets:
        bucket_name = b["Name"]
        try:
            loc = s3.get_bucket_location(Bucket=bucket_name)
            loc_str = loc.get("LocationConstraint") or "us-east-1"
        except Exception:
            continue  # can't read location — skip (likely permissions)

        if loc_str != region:
            continue

        results.append(
            {
                "type": "aws_s3_bucket",
                "id": bucket_name,
                "arn": f"arn:aws:s3:::{bucket_name}",
                "tags": {},
                "is_default": False,
                "raw_name": bucket_name,
                "created_at": _resolve_created_at({}, b.get("CreationDate")),
            }
        )

    return results


def _scan_dynamodb_tables(session, region: str) -> list[dict[str, Any]]:
    """Return every DynamoDB table in *region*."""
    ddb = session.client("dynamodb", region_name=region)
    results: list[dict[str, Any]] = []

    paginator = ddb.get_paginator("list_tables")
    for page in paginator.paginate():
        for table_name in page.get("TableNames", []):
            try:
                desc = ddb.describe_table(TableName=table_name)
                tbl = desc["Table"]
            except Exception:
                continue

            results.append(
                {
                    "type": "aws_dynamodb_table",
                    "id": table_name,
                    "arn": tbl.get("TableArn", ""),
                    "tags": {},
                    "is_default": False,
                    "raw_name": table_name,
                    "created_at": _resolve_created_at({}, tbl.get("CreationDateTime")),
                }
            )

    return results


# ---------------------------------------------------------------------------
# Service registry — add new services here as they are implemented.
# ---------------------------------------------------------------------------

_SCANNERS = {
    "aws_security_group": _scan_ec2_security_groups,
    "aws_instance": _scan_ec2_instances,
    "aws_vpc": _scan_vpcs,
    "aws_subnet": _scan_subnets,
    "aws_route_table": _scan_route_tables,
    "aws_internet_gateway": _scan_internet_gateways,
    "aws_nat_gateway": _scan_nat_gateways,
    "aws_s3_bucket": _scan_s3_buckets,
    "aws_dynamodb_table": _scan_dynamodb_tables,
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scan_unmanaged_resources(
    session,
    region: str,
    resource_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Enumerate live AWS resources in *region*.

    Parameters
    ----------
    session:
        A pre-built boto3 Session (the caller resolves credentials).
    region:
        AWS region to scan (e.g. ``us-east-1``).
    resource_types:
        Terraform resource-type names to scan.  ``None`` means every
        service that has an enumerator implemented.

    Returns
    -------
    A flat list of discovered resources.  Each dict has at minimum
    ``type``, ``id``, ``arn``, ``tags``, and ``is_default``.
    """
    types = resource_types or list(_SCANNERS)

    all_resources: list[dict[str, Any]] = []
    for resource_type in types:
        scanner = _SCANNERS.get(resource_type)
        if scanner is None:
            print(f"  ⏭  No scanner implemented for {resource_type} — skipping.")
            continue
        try:
            found = scanner(session, region)
            all_resources.extend(found)
            if found:
                print(f"  ✓ {resource_type}: {len(found)} resource(s) found")
        except Exception as exc:
            print(f"  ✗ {resource_type}: scan failed — {exc}")

    return all_resources


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Enumerate live AWS resources for unmanaged-resource detection."
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument(
        "--profile",
        default=None,
        help="AWS named profile (default: default credential chain)",
    )
    parser.add_argument(
        "--tf-dir",
        default=None,
        help="Terraform directory whose state to compare against (enables diff)",
    )
    parser.add_argument(
        "--types",
        nargs="*",
        default=None,
        help="Terraform resource types to scan (default: all implemented)",
    )
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    live = scan_unmanaged_resources(session, args.region, args.types)
    print(f"\nLive resources found: {len(live)}")

    if args.tf_dir:
        managed = load_managed_resources(args.tf_dir)
        print(f"Managed resources in state: {len(managed)}")
        findings = diff_unmanaged(live, managed, region=args.region, tf_dir=args.tf_dir)
        print(f"Unmanaged findings: {len(findings)}")
        for f in findings:
            cost = f.get("cost_impact")
            cost_line = ""
            if cost:
                cost_line = f"  💰 ${cost['monthly_estimate_usd']:.2f}/mo (${cost['hourly_usd']:.4f}/hr, accrued ${cost['accrued_usd']:.2f})"
            print(f"  [{f['risk_level']}] {f['resource_id']}{cost_line}")
            print(f"    {f['drift_summary'][:200]}")
        if not findings:
            print("  (none — every live resource is tracked in state)")
