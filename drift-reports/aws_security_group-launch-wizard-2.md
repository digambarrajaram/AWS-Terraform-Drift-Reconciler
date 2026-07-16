# Unmanaged resource: aws_security_group.launch-wizard-2

Resource exists in AWS but is not tracked in Terraform state and has no ManagedBy tag. It was likely created manually or by another tool. Consider importing it or adding a .tf resource block.

```json
{
  "type": "aws_security_group",
  "id": "sg-03c4d3a12afd11dd3",
  "arn": "arn:aws:ec2:us-east-1:605134452604:security-group/sg-03c4d3a12afd11dd3",
  "tags": {},
  "is_default": false,
  "raw_name": "launch-wizard-2"
}
```

**Action:** Import this resource into Terraform or create the corresponding `.tf` resource block, then re-run the drift reconciler to track it.