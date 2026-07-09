"""
Export live EC2 instances (us-east-1) to a Terraform-plan-style JSON file,
with attribute names mapped to match Terraform's `aws_instance` resource
schema — so this output can be diffed directly against `terraform show -json`
for drift detection.

Requires:
    pip install boto3

Required IAM permission: ec2:DescribeInstances
"""

import boto3
import json
from datetime import datetime

REGION = "us-east-1"
OUTPUT_FILE = "ec2_terraform_format.json"


def map_to_terraform_attributes(inst):
    """
    Convert a raw boto3 EC2 instance dict into keys/shapes matching
    Terraform's aws_instance resource schema.
    """
    root_device_name = inst.get("RootDeviceName")
    root_block_device = None
    ebs_block_devices = []

    for bdm in inst.get("BlockDeviceMappings", []):
        ebs = bdm.get("Ebs", {})
        device_entry = {
            "device_name": bdm.get("DeviceName"),
            "volume_id": ebs.get("VolumeId"),
            "delete_on_termination": ebs.get("DeleteOnTermination"),
        }
        if bdm.get("DeviceName") == root_device_name:
            root_block_device = device_entry
        else:
            ebs_block_devices.append(device_entry)

    # Terraform stores tags as a map {key: value}, not a list of {Key, Value}
    tags_map = {t["Key"]: t["Value"] for t in inst.get("Tags", []) if "Key" in t}

    iam_profile_arn = (
        inst.get("IamInstanceProfile", {}).get("Arn")
        if inst.get("IamInstanceProfile") else None
    )
    # Terraform's iam_instance_profile attribute stores the profile NAME, not ARN
    iam_profile_name = iam_profile_arn.split("/")[-1] if iam_profile_arn else None

    return {
        "id": inst.get("InstanceId"),
        "ami": inst.get("ImageId"),
        "instance_type": inst.get("InstanceType"),
        "availability_zone": inst.get("Placement", {}).get("AvailabilityZone"),
        "key_name": inst.get("KeyName"),
        "subnet_id": inst.get("SubnetId"),
        "vpc_security_group_ids": [
            sg.get("GroupId") for sg in inst.get("SecurityGroups", [])
        ],
        "private_ip": inst.get("PrivateIpAddress"),
        "public_ip": inst.get("PublicIpAddress"),
        "iam_instance_profile": iam_profile_name,
        "monitoring": inst.get("Monitoring", {}).get("State") == "enabled",
        "ebs_optimized": inst.get("EbsOptimized", False),
        "root_block_device": root_block_device,
        "ebs_block_device": ebs_block_devices,
        "tags": tags_map,
        "tags_all": tags_map,
        # Extra fields not part of Terraform schema, kept for visibility/debugging
        "_launch_time": inst.get("LaunchTime"),
        "_state": inst.get("State", {}).get("Name"),
    }


def get_ec2_instances():
    client = boto3.client("ec2", region_name=REGION)
    paginator = client.get_paginator("describe_instances")

    instances = []
    for page in paginator.paginate():
        for reservation in page.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                instances.append(map_to_terraform_attributes(inst))
    return instances


def to_tf_resource(values):
    """Wrap instance values into a Terraform-plan-style resource block."""
    instance_id = values.get("id", "unknown")
    safe_name = "".join(c if c.isalnum() or c == "_" else "_" for c in instance_id)

    return {
        "address": f"aws_instance.{safe_name}",
        "mode": "managed",
        "type": "aws_instance",
        "name": safe_name,
        "provider_name": "registry.terraform.io/hashicorp/aws",
        "values": values,
    }


def export_ec2_instances():
    instances = get_ec2_instances()
    resources = [to_tf_resource(v) for v in instances]

    output = {
        "format_version": "1.0",
        "terraform_version": "N/A (live AWS inventory, not a Terraform run)",
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "region": REGION,
        "values": {
            "root_module": {
                "resources": resources
            }
        }
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"Export complete -> {OUTPUT_FILE}")
    print(f"Total EC2 instances: {len(resources)}")


if __name__ == "__main__":
    export_ec2_instances()