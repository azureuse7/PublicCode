# EKS OIDC and IRSA: IAM Roles for Service Accounts

> IRSA (IAM Roles for Service Accounts) lets individual Kubernetes pods assume specific IAM roles — providing fine-grained, pod-level AWS permissions without node-level instance roles or long-lived access keys.

---

## The Problem IRSA Solves

Without IRSA, you would need to attach AWS permissions to the EC2 node IAM role — meaning **every pod on that node** gets those permissions. IRSA allows each pod to have only the permissions it specifically needs.

```
Without IRSA:
EC2 Node Role → S3 Full Access
  → Pod A gets S3 Full Access ✓ (it needs it)
  → Pod B gets S3 Full Access ✗ (it shouldn't have it)

With IRSA:
EC2 Node Role → minimal permissions (join cluster, pull images)
  → Pod A's ServiceAccount → IAM Role → S3 Read (just one bucket)
  → Pod B has no AWS permissions
```

---

## How IRSA Works

```
1. EKS cluster exposes an OIDC issuer URL
   e.g., https://oidc.eks.us-west-2.amazonaws.com/id/ABCD1234

2. You register that URL as an IAM OIDC provider

3. You create an IAM role with a trust policy that says:
   "Allow tokens from this OIDC provider where the subject
    is system:serviceaccount:<namespace>:<serviceaccount>"

4. You annotate the Kubernetes ServiceAccount with the IAM role ARN

5. EKS injects a projected OIDC token into pods using that ServiceAccount

6. The AWS SDK in the pod exchanges the token with STS for temporary credentials
```

---

## Step-by-Step Setup

### Prerequisites

- `eksctl`, `kubectl`, and AWS CLI configured
- An EKS cluster (or create one with `--with-oidc` to auto-setup the provider)

### Step 1: Associate the OIDC Provider

```bash
eksctl utils associate-iam-oidc-provider \
  --cluster my-cluster \
  --region us-west-2 \
  --approve
```

Or manually via AWS CLI:

```bash
# Get the OIDC issuer URL
oidc_url=$(aws eks describe-cluster \
  --name my-cluster \
  --region us-west-2 \
  --query "cluster.identity.oidc.issuer" \
  --output text)

# Get the TLS thumbprint
thumbprint=$(echo | openssl s_client -servername oidc.eks.us-west-2.amazonaws.com \
  -connect oidc.eks.us-west-2.amazonaws.com:443 2>/dev/null \
  | openssl x509 -fingerprint -noout | sed 's/://g' | awk -F= '{print tolower($2)}')

# Create the OIDC provider
aws iam create-open-id-connect-provider \
  --url $oidc_url \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list $thumbprint
```

### Step 2: Create an IAM Policy

```bash
cat > s3-read-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject", "s3:ListBucket"],
    "Resource": [
      "arn:aws:s3:::my-secure-bucket",
      "arn:aws:s3:::my-secure-bucket/*"
    ]
  }]
}
EOF

aws iam create-policy \
  --policy-name EKSReadMyBucket \
  --policy-document file://s3-read-policy.json
```

Note the returned ARN: `arn:aws:iam::111122223333:policy/EKSReadMyBucket`

### Step 3: Create the IAM Role with a Trust Policy

Get the OIDC provider ARN:

```bash
oidc_provider=$(aws eks describe-cluster \
  --name my-cluster \
  --region us-west-2 \
  --query "cluster.identity.oidc.issuer" \
  --output text | sed 's|https://||')

# e.g., oidc.eks.us-west-2.amazonaws.com/id/ABCD1234EFGH5678IJKL
```

Create the trust policy:

```bash
cat > trust.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::111122223333:oidc-provider/${oidc_provider}"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "${oidc_provider}:sub": "system:serviceaccount:demo:read-s3-sa",
        "${oidc_provider}:aud": "sts.amazonaws.com"
      }
    }
  }]
}
EOF

aws iam create-role \
  --role-name EKS-S3ReadRole \
  --assume-role-policy-document file://trust.json

aws iam attach-role-policy \
  --role-name EKS-S3ReadRole \
  --policy-arn arn:aws:iam::111122223333:policy/EKSReadMyBucket
```

### Shortcut: Use eksctl to do Steps 1-3 automatically

```bash
eksctl create iamserviceaccount \
  --cluster my-cluster \
  --region us-west-2 \
  --namespace demo \
  --name read-s3-sa \
  --attach-policy-arn arn:aws:iam::111122223333:policy/EKSReadMyBucket \
  --approve
```

This creates the IAM role, trust policy, and annotated ServiceAccount in one command.

### Step 4: Create the Kubernetes ServiceAccount (if not using eksctl)

```yaml
# sa-read-s3.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: read-s3-sa
  namespace: demo
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::111122223333:role/EKS-S3ReadRole
```

```bash
kubectl create namespace demo
kubectl apply -f sa-read-s3.yaml
```

### Step 5: Deploy a Pod Using the ServiceAccount

```yaml
# pod-list-s3.yaml
apiVersion: v1
kind: Pod
metadata:
  name: list-s3
  namespace: demo
spec:
  serviceAccountName: read-s3-sa
  containers:
  - name: aws-cli
    image: amazon/aws-cli:latest
    command: ["aws", "s3", "ls", "s3://my-secure-bucket"]
```

```bash
kubectl apply -f pod-list-s3.yaml
kubectl logs -n demo pod/list-s3
# Expected: list of objects in the bucket
```

---

## How the Token Exchange Works

```
Pod starts with ServiceAccount read-s3-sa
    │
    ▼
EKS injects a projected OIDC token into the pod at:
/var/run/secrets/eks.amazonaws.com/serviceaccount/token

AWS SDK (or CLI) reads these env vars:
  AWS_ROLE_ARN=arn:aws:iam::111122223333:role/EKS-S3ReadRole
  AWS_WEB_IDENTITY_TOKEN_FILE=/var/run/secrets/eks.amazonaws.com/serviceaccount/token
    │
    ▼
SDK calls AWS STS: AssumeRoleWithWebIdentity(token, roleArn)
    │
    ▼
STS validates the token against the OIDC provider
    │
    ▼
STS returns temporary credentials (15 min – 12 hr)
    │
    ▼
SDK uses credentials to call AWS APIs (S3, DynamoDB, etc.)
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `AccessDenied` from pod | IAM policy too restrictive | Check policy actions and resources |
| `Not authorized to perform sts:AssumeRoleWithWebIdentity` | Trust policy condition mismatch | Verify namespace and SA name in trust policy `sub` |
| Credentials not found | Missing annotation on ServiceAccount | Add `eks.amazonaws.com/role-arn` annotation |
| OIDC provider not found | Provider not registered | Run `associate-iam-oidc-provider` |

---

## Key Takeaways

- **No static credentials** — pods get short-lived STS tokens, automatically rotated
- **Least privilege per pod** — each ServiceAccount maps to its own role
- **Works with any AWS SDK** — the token exchange is handled transparently
- **Scales to any service** — S3, DynamoDB, SQS, Secrets Manager, RDS IAM auth, etc.
