# Drift report: aws_instance.drift_web_server

- `tags`: before `{'Name': 'WebServer'}` → after `{'Name': 'WebServer12344'}`
- `tags_all`: before `{'Name': 'WebServer'}` → after `{'Name': 'WebServer12344'}`

```
{
  "tags": {
    "before": {
      "Name": "WebServer"
    },
    "after": {
      "Name": "WebServer12344"
    }
  },
  "tags_all": {
    "before": {
      "Name": "WebServer"
    },
    "after": {
      "Name": "WebServer12344"
    }
  }
}
```

Merging is a no-op on code — run `terraform apply` to revert AWS.