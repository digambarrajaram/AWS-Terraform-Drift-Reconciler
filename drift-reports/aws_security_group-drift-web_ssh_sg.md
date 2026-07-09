# Drift report: aws_security_group.drift-web_ssh_sg

- `ingress`: before `[]` → after `[{'cidr_blocks': ['0.0.0.0/0'], 'description': '', 'from_port': 22, 'ipv6_cidr_blocks': [], 'prefix_list_ids': [], 'protocol': 'tcp', 'security_groups': [], 'self': False, 'to_port': 22}, {'cidr_blocks': ['0.0.0.0/0'], 'description': '', 'from_port': 443, 'ipv6_cidr_blocks': [], 'prefix_list_ids': [], 'protocol': 'tcp', 'security_groups': [], 'self': False, 'to_port': 443}]`

```
{
  "ingress": {
    "before": [],
    "after": [
      {
        "cidr_blocks": [
          "0.0.0.0/0"
        ],
        "description": "",
        "from_port": 22,
        "ipv6_cidr_blocks": [],
        "prefix_list_ids": [],
        "protocol": "tcp",
        "security_groups": [],
        "self": false,
        "to_port": 22
      },
      {
        "cidr_blocks": [
          "0.0.0.0/0"
        ],
        "description": "",
        "from_port": 443,
        "ipv6_cidr_blocks": [],
        "prefix_list_ids": [],
        "protocol": "tcp",
        "security_groups": [],
        "self": false,
        "to_port": 443
      }
    ]
  }
}
```