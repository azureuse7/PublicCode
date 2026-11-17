# AWS WAF: Web Application Firewall

> AWS WAF protects web applications and APIs from common exploits (SQL injection, XSS, bot traffic) at Layer 7. You define Web ACLs with rules that allow, block, or count HTTP/HTTPS requests — deployable in front of CloudFront, ALB, API Gateway, or AppSync.

---

## How AWS WAF Works

```
User Request (HTTP/HTTPS)
        │
        ▼
  AWS WAF Web ACL
        │
        ├── Rule 1 (Managed: AWSManagedRulesCommonRuleSet)
        │       → BLOCK if SQL injection detected
        │
        ├── Rule 2 (Custom: Rate limit)
        │       → BLOCK if > 1000 req/5min from same IP
        │
        └── Default Action: ALLOW
        │
        ▼
  Your Application (CloudFront / ALB / API Gateway)
```

---

## Key Concepts

| Term | Description |
|---|---|
| **Web ACL** | The top-level WAF resource; attached to a CloudFront, ALB, or API Gateway |
| **Rule** | A condition + action (Allow, Block, Count) |
| **Rule Group** | A reusable collection of rules |
| **Managed Rule Group** | Pre-built rules from AWS or marketplace vendors (OWASP Top 10, bot control, etc.) |
| **Rate-Based Rule** | Blocks IPs that exceed a request rate threshold |
| **Statement** | The condition that a rule evaluates (IP, header, body, URI, geo, etc.) |
| **Scope** | `REGIONAL` (ALB, API GW) or `CLOUDFRONT` (must be in us-east-1) |

---

## Common AWS Managed Rule Groups

| Rule Group | Protects Against |
|---|---|
| `AWSManagedRulesCommonRuleSet` | OWASP Top 10: SQL injection, XSS, LFI, RFI |
| `AWSManagedRulesKnownBadInputsRuleSet` | Log4Shell, SSRF, Spring4Shell |
| `AWSManagedRulesSQLiRuleSet` | SQL injection |
| `AWSManagedRulesLinuxRuleSet` | Linux-specific attack patterns |
| `AWSManagedRulesBotControlRuleSet` | Bot traffic, scrapers, credential stuffing |
| `AWSManagedRulesAmazonIpReputationList` | Known malicious IPs tracked by Amazon |

---

## Use Cases

| Use Case | WAF Feature |
|---|---|
| Block OWASP Top 10 attacks | AWS Managed Common Rule Set |
| Rate limit per IP | Rate-Based Rule |
| Block specific countries | Geo match statement |
| Allow only known IPs | IP set allow list |
| Block specific User-Agents | String match on `User-Agent` header |
| Bot mitigation | Bot Control Managed Rule Group |
| Prevent data exfiltration | Custom rules inspecting response bodies |

---

## Setting Up WAF with an ALB

### Using the AWS Management Console

1. Open **WAF & Shield** → **Web ACLs** → **Create web ACL**
2. Set **Scope** to **Regional** and select the region
3. Add **Rules**:
   - Click **Add managed rule groups** → enable `AWSManagedRulesCommonRuleSet`
   - Add a custom rate-based rule (e.g., block IPs > 2000 req/5min)
4. Set **Default action**: Allow (rules decide what to block)
5. **Associate** with your ALB or API Gateway
6. Review and create

### Using the AWS CLI

**Step 1: Create the Web ACL**

```bash
aws wafv2 create-web-acl \
  --name my-web-acl \
  --scope REGIONAL \
  --region us-east-1 \
  --default-action Allow={} \
  --description "Application WAF" \
  --rules '[
    {
      "Name": "CommonRuleSet",
      "Priority": 1,
      "Statement": {
        "ManagedRuleGroupStatement": {
          "VendorName": "AWS",
          "Name": "AWSManagedRulesCommonRuleSet"
        }
      },
      "OverrideAction": { "None": {} },
      "VisibilityConfig": {
        "SampledRequestsEnabled": true,
        "CloudWatchMetricsEnabled": true,
        "MetricName": "CommonRuleSet"
      }
    },
    {
      "Name": "RateLimitRule",
      "Priority": 2,
      "Statement": {
        "RateBasedStatement": {
          "Limit": 2000,
          "AggregateKeyType": "IP"
        }
      },
      "Action": { "Block": {} },
      "VisibilityConfig": {
        "SampledRequestsEnabled": true,
        "CloudWatchMetricsEnabled": true,
        "MetricName": "RateLimitRule"
      }
    }
  ]' \
  --visibility-config \
    SampledRequestsEnabled=true,CloudWatchMetricsEnabled=true,MetricName=my-web-acl
```

**Step 2: Associate with an ALB**

```bash
# Get the ALB ARN
alb_arn=$(aws elbv2 describe-load-balancers \
  --names my-alb \
  --query 'LoadBalancers[0].LoadBalancerArn' \
  --output text)

# Get the Web ACL ARN
web_acl_arn=$(aws wafv2 list-web-acls \
  --scope REGIONAL \
  --region us-east-1 \
  --query "WebACLs[?Name=='my-web-acl'].ARN" \
  --output text)

# Associate
aws wafv2 associate-web-acl \
  --web-acl-arn $web_acl_arn \
  --resource-arn $alb_arn \
  --region us-east-1
```

---

## Monitoring and Logging

```bash
# Enable WAF logging to a Kinesis Firehose or S3 bucket
aws wafv2 put-logging-configuration \
  --logging-configuration '{
    "ResourceArn": "<WEB_ACL_ARN>",
    "LogDestinationConfigs": ["arn:aws:firehose:us-east-1:123456789012:deliverystream/aws-waf-logs-my-stream"]
  }' \
  --region us-east-1

# View sampled requests (last 3 hours)
aws wafv2 get-sampled-requests \
  --web-acl-arn <WEB_ACL_ARN> \
  --rule-metric-name CommonRuleSet \
  --scope REGIONAL \
  --time-window StartTime=2024-01-01T00:00:00Z,EndTime=2024-01-01T03:00:00Z \
  --max-items 100 \
  --region us-east-1
```

---

## WAF Deployment Architecture

| Protected Resource | Scope | Region |
|---|---|---|
| **CloudFront distribution** | CLOUDFRONT | Must be created in `us-east-1` |
| **Application Load Balancer** | REGIONAL | Same region as ALB |
| **API Gateway (REST API)** | REGIONAL | Same region as API |
| **AppSync GraphQL API** | REGIONAL | Same region as AppSync |
| **Cognito User Pool** | REGIONAL | Same region as pool |

---

## Summary

AWS WAF is your first line of defence against application-layer attacks. Start with AWS Managed Rule Groups (especially `AWSManagedRulesCommonRuleSet` and the IP Reputation List) to get immediate protection against the OWASP Top 10 and known bad actors. Add rate-based rules to prevent brute force and DDoS, and use `Count` mode initially to observe traffic before switching to `Block`.
