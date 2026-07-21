# Unmanaged resource: aws_instance.WebServer

Resource exists in AWS but is not tracked in Terraform state and has no ManagedBy tag. It was likely created manually or by another tool. Consider importing it or adding a .tf resource block.

```json
{
  "type": "aws_instance",
  "id": "i-09101adf27b3ee314",
  "arn": "arn:aws:ec2:us-east-1:605134452604:instance/i-09101adf27b3ee314",
  "tags": {
    "Name": "WebServer"
  },
  "is_default": false,
  "raw_name": "WebServer",
  "spec": "t2.nano",
  "state": "running",
  "created_at": "2026-07-21T12:47:49+00:00"
}
```

**Action:** Import this resource into Terraform or create the corresponding `.tf` resource block, then re-run the drift reconciler to track it.