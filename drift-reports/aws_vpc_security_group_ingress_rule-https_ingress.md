# Drift report: aws_vpc_security_group_ingress_rule.https_ingress

- `arn`: before `arn:aws:ec2:us-east-1:605134452604:security-group-rule/sgr-005ab0bd5db486f36` → after `None`
- `cidr_ipv4`: before `0.0.0.0/0` → after `None`
- `description`: before `HTTPS from internet` → after `None`
- `from_port`: before `443` → after `None`
- `id`: before `sgr-005ab0bd5db486f36` → after `None`
- `ip_protocol`: before `tcp` → after `None`
- `region`: before `us-east-1` → after `None`
- `security_group_id`: before `sg-0fc806f0fa58ffe79` → after `None`
- `security_group_rule_id`: before `sgr-005ab0bd5db486f36` → after `None`
- `tags_all`: before `{}` → after `None`
- `to_port`: before `443` → after `None`

```
{
  "arn": {
    "before": "arn:aws:ec2:us-east-1:605134452604:security-group-rule/sgr-005ab0bd5db486f36",
    "after": null
  },
  "cidr_ipv4": {
    "before": "0.0.0.0/0",
    "after": null
  },
  "description": {
    "before": "HTTPS from internet",
    "after": null
  },
  "from_port": {
    "before": 443,
    "after": null
  },
  "id": {
    "before": "sgr-005ab0bd5db486f36",
    "after": null
  },
  "ip_protocol": {
    "before": "tcp",
    "after": null
  },
  "region": {
    "before": "us-east-1",
    "after": null
  },
  "security_group_id": {
    "before": "sg-0fc806f0fa58ffe79",
    "after": null
  },
  "security_group_rule_id": {
    "before": "sgr-005ab0bd5db486f36",
    "after": null
  },
  "tags_all": {
    "before": {},
    "after": null
  },
  "to_port": {
    "before": 443,
    "after": null
  }
}
```

Merging is a no-op on code — run `terraform apply` to revert AWS.