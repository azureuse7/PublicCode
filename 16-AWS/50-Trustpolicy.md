# IAM Trust Policies

> A trust policy is a JSON document attached to an IAM role that defines **who is allowed to assume that role**. Think of it as the gatekeeper — it controls *access to the role itself*, before any permissions attached to the role come into play.

---

## The Two-Layer Permission Model

When using IAM roles, two separate policies must both allow an action for it to succeed:

```
Caller (user / service / account)
        │
        ▼ "Can I assume this role?"
  Trust Policy ──── checks: is this principal in my trusted list?
        │
        ▼ (if yes — role is assumed, STS issues temp credentials)
  Permissions Policy ──── checks: what can this role do?
        │
        ▼
  Action succeeds or is denied
```

| Policy | Question it answers |
|---|---|
| **Trust Policy** | *Who* can assume this role? |
| **Permissions Policy** | *What* can this role do? |

---

## Trust Policy Structure

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Action": "sts:AssumeRole",
      "Condition": {}
    }
  ]
}
```

| Field | Description |
|---|---|
| `Effect` | Always `"Allow"` in trust policies |
| `Principal` | Who is trusted to assume the role |
| `Action` | The STS action allowed — see variants below |
| `Condition` | Optional restrictions (e.g., specific tags, MFA, OIDC claims) |

---

## Principal Types

| Principal | Example | Used When |
|---|---|---|
| **AWS Service** | `"Service": "ec2.amazonaws.com"` | EC2 instances, Lambda, EKS, etc. |
| **IAM User** | `"AWS": "arn:aws:iam::123456789012:user/Alice"` | Specific IAM user |
| **IAM Role** | `"AWS": "arn:aws:iam::123456789012:role/DevRole"` | Role chaining |
| **AWS Account** | `"AWS": "arn:aws:iam::999988887777:root"` | Cross-account trust |
| **Federated (OIDC)** | `"Federated": "arn:aws:iam::123456789012:oidc-provider/..."` | IRSA, web identity federation |
| **Everyone** | `"Principal": "*"` | Avoid — use only with strict Conditions |

---

## STS Action Variants

| Action | Used For |
|---|---|
| `sts:AssumeRole` | Standard role assumption (IAM users, roles, cross-account) |
| `sts:AssumeRoleWithWebIdentity` | OIDC federation (IRSA, Cognito, social logins) |
| `sts:AssumeRoleWithSAML` | SAML 2.0 enterprise federation |
| `sts:TagSession` | Pass session tags when assuming the role |

---

## Common Trust Policy Examples

### EC2 Instance Role

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "ec2.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

### Lambda Execution Role

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "lambda.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

### Cross-Account Access

Allow account `999988887777` to assume this role (useful for CI/CD or multi-account setups):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "AWS": "arn:aws:iam::999988887777:root"
    },
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": {
        "sts:ExternalId": "unique-secret-id-12345"
      }
    }
  }]
}
```

> The `ExternalId` condition prevents the **confused deputy problem** — it ensures that only your specific caller (who knows the ID) can assume the role, not any random entity from that account.

### EKS IRSA (OIDC Web Identity)

Allow a specific Kubernetes ServiceAccount to assume the role:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::111122223333:oidc-provider/oidc.eks.us-west-2.amazonaws.com/id/ABCD1234"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "oidc.eks.us-west-2.amazonaws.com/id/ABCD1234:sub": "system:serviceaccount:my-namespace:my-sa",
        "oidc.eks.us-west-2.amazonaws.com/id/ABCD1234:aud": "sts.amazonaws.com"
      }
    }
  }]
}
```

### EKS Cluster Service Role

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "eks.amazonaws.com" },
    "Action": ["sts:AssumeRole", "sts:TagSession"]
  }]
}
```

### Step Functions Role

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "states.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

---

## CLI Operations

```bash
# Create a role with a trust policy
aws iam create-role \
  --role-name MyEC2Role \
  --assume-role-policy-document file://trust-policy.json

# Update the trust policy of an existing role
aws iam update-assume-role-policy \
  --role-name MyEC2Role \
  --policy-document file://new-trust-policy.json

# View the current trust policy
aws iam get-role \
  --role-name MyEC2Role \
  --query 'Role.AssumeRolePolicyDocument'

# Assume a role (get temporary credentials)
aws sts assume-role \
  --role-arn arn:aws:iam::123456789012:role/MyEC2Role \
  --role-session-name my-session
```

---

## Best Practices

| Practice | Why |
|---|---|
| Use specific principals | Never use `"Principal": "*"` without a strict Condition |
| Add `ExternalId` for cross-account | Prevents confused deputy attacks |
| Use `sts:TagSession` for EKS | Enables attribute-based access control (ABAC) |
| Use OIDC conditions for IRSA | Scopes the role to a specific namespace + service account |
| Audit with IAM Access Analyzer | Flags roles with overly permissive trust policies |

---

## Summary

The trust policy is the first gate an IAM role checks — no principal can use a role unless the trust policy explicitly allows it. Get the principal type right (Service, AWS, Federated), choose the correct STS action, and add Conditions to narrow the trust as much as possible.
