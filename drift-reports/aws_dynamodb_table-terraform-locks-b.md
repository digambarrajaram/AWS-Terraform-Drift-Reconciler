# Unmanaged resource: aws_dynamodb_table.terraform-locks-b

Resource exists in AWS but is not tracked in Terraform state and has no ManagedBy tag. It was likely created manually or by another tool. Consider importing it or adding a .tf resource block.

```json
{
  "type": "aws_dynamodb_table",
  "id": "terraform-locks-b",
  "arn": "arn:aws:dynamodb:us-west-2:605134452604:table/terraform-locks-b",
  "tags": {},
  "is_default": false,
  "raw_name": "terraform-locks-b"
}
```

**Action:** Import this resource into Terraform or create the corresponding `.tf` resource block, then re-run the drift reconciler to track it.