# Unmanaged resource: aws_route_table.drift-route-table

Resource exists in AWS but is not tracked in Terraform state and has no ManagedBy tag. It was likely created manually or by another tool. Consider importing it or adding a .tf resource block.

```json
{
  "type": "aws_route_table",
  "id": "rtb-03077b9ea2c3ac9a1",
  "arn": "arn:aws:ec2:us-east-1:285629514281:route-table/rtb-03077b9ea2c3ac9a1",
  "tags": {
    "Name": "drift-route-table"
  },
  "is_default": false,
  "raw_name": "drift-route-table",
  "created_at": null
}
```

**Action:** Import this resource into Terraform or create the corresponding `.tf` resource block, then re-run the drift reconciler to track it.