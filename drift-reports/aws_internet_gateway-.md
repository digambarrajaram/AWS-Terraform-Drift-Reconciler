# Unmanaged resource: aws_internet_gateway.

Resource exists in AWS but is not tracked in Terraform state and has no ManagedBy tag. It was likely created manually or by another tool. Consider importing it or adding a .tf resource block.

```json
{
  "type": "aws_internet_gateway",
  "id": "igw-080516b0188965f19",
  "arn": "arn:aws:ec2:us-east-1:605134452604:internet-gateway/igw-080516b0188965f19",
  "tags": {},
  "is_default": false,
  "raw_name": "",
  "created_at": null
}
```

**Action:** Import this resource into Terraform or create the corresponding `.tf` resource block, then re-run the drift reconciler to track it.