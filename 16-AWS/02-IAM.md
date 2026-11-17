# IAM: Identity and Access Management

> IAM is the AWS service that controls **who** can authenticate and **what** they are authorised to do. It manages users, groups, roles, and policies to enforce the principle of least privilege across your AWS account.

---

## Core Components

### Users

- Represent individual people or applications that interact with AWS
- Each user has unique credentials (password for console, access keys for CLI/API)
- New users have **no permissions** by default — explicit deny until granted
- Can hold long-term credentials (passwords and access keys)

### Groups

- Collections of users with similar access requirements
- Assign permissions to the group, not to each user individually
- Users can belong to multiple groups; group rules are additive

### Roles

- Temporary identities assumed by users, applications, or AWS services
- Do **not** have long-term credentials — they use short-lived STS tokens
- Common use cases:
  - EC2 instances accessing other AWS services (via instance profile)
  - Cross-account access between AWS accounts
  - Federated users from external identity providers (SAML, OIDC)
  - AWS services acting on your behalf (Lambda, EKS, etc.)
- When assumed, AWS STS returns: `AccessKeyId`, `SecretAccessKey`, `SessionToken`

### Policies

JSON documents that define permissions. There are two main categories:

#### Identity-Based Policies

Attached to users, groups, or roles.

| Type | Description |
|---|---|
| **AWS Managed** | Created and maintained by AWS; cover common use cases |
| **Customer Managed** | Created by you; reusable across multiple identities |
| **Inline** | Embedded directly into one identity; deleted with it |

#### Resource-Based Policies

Attached directly to resources (S3 buckets, SQS queues, KMS keys).

- Must include a `Principal` element (who the policy applies to)
- Support cross-account access without requiring a role assumption

---

## Policy Structure

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::my-bucket/*",
      "Condition": {
        "IpAddress": {
          "aws:SourceIp": "203.0.113.0/24"
        }
      }
    }
  ]
}
```

| Element | Description |
|---|---|
| `Version` | Policy language version — always `"2012-10-17"` |
| `Statement` | Array of individual permission statements |
| `Effect` | `"Allow"` or `"Deny"` |
| `Action` | The API operations being allowed or denied (e.g., `s3:GetObject`) |
| `Resource` | The AWS resources the actions apply to (ARN format) |
| `Principal` | Who the statement applies to (resource-based policies only) |
| `Condition` | Optional — restrict by IP, MFA, time, tags, etc. |

> **Evaluation order:** Explicit Deny > Explicit Allow > Implicit Deny. An explicit `Deny` always wins.

---

## Common CLI Operations

### Users and Groups

```bash
# Create a user
aws iam create-user --user-name Alice

# Create a group and add user
aws iam create-group --group-name Developers
aws iam add-user-to-group --group-name Developers --user-name Alice

# Attach a managed policy to a group
aws iam attach-group-policy \
  --group-name Developers \
  --policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess
```

### Roles

```bash
# Create role trust policy (allows EC2 to assume the role)
cat > trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "ec2.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
EOF

aws iam create-role \
  --role-name EC2S3ReadRole \
  --assume-role-policy-document file://trust-policy.json

# Attach policy to role
aws iam attach-role-policy \
  --role-name EC2S3ReadRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess
```

### Policies

```bash
# Create a custom policy (S3 read-only on a specific bucket)
cat > s3-read-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject", "s3:ListBucket"],
    "Resource": [
      "arn:aws:s3:::my-bucket",
      "arn:aws:s3:::my-bucket/*"
    ]
  }]
}
EOF

aws iam create-policy \
  --policy-name S3ReadMyBucket \
  --policy-document file://s3-read-policy.json
```

### MFA

```bash
# Enable MFA for a user
aws iam enable-mfa-device \
  --user-name Alice \
  --serial-number arn:aws:iam::123456789012:mfa/Alice \
  --authentication-code-1 123456 \
  --authentication-code-2 654321
```

---

## IAM in EKS

| Role | Used by | Key Policies |
|---|---|---|
| **Cluster IAM Role** | EKS control plane | `AmazonEKSClusterPolicy` |
| **Node IAM Role** | EC2 worker nodes | `AmazonEKSWorkerNodePolicy`, `AmazonEC2ContainerRegistryReadOnly`, `AmazonEKS_CNI_Policy` |

---

## Best Practices

| Practice | Why |
|---|---|
| **Principle of Least Privilege** | Grant only what is needed, nothing more |
| **Use roles for applications** | Avoid embedding long-term access keys in code |
| **Enable MFA** | Protects privileged and root accounts |
| **Rotate credentials regularly** | Limits blast radius of a compromised key |
| **Use IAM Access Analyzer** | Identifies resources shared with external accounts |
| **Monitor with CloudTrail** | Audit every API call made with IAM identities |
| **Never use root for day-to-day** | Create an admin IAM user instead |

---

## Summary

AWS IAM is the foundation of security in AWS. Users, groups, roles, and policies work together to enforce least-privilege access. For applications running on AWS, always use **roles** (not access keys) — roles provide short-lived, automatically-rotated credentials with no manual secret management.
