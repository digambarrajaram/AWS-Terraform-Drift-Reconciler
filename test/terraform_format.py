"""
Export AWS resources for specific services to a JSON file.

Covers:
    - Compute        (EC2 instances)
    - Storage        (EBS volumes, S3 buckets)
    - EKS            (clusters)
    - Route 53       (hosted zones - global service)
    - Bedrock        (available foundation models + custom/provisioned models)
    - Lambda         (functions)
    - API Gateway    (REST APIs + HTTP APIs)
    - CloudFront     (distributions - global service)
    - DynamoDB       (tables)
    - Load Balancers (ALB/NLB via ELBv2, plus Classic ELB)
    - Auto Scaling Groups
    - VPC            (VPCs, subnets)
    - ECS            (clusters, services, task definitions)
    - SNS            (topics)
    - SQS            (queues)
    - CloudWatch     (alarms)
    - IAM            (users, roles)

Requires:
    pip install boto3

AWS credentials via ~/.aws/credentials, env vars, or IAM role.

Required IAM permissions (read-only), roughly:
    ec2:Describe*, s3:ListAllMyBuckets, eks:ListClusters, eks:DescribeCluster,
    route53:ListHostedZones, bedrock:ListFoundationModels,
    bedrock:ListCustomModels, bedrock:ListProvisionedModelThroughputs,
    lambda:ListFunctions, apigateway:GET, cloudfront:ListDistributions,
    dynamodb:ListTables, dynamodb:DescribeTable, elasticloadbalancing:Describe*,
    autoscaling:DescribeAutoScalingGroups, ecs:ListClusters, ecs:DescribeClusters,
    ecs:ListServices, ecs:DescribeServices, ecs:ListTaskDefinitions,
    sns:ListTopics, sqs:ListQueues, cloudwatch:DescribeAlarms,
    iam:ListUsers, iam:ListRoles
"""

import boto3
import json
from datetime import datetime

REGION = "us-east-1"
OUTPUT_FILE = "aws_resources_selected_services.json"


def get_ec2_instances():
    client = boto3.client("ec2", region_name=REGION)
    paginator = client.get_paginator("describe_instances")
    instances = []
    for page in paginator.paginate():
        for reservation in page.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                instances.append({
                    "InstanceId": inst.get("InstanceId"),
                    "InstanceType": inst.get("InstanceType"),
                    "State": inst.get("State", {}).get("Name"),
                    "LaunchTime": inst.get("LaunchTime"),
                    "Tags": inst.get("Tags", []),
                })
    return instances


def get_ebs_volumes():
    client = boto3.client("ec2", region_name=REGION)
    paginator = client.get_paginator("describe_volumes")
    volumes = []
    for page in paginator.paginate():
        for vol in page.get("Volumes", []):
            volumes.append({
                "VolumeId": vol.get("VolumeId"),
                "Size": vol.get("Size"),
                "VolumeType": vol.get("VolumeType"),
                "State": vol.get("State"),
                "Tags": vol.get("Tags", []),
            })
    return volumes


def get_s3_buckets():
    client = boto3.client("s3", region_name=REGION)
    response = client.list_buckets()
    return [{
        "Name": b.get("Name"),
        "CreationDate": b.get("CreationDate"),
    } for b in response.get("Buckets", [])]


def get_eks_clusters():
    client = boto3.client("eks", region_name=REGION)
    paginator = client.get_paginator("list_clusters")
    clusters = []
    for page in paginator.paginate():
        for name in page.get("clusters", []):
            detail = client.describe_cluster(name=name)["cluster"]
            clusters.append({
                "Name": detail.get("name"),
                "Status": detail.get("status"),
                "Version": detail.get("version"),
                "Endpoint": detail.get("endpoint"),
                "CreatedAt": detail.get("createdAt"),
            })
    return clusters


def get_route53_hosted_zones():
    # Route 53 is a global service, not region-scoped
    client = boto3.client("route53")
    paginator = client.get_paginator("list_hosted_zones")
    zones = []
    for page in paginator.paginate():
        for z in page.get("HostedZones", []):
            zones.append({
                "Id": z.get("Id"),
                "Name": z.get("Name"),
                "RecordCount": z.get("ResourceRecordSetCount"),
                "PrivateZone": z.get("Config", {}).get("PrivateZone"),
            })
    return zones


def get_bedrock_models():
    """Foundation models available + any custom/provisioned models in the account."""
    client = boto3.client("bedrock", region_name=REGION)
    result = {"foundation_models_available": [], "custom_models": [], "provisioned_throughputs": []}

    try:
        fm = client.list_foundation_models()
        result["foundation_models_available"] = [
            {"modelId": m.get("modelId"), "providerName": m.get("providerName")}
            for m in fm.get("modelSummaries", [])
        ]
    except Exception as e:
        result["foundation_models_available"] = f"Error: {e}"

    try:
        cm = client.list_custom_models()
        result["custom_models"] = [
            {"modelName": m.get("modelName"), "modelArn": m.get("modelArn")}
            for m in cm.get("modelSummaries", [])
        ]
    except Exception as e:
        result["custom_models"] = f"Error: {e}"

    try:
        pt = client.list_provisioned_model_throughputs()
        result["provisioned_throughputs"] = [
            {"name": p.get("provisionedModelName"), "status": p.get("status")}
            for p in pt.get("provisionedModelSummaries", [])
        ]
    except Exception as e:
        result["provisioned_throughputs"] = f"Error: {e}"

    return result


def get_lambda_functions():
    client = boto3.client("lambda", region_name=REGION)
    paginator = client.get_paginator("list_functions")
    functions = []
    for page in paginator.paginate():
        for fn in page.get("Functions", []):
            functions.append({
                "FunctionName": fn.get("FunctionName"),
                "Runtime": fn.get("Runtime"),
                "LastModified": fn.get("LastModified"),
                "MemorySize": fn.get("MemorySize"),
            })
    return functions


def get_api_gateways():
    result = {"rest_apis": [], "http_apis": []}

    # REST APIs (API Gateway v1)
    client_v1 = boto3.client("apigateway", region_name=REGION)
    paginator = client_v1.get_paginator("get_rest_apis")
    for page in paginator.paginate():
        for api in page.get("items", []):
            result["rest_apis"].append({
                "id": api.get("id"),
                "name": api.get("name"),
                "createdDate": api.get("createdDate"),
            })

    # HTTP APIs (API Gateway v2)
    client_v2 = boto3.client("apigatewayv2", region_name=REGION)
    response = client_v2.get_apis()
    for api in response.get("Items", []):
        result["http_apis"].append({
            "ApiId": api.get("ApiId"),
            "Name": api.get("Name"),
            "ProtocolType": api.get("ProtocolType"),
            "CreatedDate": api.get("CreatedDate"),
        })

    return result


def get_cloudfront_distributions():
    # CloudFront is a global service, not region-scoped
    client = boto3.client("cloudfront")
    paginator = client.get_paginator("list_distributions")
    distributions = []
    for page in paginator.paginate():
        items = page.get("DistributionList", {}).get("Items", [])
        for d in items:
            distributions.append({
                "Id": d.get("Id"),
                "DomainName": d.get("DomainName"),
                "Status": d.get("Status"),
                "Enabled": d.get("Enabled"),
            })
    return distributions


def get_dynamodb_tables():
    client = boto3.client("dynamodb", region_name=REGION)
    paginator = client.get_paginator("list_tables")
    tables = []
    for page in paginator.paginate():
        for name in page.get("TableNames", []):
            detail = client.describe_table(TableName=name)["Table"]
            tables.append({
                "TableName": detail.get("TableName"),
                "Status": detail.get("TableStatus"),
                "ItemCount": detail.get("ItemCount"),
                "SizeBytes": detail.get("TableSizeBytes"),
            })
    return tables


def get_load_balancers():
    result = {"alb_nlb": [], "classic_elb": []}

    client_v2 = boto3.client("elbv2", region_name=REGION)
    paginator_v2 = client_v2.get_paginator("describe_load_balancers")
    for page in paginator_v2.paginate():
        for lb in page.get("LoadBalancers", []):
            result["alb_nlb"].append({
                "LoadBalancerName": lb.get("LoadBalancerName"),
                "Type": lb.get("Type"),
                "Scheme": lb.get("Scheme"),
                "State": lb.get("State", {}).get("Code"),
                "DNSName": lb.get("DNSName"),
            })

    client_v1 = boto3.client("elb", region_name=REGION)
    paginator_v1 = client_v1.get_paginator("describe_load_balancers")
    for page in paginator_v1.paginate():
        for lb in page.get("LoadBalancerDescriptions", []):
            result["classic_elb"].append({
                "LoadBalancerName": lb.get("LoadBalancerName"),
                "DNSName": lb.get("DNSName"),
            })

    return result


def get_auto_scaling_groups():
    client = boto3.client("autoscaling", region_name=REGION)
    paginator = client.get_paginator("describe_auto_scaling_groups")
    groups = []
    for page in paginator.paginate():
        for asg in page.get("AutoScalingGroups", []):
            groups.append({
                "AutoScalingGroupName": asg.get("AutoScalingGroupName"),
                "MinSize": asg.get("MinSize"),
                "MaxSize": asg.get("MaxSize"),
                "DesiredCapacity": asg.get("DesiredCapacity"),
                "Instances": [i.get("InstanceId") for i in asg.get("Instances", [])],
            })
    return groups


def get_vpcs():
    client = boto3.client("ec2", region_name=REGION)
    vpcs_resp = client.describe_vpcs()
    subnets_resp = client.describe_subnets()

    return {
        "vpcs": [{
            "VpcId": v.get("VpcId"),
            "CidrBlock": v.get("CidrBlock"),
            "IsDefault": v.get("IsDefault"),
            "Tags": v.get("Tags", []),
        } for v in vpcs_resp.get("Vpcs", [])],
        "subnets": [{
            "SubnetId": s.get("SubnetId"),
            "VpcId": s.get("VpcId"),
            "CidrBlock": s.get("CidrBlock"),
            "AvailabilityZone": s.get("AvailabilityZone"),
        } for s in subnets_resp.get("Subnets", [])],
    }


def get_ecs_resources():
    client = boto3.client("ecs", region_name=REGION)
    result = {"clusters": []}

    cluster_arns = []
    paginator = client.get_paginator("list_clusters")
    for page in paginator.paginate():
        cluster_arns.extend(page.get("clusterArns", []))

    if not cluster_arns:
        return result

    clusters_detail = client.describe_clusters(clusters=cluster_arns).get("clusters", [])

    for cluster in clusters_detail:
        cluster_name = cluster.get("clusterName")
        cluster_arn = cluster.get("clusterArn")

        # Services in this cluster
        services = []
        svc_paginator = client.get_paginator("list_services")
        service_arns = []
        for page in svc_paginator.paginate(cluster=cluster_arn):
            service_arns.extend(page.get("serviceArns", []))

        if service_arns:
            # describe_services accepts max 10 at a time
            for i in range(0, len(service_arns), 10):
                batch = service_arns[i:i + 10]
                svc_detail = client.describe_services(cluster=cluster_arn, services=batch)
                for svc in svc_detail.get("services", []):
                    services.append({
                        "serviceName": svc.get("serviceName"),
                        "status": svc.get("status"),
                        "desiredCount": svc.get("desiredCount"),
                        "runningCount": svc.get("runningCount"),
                        "launchType": svc.get("launchType"),
                    })

        result["clusters"].append({
            "clusterName": cluster_name,
            "status": cluster.get("status"),
            "runningTasksCount": cluster.get("runningTasksCount"),
            "activeServicesCount": cluster.get("activeServicesCount"),
            "services": services,
        })

    # Task definition families (registered, not necessarily in use)
    task_def_families = []
    td_paginator = client.get_paginator("list_task_definition_families")
    for page in td_paginator.paginate(status="ACTIVE"):
        task_def_families.extend(page.get("families", []))
    result["task_definition_families"] = task_def_families

    return result


def get_sns_topics():
    client = boto3.client("sns", region_name=REGION)
    paginator = client.get_paginator("list_topics")
    topics = []
    for page in paginator.paginate():
        for t in page.get("Topics", []):
            topics.append({"TopicArn": t.get("TopicArn")})
    return topics


def get_sqs_queues():
    client = boto3.client("sqs", region_name=REGION)
    queues = []
    response = client.list_queues()
    for url in response.get("QueueUrls", []):
        queues.append({"QueueUrl": url})
    return queues


def get_cloudwatch_alarms():
    client = boto3.client("cloudwatch", region_name=REGION)
    paginator = client.get_paginator("describe_alarms")
    alarms = []
    for page in paginator.paginate():
        for a in page.get("MetricAlarms", []):
            alarms.append({
                "AlarmName": a.get("AlarmName"),
                "StateValue": a.get("StateValue"),
                "MetricName": a.get("MetricName"),
                "Namespace": a.get("Namespace"),
            })
    return alarms


def get_iam_resources():
    # IAM is a global service, not region-scoped
    client = boto3.client("iam")
    result = {"users": [], "roles": []}

    user_paginator = client.get_paginator("list_users")
    for page in user_paginator.paginate():
        for u in page.get("Users", []):
            result["users"].append({
                "UserName": u.get("UserName"),
                "UserId": u.get("UserId"),
                "CreateDate": u.get("CreateDate"),
            })

    role_paginator = client.get_paginator("list_roles")
    for page in role_paginator.paginate():
        for r in page.get("Roles", []):
            result["roles"].append({
                "RoleName": r.get("RoleName"),
                "RoleId": r.get("RoleId"),
                "CreateDate": r.get("CreateDate"),
            })

    return result


def to_tf_resource(resource_type, name_key, values, index=None):
    """
    Wrap a raw resource dict into a Terraform-plan-style resource block:
    { address, mode, type, name, provider_name, values }
    """
    name = str(values.get(name_key, index if index is not None else "unknown"))
    # Terraform addresses can't contain most special characters - sanitize lightly
    safe_name = "".join(c if c.isalnum() or c == "_" else "_" for c in name)

    return {
        "address": f"{resource_type}.{safe_name}",
        "mode": "managed",
        "type": resource_type,
        "name": safe_name,
        "provider_name": "registry.terraform.io/hashicorp/aws",
        "values": values,
    }


def build_resource_list():
    resources = []

    for inst in get_ec2_instances():
        resources.append(to_tf_resource("aws_instance", "InstanceId", inst))

    for vol in get_ebs_volumes():
        resources.append(to_tf_resource("aws_ebs_volume", "VolumeId", vol))

    for bucket in get_s3_buckets():
        resources.append(to_tf_resource("aws_s3_bucket", "Name", bucket))

    for cluster in get_eks_clusters():
        resources.append(to_tf_resource("aws_eks_cluster", "Name", cluster))

    for zone in get_route53_hosted_zones():
        resources.append(to_tf_resource("aws_route53_zone", "Name", zone))

    bedrock = get_bedrock_models()
    for i, m in enumerate(bedrock.get("foundation_models_available", []) or []):
        if isinstance(m, dict):
            resources.append(to_tf_resource("aws_bedrock_foundation_model", "modelId", m, i))
    for i, m in enumerate(bedrock.get("custom_models", []) or []):
        if isinstance(m, dict):
            resources.append(to_tf_resource("aws_bedrock_custom_model", "modelName", m, i))
    for i, m in enumerate(bedrock.get("provisioned_throughputs", []) or []):
        if isinstance(m, dict):
            resources.append(to_tf_resource("aws_bedrock_provisioned_model_throughput", "name", m, i))

    for fn in get_lambda_functions():
        resources.append(to_tf_resource("aws_lambda_function", "FunctionName", fn))

    api = get_api_gateways()
    for a in api.get("rest_apis", []):
        resources.append(to_tf_resource("aws_api_gateway_rest_api", "name", a))
    for a in api.get("http_apis", []):
        resources.append(to_tf_resource("aws_apigatewayv2_api", "Name", a))

    for d in get_cloudfront_distributions():
        resources.append(to_tf_resource("aws_cloudfront_distribution", "Id", d))

    for t in get_dynamodb_tables():
        resources.append(to_tf_resource("aws_dynamodb_table", "TableName", t))

    lbs = get_load_balancers()
    for lb in lbs.get("alb_nlb", []):
        resources.append(to_tf_resource("aws_lb", "LoadBalancerName", lb))
    for lb in lbs.get("classic_elb", []):
        resources.append(to_tf_resource("aws_elb", "LoadBalancerName", lb))

    for asg in get_auto_scaling_groups():
        resources.append(to_tf_resource("aws_autoscaling_group", "AutoScalingGroupName", asg))

    vpc_data = get_vpcs()
    for v in vpc_data.get("vpcs", []):
        resources.append(to_tf_resource("aws_vpc", "VpcId", v))
    for s in vpc_data.get("subnets", []):
        resources.append(to_tf_resource("aws_subnet", "SubnetId", s))

    ecs = get_ecs_resources()
    for cluster in ecs.get("clusters", []):
        # Cluster itself
        cluster_copy = {k: v for k, v in cluster.items() if k != "services"}
        resources.append(to_tf_resource("aws_ecs_cluster", "clusterName", cluster_copy))
        # Services inside it, addressed under the cluster name
        for svc in cluster.get("services", []):
            svc_with_cluster = {**svc, "clusterName": cluster.get("clusterName")}
            resources.append(to_tf_resource(
                "aws_ecs_service",
                "serviceName",
                svc_with_cluster
            ))

    for t in get_sns_topics():
        resources.append(to_tf_resource("aws_sns_topic", "TopicArn", t))

    for q in get_sqs_queues():
        resources.append(to_tf_resource("aws_sqs_queue", "QueueUrl", q))

    for a in get_cloudwatch_alarms():
        resources.append(to_tf_resource("aws_cloudwatch_metric_alarm", "AlarmName", a))

    iam = get_iam_resources()
    for u in iam.get("users", []):
        resources.append(to_tf_resource("aws_iam_user", "UserName", u))
    for r in iam.get("roles", []):
        resources.append(to_tf_resource("aws_iam_role", "RoleName", r))

    return resources


def export_resources():
    resources = build_resource_list()

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
    print(f"Total resources: {len(resources)}")


if __name__ == "__main__":
    export_resources()