# aws-auth ConfigMap

> The `aws-auth` ConfigMap is the legacy mechanism for granting IAM principals access to an EKS cluster. It maps IAM roles/users to Kubernetes usernames and groups. For new clusters, prefer **EKS Access Entries** (see [36-Access-Entry.md](36-Access-Entry.md)) — they are API-driven, auditable, and don't require direct ConfigMap editing.

---

## What It Is

The `aws-auth` ConfigMap lives in the `kube-system` namespace. When an IAM principal authenticates to the Kubernetes API, the EKS authenticator checks this ConfigMap to determine which Kubernetes identity (username/groups) to map them to.

```
IAM Principal (User / Role) authenticates
        │
        ▼
EKS authenticates via IAM
        │
        ▼
aws-auth ConfigMap: find matching IAM ARN
        │
        ▼
Map to Kubernetes username + groups
        │
        ▼
RBAC evaluates permissions for that username/group
```

---

## Structure

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: aws-auth
  namespace: kube-system
data:
  mapRoles: |
    - rolearn: arn:aws:iam::111122223333:role/NodeInstanceRole
      username: system:node:{{EC2PrivateDNSName}}
      groups:
        - system:bootstrappers
        - system:nodes

    - rolearn: arn:aws:iam::111122223333:role/dev-team
      username: dev-user
      groups:
        - dev-team-group

  mapUsers: |
    - userarn: arn:aws:iam::111122223333:user/alice
      username: alice
      groups:
        - system:masters
```

| Field | Description |
|---|---|
| `mapRoles` | Maps IAM roles (used by EC2 nodes, CI/CD roles, assumed roles) |
| `mapUsers` | Maps IAM users directly (less common — roles preferred) |
| `rolearn` | Full ARN of the IAM role |
| `userarn` | Full ARN of the IAM user |
| `username` | The Kubernetes username the principal maps to |
| `groups` | Kubernetes groups the principal belongs to (drives RBAC) |

---

## Viewing the ConfigMap

```bash
kubectl get configmap aws-auth -n kube-system -o yaml
```

---

## Editing the ConfigMap

> **Warning:** Editing `aws-auth` directly is risky. A YAML syntax error can lock all users out of the cluster. Always make a backup first and test in a non-production cluster.

```bash
# Backup first
kubectl get configmap aws-auth -n kube-system -o yaml > aws-auth-backup.yaml

# Edit
kubectl edit configmap aws-auth -n kube-system
```

Or apply a full replacement:

```bash
kubectl apply -f aws-auth.yaml
```

### Using eksctl (safer)

```bash
# Add an IAM role
eksctl create iamidentitymapping \
  --cluster my-cluster \
  --region us-east-1 \
  --arn arn:aws:iam::111122223333:role/dev-team \
  --username dev-user \
  --group dev-team-group

# Add an IAM user
eksctl create iamidentitymapping \
  --cluster my-cluster \
  --region us-east-1 \
  --arn arn:aws:iam::111122223333:user/alice \
  --username alice \
  --group system:masters

# List all mappings
eksctl get iamidentitymapping --cluster my-cluster --region us-east-1

# Remove a mapping
eksctl delete iamidentitymapping \
  --cluster my-cluster \
  --region us-east-1 \
  --arn arn:aws:iam::111122223333:role/dev-team
```

---

## Node Role Entry (Required)

Every EC2 worker node needs an entry so it can join the cluster. If you create a node group with `eksctl`, this is added automatically. If you do it manually:

```yaml
mapRoles: |
  - rolearn: arn:aws:iam::111122223333:role/MyNodeGroupRole
    username: system:node:{{EC2PrivateDNSName}}
    groups:
      - system:bootstrappers
      - system:nodes
```

---

## Common Kubernetes Groups

| Group | Access Level |
|---|---|
| `system:masters` | Full cluster admin (equivalent to `cluster-admin` ClusterRole) |
| `system:nodes` | Required for worker nodes |
| `system:bootstrappers` | Required for nodes during cluster join |
| Custom group (e.g., `dev-team`) | Controlled by your own ClusterRoleBinding/RoleBinding |

---

## Full Example: Add a Dev Team Role

```bash
# 1. Add the mapping
eksctl create iamidentitymapping \
  --cluster my-cluster \
  --region us-east-1 \
  --arn arn:aws:iam::111122223333:role/dev-team \
  --username dev-user \
  --group dev-team

# 2. Create a RoleBinding in the 'dev' namespace
kubectl create rolebinding dev-team-binding \
  --namespace dev \
  --clusterrole edit \
  --group dev-team
```

Now anyone who assumes the `dev-team` role can edit resources in the `dev` namespace.

---

## aws-auth vs EKS Access Entries

| | aws-auth ConfigMap | EKS Access Entry |
|---|---|---|
| Management method | `kubectl edit` / `eksctl` | AWS API / console / Terraform |
| Audit trail | None (direct etcd write) | CloudTrail |
| Risk | Misconfiguration = lockout | Safe — API validates input |
| Namespace scoping | Requires separate RBAC objects | Native in access policy association |
| EKS Auto Mode | Not supported | Required |
| Recommendation | Legacy / migration | Use for new clusters |

---

## Migrating to Access Entries

1. Set the cluster authentication mode to `API_AND_CONFIG_MAP` (runs both in parallel)
2. Create EKS Access Entries for all roles/users in `aws-auth`
3. Verify access works via access entries
4. Switch to `API` mode only (disables `aws-auth`)

```bash
# Switch to dual mode
aws eks update-cluster-config \
  --name my-cluster \
  --access-config authenticationMode=API_AND_CONFIG_MAP

# After migrating all entries, switch to API-only
aws eks update-cluster-config \
  --name my-cluster \
  --access-config authenticationMode=API
```

---

## Summary

The `aws-auth` ConfigMap is the legacy way to manage EKS cluster access. It works but is fragile — a YAML error can lock everyone out. For new clusters, use EKS Access Entries instead. For existing clusters, plan a migration using `API_AND_CONFIG_MAP` dual mode to transition safely.
