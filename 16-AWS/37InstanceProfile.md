# EC2 Instance Profiles

> An Instance Profile is the container that lets you attach an IAM Role to an EC2 instance. When attached, the instance automatically receives temporary AWS credentials — no hardcoded access keys needed.

---

## How It Works

```
IAM Role (defines permissions)
        │
        ▼
Instance Profile (container for the role)
        │
        ▼
EC2 Instance (assumes the role on startup)
        │
        ▼
Application inside EC2 uses AWS SDK
→ SDK calls the EC2 metadata service (169.254.169.254)
→ Gets temporary credentials for the role
→ Calls AWS APIs (S3, DynamoDB, etc.)
```

---

## Instance Profile vs IAM Role

| Concept | Description |
|---|---|
| **IAM Role** | Defines *what* permissions are granted (policies attached to it) |
| **Instance Profile** | The *wrapper* that makes a role attachable to EC2 |
| **Console shortcut** | When you create an EC2 role in the IAM console, AWS automatically creates an instance profile with the **same name** — that's why you can pick "the role" directly in the EC2 launch wizard |

> You need the instance profile, not the role itself, when attaching to EC2 via CLI or IaC. The console hides this distinction.

---

## Creating and Attaching an Instance Profile

### Step 1: Create the IAM Role (trust EC2)

```bash
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
  --role-name MyEC2S3Role \
  --assume-role-policy-document file://trust-policy.json
```

### Step 2: Attach a permissions policy

```bash
aws iam attach-role-policy \
  --role-name MyEC2S3Role \
  --policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess
```

### Step 3: Create the instance profile

```bash
aws iam create-instance-profile \
  --instance-profile-name MyEC2S3Profile
```

### Step 4: Add the role to the profile

```bash
aws iam add-role-to-instance-profile \
  --instance-profile-name MyEC2S3Profile \
  --role-name MyEC2S3Role
```

### Step 5: Attach the profile to an EC2 instance

**At launch time:**

```bash
aws ec2 run-instances \
  --image-id ami-0abcdef1234567890 \
  --instance-type t3.micro \
  --iam-instance-profile Name=MyEC2S3Profile \
  --key-name MyKeyPair \
  --subnet-id subnet-12345678
```

**To an existing instance:**

```bash
aws ec2 associate-iam-instance-profile \
  --instance-id i-1234567890abcdef0 \
  --iam-instance-profile Name=MyEC2S3Profile
```

---

## Verifying the Role Works

SSH into the instance and run:

```bash
# Check which identity the instance is using
aws sts get-caller-identity

# List S3 buckets (should work if role has AmazonS3ReadOnlyAccess)
aws s3 ls

# View the raw credentials (temporary STS token from metadata service)
curl http://169.254.169.254/latest/meta-data/iam/security-credentials/MyEC2S3Role
```

---

## Changing the Instance Profile on a Running Instance

```bash
# First, get the current association ID
aws ec2 describe-iam-instance-profile-associations \
  --filters Name=instance-id,Values=i-1234567890abcdef0

# Replace with a different profile
aws ec2 replace-iam-instance-profile-association \
  --association-id iip-assoc-0123456789abcdef0 \
  --iam-instance-profile Name=NewInstanceProfile

# Or disassociate entirely
aws ec2 disassociate-iam-instance-profile \
  --association-id iip-assoc-0123456789abcdef0
```

---

## Common Use Cases

| Use Case | Role Policies |
|---|---|
| EC2 reads from S3 | `AmazonS3ReadOnlyAccess` |
| EC2 writes to S3 | `AmazonS3FullAccess` or custom write policy |
| EC2 sends logs to CloudWatch | `CloudWatchAgentServerPolicy` |
| EC2 accesses SSM Parameter Store | `AmazonSSMReadOnlyAccess` |
| EC2 joins EKS node group | `AmazonEKSWorkerNodePolicy`, `AmazonEC2ContainerRegistryReadOnly`, `AmazonEKS_CNI_Policy` |

---

## Best Practices

- **Never hardcode AWS credentials** on an EC2 instance — use instance profiles instead
- Follow **least privilege** — grant only the specific actions and resources the application needs
- Use **resource-level permissions** in the policy (e.g., `arn:aws:s3:::my-specific-bucket/*` not `*`)
- **Rotate** by modifying the attached role policies, not by generating new access keys
- Use **IMDSv2** (Instance Metadata Service v2) to prevent SSRF attacks from stealing credentials:

```bash
# Enforce IMDSv2 at instance launch
aws ec2 run-instances \
  --metadata-options HttpTokens=required,HttpEndpoint=enabled \
  ... (other options)
```

---

## Summary

Instance profiles are the bridge between IAM roles and EC2 instances. They eliminate the need for hardcoded credentials, provide automatically-rotated temporary credentials, and integrate seamlessly with the AWS SDK. Always use instance profiles for any EC2 workload that needs to call AWS APIs.
