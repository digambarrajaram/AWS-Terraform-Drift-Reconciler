# Unmanaged resource: aws_instance.WebServer

Resource exists in AWS but is not tracked in Terraform state and has no ManagedBy tag. It was likely created manually or by another tool. Consider importing it or adding a .tf resource block. Estimated cost: $7.59/mo ($0.0104/hr). Accrued: $0.04.

```json
{
  "type": "aws_instance",
  "id": "i-0be3e5f02ee841bb6",
  "arn": "arn:aws:ec2:us-east-1:285629514281:instance/i-0be3e5f02ee841bb6",
  "tags": {
    "Name": "WebServer"
  },
  "is_default": false,
  "raw_name": "WebServer",
  "spec": "t3.micro",
  "state": "running",
  "created_at": "2026-08-14T08:10:22+00:00"
}
```

**Action:** Import this resource into Terraform or create the corresponding `.tf` resource block, then re-run the drift reconciler to track it.

### Cost Estimate

- Hourly rate: $0.0104
- Estimated monthly: **$7.59**
- Accrued since creation: $0.04
- Running for: 3.6 hours
