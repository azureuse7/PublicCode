# EKS Access Entry

> EKS Access Entries are the modern way to grant IAM principals (users and roles) access to a Kubernetes cluster. They replace the need to manually edit the `aws-auth` ConfigMap — providing API-driven, auditable, and namespace-scoped access control.

---

## What Is an Access Entry?

An access entry maps an IAM principal (user or role) to Kubernetes permissions. It has two parts:

1. **Authentication** — the access entry itself proves the IAM principal is allowed to authenticate to the cluster
2. **Authorization** — attach EKS access policies (AWS-managed Kubernetes RBAC) or map to Kubernetes groups

```
IAM Principal (User / Role)
        │
        ▼
  EKS Access Entry (authentication)
        │
        ├── EKS Access Policy (e.g., AmazonEKSAdminPolicy)
        │     scoped to: cluster | namespace
        │
        └── Kubernetes Group mapping
              → standard RBAC via ClusterRoleBinding / RoleBinding
```

---

## Access Entry Types

| Type | Used For |
|---|---|
| **STANDARD** | Human users, automation, service accounts — attach access policies and/or K8s groups |
| **EC2_LINUX** | EC2 worker nodes (Linux) — EKS auto-grants required node permissions |
| **EC2_WINDOWS** | EC2 worker nodes (Windows) |
| **FARGATE_LINUX** | Fargate pods |
| **EC2** | EKS Auto Mode nodes |

---

## AWS-Managed Access Policies

| Policy | Kubernetes Equivalent |
|---|---|
| `AmazonEKSClusterAdminPolicy` | `cluster-admin` — full cluster access |
| `AmazonEKSAdminPolicy` | Admin — typically scoped to a namespace |
| `AmazonEKSEditPolicy` | Edit — read/write resources in a namespace |
| `AmazonEKSViewPolicy` | View-only access |
| `AmazonEKSAutoNodePolicy` | Required for EKS Auto Mode nodes |

---

## Authentication Modes

| Mode | Description |
|---|---|
| `API` | Access entries only — no `aws-auth` ConfigMap |
| `API_AND_CONFIG_MAP` | Both — useful during migration from ConfigMap to access entries |
| `CONFIG_MAP` | Legacy — `aws-auth` ConfigMap only |

---

## CLI: Create and Configure Access Entries

### Grant a developer namespace-scoped admin access

```bash
# Step 1: Create the access entry (authentication)
aws eks create-access-entry \
  --cluster-name my-eks \
  --principal-arn arn:aws:iam::111122223333:role/dev-team \
  --type STANDARD

# Step 2: Attach an access policy (authorization) scoped to the 'dev' namespace
aws eks associate-access-policy \
  --cluster-name my-eks \
  --principal-arn arn:aws:iam::111122223333:role/dev-team \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSAdminPolicy \
  --access-scope type=namespace,namespaces=dev
```

### Grant cluster-wide admin access

```bash
aws eks create-access-entry \
  --cluster-name my-eks \
  --principal-arn arn:aws:iam::111122223333:role/platform-admin \
  --type STANDARD

aws eks associate-access-policy \
  --cluster-name my-eks \
  --principal-arn arn:aws:iam::111122223333:role/platform-admin \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy \
  --access-scope type=cluster
```

### Register EC2 worker nodes (Auto Mode)

```bash
aws eks create-access-entry \
  --cluster-name my-eks \
  --principal-arn arn:aws:iam::111122223333:role/NodeRole \
  --type EC2

aws eks associate-access-policy \
  --cluster-name my-eks \
  --principal-arn arn:aws:iam::111122223333:role/NodeRole \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSAutoNodePolicy \
  --access-scope type=cluster
```

### List and inspect access entries

```bash
# List all access entries
aws eks list-access-entries --cluster-name my-eks

# Describe a specific entry
aws eks describe-access-entry \
  --cluster-name my-eks \
  --principal-arn arn:aws:iam::111122223333:role/dev-team

# List associated policies
aws eks list-associated-access-policies \
  --cluster-name my-eks \
  --principal-arn arn:aws:iam::111122223333:role/dev-team

# Delete an access entry
aws eks delete-access-entry \
  --cluster-name my-eks \
  --principal-arn arn:aws:iam::111122223333:role/dev-team
```

---

## Terraform

```hcl
# Create the access entry
resource "aws_eks_access_entry" "dev" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = aws_iam_role.dev.arn
  type          = "STANDARD"
}

# Associate an access policy scoped to a namespace
resource "aws_eks_access_policy_association" "dev_admin_ns" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = aws_iam_role.dev.arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSAdminPolicy"

  access_scope {
    type       = "namespace"
    namespaces = ["dev"]
  }

  depends_on = [aws_eks_access_entry.dev]
}
```

---

## Access Entries vs aws-auth ConfigMap

| | Access Entry | aws-auth ConfigMap |
|---|---|---|
| Management | AWS API / console / IaC | Manual `kubectl edit configmap` |
| Audit trail | CloudTrail | None (direct etcd edit) |
| Namespace scoping | Native | Requires additional RBAC objects |
| Risk | Low | Misconfiguration can lock out all users |
| EKS Auto Mode | Required | Not supported |
| Migration | Run in `API_AND_CONFIG_MAP` mode | — |

---

## Important Notes

- If you **delete and recreate** an IAM principal, the old access entry will not work — the internal IAM ID changes. Delete the access entry and recreate it.
- Access entries are **required** for EKS Auto Mode — you cannot disable them.
- You can mix both policies (EKS access policies) and Kubernetes group mappings on the same access entry.

---

## Summary

Access Entries are the recommended and future-proof way to manage EKS cluster access. They provide centralized, auditable, API-driven access management with fine-grained namespace scoping — eliminating the fragility and manual overhead of editing the `aws-auth` ConfigMap.
