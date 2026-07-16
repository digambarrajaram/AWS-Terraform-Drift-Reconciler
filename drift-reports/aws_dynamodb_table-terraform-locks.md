# Unmanaged resource: aws_dynamodb_table.terraform-locks

Resource exists in AWS but is not tracked in Terraform state and has no ManagedBy tag. It was likely created manually or by another tool. Consider importing it or adding a .tf resource block.

```json
{
  "type": "aws_dynamodb_table",
  "id": "terraform-locks",
  "arn": "arn:aws:dynamodb:us-east-1:605134452604:table/terraform-locks",
  "tags": {},
  "is_default": false,
  "raw_name": "terraform-locks",
  "created_at": "2026-07-16T09:07:31.146000+05:30"
}
```

**Action:** Import this resource into Terraform or create the corresponding `.tf` resource block, then re-run the drift reconciler to track it.