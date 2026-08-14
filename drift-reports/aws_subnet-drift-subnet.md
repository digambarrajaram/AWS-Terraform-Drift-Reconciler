# Unmanaged resource: aws_subnet.drift-subnet

Resource exists in AWS but is not tracked in Terraform state and has no ManagedBy tag. It was likely created manually or by another tool. Consider importing it or adding a .tf resource block.

```json
{
  "type": "aws_subnet",
  "id": "subnet-01f1157759f6b711f",
  "arn": "arn:aws:ec2:us-east-1:285629514281:subnet/subnet-01f1157759f6b711f",
  "tags": {
    "Name": "drift-subnet"
  },
  "is_default": false,
  "raw_name": "drift-subnet",
  "created_at": null
}
```

**Action:** Import this resource into Terraform or create the corresponding `.tf` resource block, then re-run the drift reconciler to track it.