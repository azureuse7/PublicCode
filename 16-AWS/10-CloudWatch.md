# Amazon CloudWatch: Monitoring and Observability

> CloudWatch is the AWS native monitoring service. It collects metrics, logs, and events from AWS resources and custom applications — enabling alerting, dashboards, log analysis, and automated responses to infrastructure health changes.

---

## What CloudWatch Does

```
AWS Resources (EC2, RDS, Lambda, etc.)
         │
         ▼
   CloudWatch ──► Metrics ──► Alarms ──► SNS / Auto Scaling / Lambda
         │
         ├──► Logs ──► Log Insights (SQL-like queries)
         │
         └──► Events/EventBridge ──► Trigger Lambda, Step Functions, etc.
```

---

## Core Features

### Metrics

- Automatically collected from AWS services (EC2 CPU, RDS connections, Lambda duration, etc.)
- Default resolution: **1-minute** standard; **1-second** high-resolution (custom metrics)
- Custom metrics: publish your own application metrics via SDK or `put-metric-data`

### CloudWatch Logs

- Centralised log storage from EC2, Lambda, ECS, EKS, CloudTrail, VPC Flow Logs, and more
- **Log Groups** → **Log Streams** → individual log events
- **Log Insights**: run SQL-like queries against log data in real time

### Alarms

- Monitor any metric and trigger actions when a threshold is crossed
- States: `OK`, `ALARM`, `INSUFFICIENT_DATA`
- Actions: send SNS notification, trigger Auto Scaling policy, invoke Lambda

### Dashboards

- Customisable visualisations combining metrics and logs across services and accounts
- Shareable with teams; supports cross-account/cross-region views

### CloudWatch Events / EventBridge

- Near-real-time stream of AWS resource state changes
- Rules match events and route them to targets (Lambda, SQS, Step Functions, etc.)

### Anomaly Detection

- Machine learning-based — automatically learns the normal range of a metric
- Triggers alarms when metrics deviate from the expected band

### ServiceLens

- End-to-end observability integrating CloudWatch, X-Ray traces, and logs
- Visualises service maps, latency, error rates, and dependencies

---

## Common Use Cases

| Use Case | How CloudWatch Helps |
|---|---|
| **Infrastructure monitoring** | EC2 CPU, memory (custom), disk, network metrics |
| **Application performance (APM)** | Custom metrics + X-Ray traces via ServiceLens |
| **Log analysis** | Log Insights queries on Lambda/ECS/application logs |
| **Automated scaling** | Alarms trigger Auto Scaling policies |
| **Security monitoring** | VPC Flow Logs, CloudTrail logs, WAF logs |
| **Cost alerting** | Billing alarms notify when spend exceeds threshold |

---

## Example: CPU Alarm on an EC2 Instance

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name HighCPUUtilization \
  --metric-name CPUUtilization \
  --namespace AWS/EC2 \
  --statistic Average \
  --period 300 \
  --threshold 80 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --dimensions Name=InstanceId,Value=i-1234567890abcdef0 \
  --evaluation-periods 2 \
  --alarm-actions arn:aws:sns:us-east-1:123456789012:ops-alerts
```

| Parameter | Description |
|---|---|
| `--period` | Evaluation window in seconds (300 = 5 min) |
| `--threshold` | Value that triggers the alarm (80 = 80% CPU) |
| `--evaluation-periods` | Number of consecutive periods before alarm fires |
| `--alarm-actions` | SNS topic ARN to notify when alarm state is reached |

---

## Example: Log Insights Query

Find Lambda errors in the last hour:

```sql
fields @timestamp, @message
| filter @message like /ERROR/
| sort @timestamp desc
| limit 50
```

Find slow API requests (>500ms):

```sql
fields @timestamp, @requestId, @duration
| filter @duration > 500
| sort @duration desc
| limit 20
```

---

## Example: Custom Metric (Python)

```python
import boto3

cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')

cloudwatch.put_metric_data(
    Namespace='MyApp',
    MetricData=[{
        'MetricName': 'OrdersProcessed',
        'Value': 42,
        'Unit': 'Count',
        'Dimensions': [{'Name': 'Environment', 'Value': 'production'}]
    }]
)
```

---

## CloudWatch vs X-Ray vs CloudTrail

| Tool | Tracks | Use for |
|---|---|---|
| **CloudWatch** | Metrics, logs, alarms | Infrastructure health, alerting, dashboards |
| **X-Ray** | Distributed traces | Request latency, service dependencies, bottlenecks |
| **CloudTrail** | API calls (who did what) | Security auditing, compliance, change history |

---

## Summary

CloudWatch is the central observability hub for AWS. Pair it with CloudTrail (for API audit) and X-Ray (for distributed tracing) to achieve full-stack visibility. Start by enabling alarms on core metrics (CPU, memory, error rates) and setting up a Log Insights dashboard for your most critical applications.
