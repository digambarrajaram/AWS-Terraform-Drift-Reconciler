# Unmanaged resource: aws_security_group.launch-wizard-1

Resource exists in AWS but is not tracked in Terraform state and has no ManagedBy tag. It was likely created manually or by another tool. Consider importing it or adding a .tf resource block.

```json
{
  "type": "aws_security_group",
  "id": "sg-00d4c6d12e42f2403",
  "arn": "arn:aws:ec2:us-east-1:605134452604:security-group/sg-00d4c6d12e42f2403",
  "tags": {},
  "is_default": false,
  "raw_name": "launch-wizard-1",
  "created_at": null
}
```

**Action:** Import this resource into Terraform or create the corresponding `.tf` resource block, then re-run the drift reconciler to track it.