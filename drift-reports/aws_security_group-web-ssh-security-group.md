# Unmanaged resource: aws_security_group.web-ssh-security-group

Resource exists in AWS but is not tracked in Terraform state and has no ManagedBy tag. It was likely created manually or by another tool. Consider importing it or adding a .tf resource block.

```json
{
  "type": "aws_security_group",
  "id": "sg-088d9db80e16cf8eb",
  "arn": "arn:aws:ec2:us-east-1:605134452604:security-group/sg-088d9db80e16cf8eb",
  "tags": {
    "Name": "drift-web-ssh-sg"
  },
  "is_default": false,
  "raw_name": "web-ssh-security-group",
  "created_at": null
}
```

**Action:** Import this resource into Terraform or create the corresponding `.tf` resource block, then re-run the drift reconciler to track it.