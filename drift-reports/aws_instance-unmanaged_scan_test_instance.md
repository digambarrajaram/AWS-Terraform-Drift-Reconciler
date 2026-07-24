# Unmanaged resource: aws_instance.unmanaged_scan_test_instance

Resource exists in AWS but is not tracked in Terraform state and has no ManagedBy tag. It was likely created manually or by another tool. Consider importing it or adding a .tf resource block.

```json
{
  "type": "aws_instance",
  "id": "i-08d88c4916b70132a",
  "arn": "arn:aws:ec2:us-east-1:605134452604:instance/i-08d88c4916b70132a",
  "tags": {
    "Name": "unmanaged_scan_test_instance"
  },
  "is_default": false,
  "raw_name": "unmanaged_scan_test_instance",
  "spec": "t2.nano",
  "state": "stopped",
  "created_at": "2026-07-24T14:24:33+00:00"
}
```

**Action:** Import this resource into Terraform or create the corresponding `.tf` resource block, then re-run the drift reconciler to track it.