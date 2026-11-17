# AWS Config: Configuration Compliance and Audit

> AWS Config continuously records the configuration state of your AWS resources and evaluates them against **rules** you define. It answers: *"What did my infrastructure look like at any point in time, and is it compliant with my policies?"*

---

## What AWS Config Does

1. **Records** resource configurations when they change
2. **Stores** a full history of every configuration change in S3
3. **Evaluates** resources against Config Rules (AWS-managed or custom Lambda)
4. **Notifies** via SNS when resources become non-compliant
5. **Provides** a visual timeline of changes for any resource

---

## Key Concepts

| Term | Description |
|------|-------------|
| **Configuration Item** | A snapshot of a resource's configuration at a point in time |
| **Config Rule** | A compliance check — evaluates whether a resource meets a condition |
| **Conformance Pack** | A collection of Config rules deployed together (e.g., CIS benchmark) |
| **Remediation** | Automatic or manual action to fix a non-compliant resource |
| **Aggregator** | View Config data across multiple accounts and regions |

---

## Common AWS-Managed Config Rules

| Rule | What it checks |
|------|---------------|
| `s3-bucket-public-read-prohibited` | No S3 buckets have public read access |
| `ec2-instance-no-public-ip` | EC2 instances do not have public IPs |
| `restricted-ssh` | Security groups do not allow unrestricted SSH (port 22) |
| `iam-root-access-key-check` | Root account has no active access keys |
| `mfa-enabled-for-iam-console-access` | IAM users with console access have MFA enabled |
| `rds-instance-public-access-check` | RDS instances are not publicly accessible |
| `encrypted-volumes` | EBS volumes are encrypted |

---

## Setup

```bash
# Enable AWS Config in a region
aws configservice put-configuration-recorder \
  --configuration-recorder name=default,roleARN=arn:aws:iam::123456789:role/ConfigRole

# Set the delivery channel (where to store config history)
aws configservice put-delivery-channel \
  --delivery-channel name=default,s3BucketName=my-config-bucket

# Start recording
aws configservice start-configuration-recorder --configuration-recorder-name default
```

---

## Querying with Advanced Query (SQL-like)

```sql
-- Find all EC2 instances in a specific region
SELECT resourceId, resourceType, awsRegion
WHERE resourceType = 'AWS::EC2::Instance'
AND awsRegion = 'eu-west-1'

-- Find non-compliant resources
SELECT resourceId, resourceType, complianceType
WHERE complianceType = 'NON_COMPLIANT'
```

---

## AWS Config vs CloudTrail

| | AWS Config | CloudTrail |
|-|-----------|-----------|
| Tracks | **What** the config looks like | **Who** made an API call |
| Focus | State / compliance | Activity / audit trail |
| Storage | Config history in S3 | API call logs in S3 |
| Use for | Drift detection, compliance | Security investigation, who-changed-what |
| Together | Use both — Config for state, CloudTrail for the actor |
