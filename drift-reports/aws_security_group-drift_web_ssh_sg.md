# Drift report: aws_security_group.drift_web_ssh_sg

- `ingress`: before `[{'cidr_blocks': ['0.0.0.0/0'], 'description': 'HTTPS from internet', 'from_port': 443, 'ipv6_cidr_blocks': [], 'prefix_list_ids': [], 'protocol': 'tcp', 'security_groups': [], 'self': False, 'to_port': 443}, {'cidr_blocks': ['203.0.113.10/32'], 'description': 'SSH from admin IP only', 'from_port': 22, 'ipv6_cidr_blocks': [], 'prefix_list_ids': [], 'protocol': 'tcp', 'security_groups': [], 'self': False, 'to_port': 22}]` → after `[{'cidr_blocks': ['203.0.113.10/32'], 'description': 'SSH from admin IP only', 'from_port': 2222, 'ipv6_cidr_blocks': [], 'prefix_list_ids': [], 'protocol': 'tcp', 'security_groups': [], 'self': False, 'to_port': 2222}]`

```
{
  "ingress": {
    "before": [
      {
        "cidr_blocks": [
          "0.0.0.0/0"
        ],
        "description": "HTTPS from internet",
        "from_port": 443,
        "ipv6_cidr_blocks": [],
        "prefix_list_ids": [],
        "protocol": "tcp",
        "security_groups": [],
        "self": false,
        "to_port": 443
      },
      {
        "cidr_blocks": [
          "203.0.113.10/32"
        ],
        "description": "SSH from admin IP only",
        "from_port": 22,
        "ipv6_cidr_blocks": [],
        "prefix_list_ids": [],
        "protocol": "tcp",
        "security_groups": [],
        "self": false,
        "to_port": 22
      }
    ],
    "after": [
      {
        "cidr_blocks": [
          "203.0.113.10/32"
        ],
        "description": "SSH from admin IP only",
        "from_port": 2222,
        "ipv6_cidr_blocks": [],
        "prefix_list_ids": [],
        "protocol": "tcp",
        "security_groups": [],
        "self": false,
        "to_port": 2222
      }
    ]
  }
}
```

Merging is a no-op on code — run `terraform apply` to revert AWS.