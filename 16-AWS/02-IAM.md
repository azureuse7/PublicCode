(# IAM: Identity and Access Management

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


# LocalDNS AKS Spike Runbook

**Purpose:** trial AKS LocalDNS on a single non-prod test node pool, capture findings for the spike doc, and roll back cleanly.

**Placeholders to replace:** `<RG>`, `<CLUSTER>`, `<POOL>` (for example `ldnstest`), `<CUSTOM_DNS_IP>`, `<fqdn>`.

**Reference:** [Configure LocalDNS in AKS](https://learn.microsoft.com/azure/aks/localdns-custom)

---

## 1. Pre-checks (before creating anything)

| # | Check | Command | Expected |
|---|---|---|---|
| 1.1 | Azure CLI version | `az version` | 2.80.0 or later |
| 1.2 | Cluster Kubernetes version | `az aks show -g <RG> -n <CLUSTER> --query kubernetesVersion -o tsv` | 1.31 or later |
| 1.3 | Existing pools: OS, size, version | `az aks nodepool list -g <RG> --cluster-name <CLUSTER> --query "[].{name:name,os:osSku,vm:vmSize,ver:orchestratorVersion}" -o table` | Ubuntu (22.04+) or AzureLinux |
| 1.4 | Node OS image | `kubectl get nodes -o wide` | Ubuntu 22.04 or newer |
| 1.5 | No NodeLocal DNSCache | `kubectl get ds -n kube-system` | No DaemonSet named `node-local-dns`. If one is there, **STOP** |
| 1.6 | Network plugin | `az aks show -g <RG> -n <CLUSTER> --query networkProfile -o json` | `networkPlugin` is not `none`. Note whether Cilium or Calico policy is used |
| 1.7 | Network policies | `kubectl get networkpolicy -A`, plus `kubectl get cnp,ccnp -A` if using Cilium | Note any egress-deny policies. DNS egress to LocalDNS must be allowed |
| 1.8 | Custom DNS over UDP and TCP 53 | `dig +udp @<CUSTOM_DNS_IP> <fqdn>` and `dig +tcp @<CUSTOM_DNS_IP> <fqdn>` | Both return an answer. If TCP fails, fix NSG, firewall or NVA rules first |
| 1.9 | Custom DNS TCP client limits | Ask the network/DNS team | No low cap on concurrent TCP clients |

> Run 1.8 from a node (for example via `kubectl debug node/<node> -it --image=ubuntu`) so the test uses the same network path as LocalDNS.

---

## 2. Baseline measurement (before LocalDNS)

```bash
kubectl run dnsbase --image=registry.k8s.io/e2e-test-images/jessie-dnsutils:1.3 --restart=Never -- sleep 3600

kubectl exec -it dnsbase -- cat /etc/resolv.conf

kubectl exec -it dnsbase -- sh -c 'for i in $(seq 1 50); do dig +noall +stats kubernetes.default.svc.cluster.local | grep "Query time"; done'

kubectl exec -it dnsbase -- sh -c 'for i in $(seq 1 50); do dig +noall +stats microsoft.com | grep "Query time"; done'
```

**Expected:** `nameserver` is the kube-dns service IP (usually `10.0.0.10`). Record the average query times.

---

## 3. Create the config file

Save as `localdnsconfig.json` and keep it in the IaC repo:

```json
{
  "mode": "Required",
  "vnetDNSOverrides": {
    ".": {
      "queryLogging": "Error",
      "protocol": "PreferUDP",
      "forwardDestination": "VnetDNS",
      "forwardPolicy": "Sequential",
      "maxConcurrent": 1000,
      "cacheDurationInSeconds": 3600,
      "serveStaleDurationInSeconds": 3600,
      "serveStale": "Immediate"
    },
    "cluster.local": {
      "queryLogging": "Error",
      "protocol": "ForceTCP",
      "forwardDestination": "ClusterCoreDNS",
      "forwardPolicy": "Sequential",
      "maxConcurrent": 1000,
      "cacheDurationInSeconds": 3600,
      "serveStaleDurationInSeconds": 3600,
      "serveStale": "Immediate"
    }
  },
  "kubeDNSOverrides": {
    ".": {
      "queryLogging": "Error",
      "protocol": "PreferUDP",
      "forwardDestination": "ClusterCoreDNS",
      "forwardPolicy": "Sequential",
      "maxConcurrent": 1000,
      "cacheDurationInSeconds": 3600,
      "serveStaleDurationInSeconds": 3600,
      "serveStale": "Immediate"
    },
    "cluster.local": {
      "queryLogging": "Error",
      "protocol": "ForceTCP",
      "forwardDestination": "ClusterCoreDNS",
      "forwardPolicy": "Sequential",
      "maxConcurrent": 1000,
      "cacheDurationInSeconds": 3600,
      "serveStaleDurationInSeconds": 3600,
      "serveStale": "Immediate"
    }
  }
}
```

**What the sections mean:**
- `vnetDNSOverrides` – applies to pods with `dnsPolicy: Default`.
- `kubeDNSOverrides` – applies to pods with `dnsPolicy: ClusterFirst` (most workloads).
- `.` – external names. `cluster.local` – in-cluster service discovery.

**Validation rules:**
- Under `vnetDNSOverrides`, the `.` zone can't use `ClusterCoreDNS`.
- `cluster.local` can't use `VnetDNS` in either section.
- `ForceTCP` can't be combined with `serveStale: Verify` – use `Immediate`.

**Modes:** `Required` (enforced; fails if prerequisites aren't met), `Preferred` (validates only, still reimages), `Disabled`.

---

## 4. Create the test node pool

```bash
az aks nodepool add \
  -g <RG> --cluster-name <CLUSTER> --name <POOL> \
  --node-vm-size Standard_D4as_v5 --os-sku Ubuntu \
  --node-count 1 --mode User \
  --labels purpose=localdns-test \
  --node-taints localdns-test=true:NoSchedule \
  --localdns-config ./localdnsconfig.json

az aks nodepool show -g <RG> --cluster-name <CLUSTER> -n <POOL> --query localDnsProfile -o json

kubectl get nodes -l purpose=localdns-test -o wide
```

**Expected:** the profile shows `mode: Required` and the node is `Ready`. If the command fails, record the prerequisite error message.

> The taint keeps other workloads off the test node, so only pods you deliberately schedule there are affected.

---

## 5. Verify LocalDNS is working

```bash
kubectl run dnstest --image=registry.k8s.io/e2e-test-images/jessie-dnsutils:1.3 --restart=Never \
  --overrides='{"spec":{"nodeSelector":{"purpose":"localdns-test"},"tolerations":[{"key":"localdns-test","operator":"Equal","value":"true","effect":"NoSchedule"}]}}' \
  -- sleep 3600

kubectl exec -it dnstest -- cat /etc/resolv.conf
kubectl exec -it dnstest -- nslookup kubernetes.default
kubectl exec -it dnstest -- nslookup microsoft.com
```

**Expected:** `Server:` is `169.254.10.10` or `169.254.10.11`, and both names resolve.

---

## 6. Measure with LocalDNS

Repeat the Section 2 loops inside `dnstest`:

```bash
kubectl exec -it dnstest -- sh -c 'for i in $(seq 1 50); do dig +noall +stats kubernetes.default.svc.cluster.local | grep "Query time"; done'

kubectl exec -it dnstest -- sh -c 'for i in $(seq 1 50); do dig +noall +stats microsoft.com | grep "Query time"; done'
```

**Expected:** lower and more consistent query times, especially on cached lookups. Record before/after numbers in a table:

| Lookup | Baseline avg (ms) | LocalDNS avg (ms) |
|---|---|---|
| `kubernetes.default.svc.cluster.local` | | |
| `microsoft.com` | | |

---

## 7. Metrics

```bash
NODE_IP=$(kubectl get node -l purpose=localdns-test -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')

kubectl exec -it dnstest -- curl -s http://$NODE_IP:9253/metrics | head -50
```

**Expected:** Prometheus-format metrics. Note whether your monitoring tool can scrape port 9253 on each node.

---

## 8. Optional resilience test (non-prod only, with team agreement)

Checks that cached answers keep being served when CoreDNS is unavailable.

```bash
# Warm the cache first
kubectl exec -it dnstest -- nslookup kubernetes.default
kubectl exec -it dnstest -- nslookup microsoft.com

# Take CoreDNS down
kubectl -n kube-system scale deploy coredns --replicas=0

# Test from the LocalDNS pod and the baseline pod
kubectl exec -it dnstest -- nslookup kubernetes.default
kubectl exec -it dnstest -- nslookup microsoft.com
kubectl exec -it dnsbase -- nslookup kubernetes.default

# Restore CoreDNS immediately afterwards
kubectl -n kube-system scale deploy coredns --replicas=2
kubectl -n kube-system get pods -l k8s-app=kube-dns
```

**Expected:** `dnstest` (LocalDNS) still answers from cache; `dnsbase` fails or times out. CoreDNS pods are back to `Running` after the restore.

> Note: on AKS the CoreDNS deployment is managed by the add-on and may be scaled back automatically. Check the original replica count before scaling down and restore to that value. Caching is best effort, so stale answers aren't guaranteed.

---

## 9. Roll back / clean up

```bash
# Remove test pods
kubectl delete pod dnstest dnsbase --ignore-not-found

# Option A – delete the test pool (simplest)
az aks nodepool delete -g <RG> --cluster-name <CLUSTER> -n <POOL>

# Option B – keep the pool but disable LocalDNS
# Set "mode": "Disabled" in localdnsconfig.json, then:
az aks nodepool update -g <RG> --cluster-name <CLUSTER> -n <POOL> --localdns-config ./localdnsconfig.json
```

**Expected:** the pool is gone (A), or pods on it show the kube-dns IP in `/etc/resolv.conf` again (B). Option B reimages the node.

---

## 10. Findings for the spike write-up

Record each of these:

- [ ] Prerequisites met across clusters (version, OS, VM size, no NodeLocal DNSCache)
- [ ] Network readiness: TCP 53 to custom DNS, NSG/firewall/NVA rules, network policy changes needed
- [ ] Node pool creation result and any errors
- [ ] Verification result (`169.254.10.10` / `169.254.10.11` seen)
- [ ] Before/after latency numbers
- [ ] Resilience test result (if run)
- [ ] Monitoring: can metrics on port 9253 be scraped
- [ ] Operational impact: every enable/change/disable reimages the pool – PDBs and maintenance windows needed
- [ ] NAP clusters: configured via `spec.localDNS` in the `AKSNodeClass`; changes drift and replace nodes rather than reimage
- [ ] Kubernetes 1.37+ risk: pools with no explicit LocalDNS profile default to `Preferred` and may turn LocalDNS on after an upgrade – decide whether to set `Disabled` explicitly until adopted
- [ ] IaC: whether the Terraform provider version in use supports the LocalDNS profile
- [ ] EKS comparison: Auto Mode runs CoreDNS on each node by default; standard EKS needs self-managed NodeLocal DNSCache
- [ ] Recommendation: adopt / don't adopt / explicitly disable for now, plus follow-up stories


AWS IAM is the foundation of security in AWS. Users, groups, roles, and policies work together to enforce least-privilege access. For applications running on AWS, always use **roles** (not access keys) — roles provide short-lived, automatically-rotated credentials with no manual secret management.
