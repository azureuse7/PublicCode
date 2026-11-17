# AWS Security Groups: Deep Dive

> Security Groups are stateful virtual firewalls attached to AWS resources (EC2, RDS, Lambda, etc.). They control inbound and outbound traffic using **allow-only** rules — traffic not explicitly allowed is denied by default.

> **See also:** [06-Security.md](06-Security.md) for a side-by-side comparison of Security Groups vs NACLs.

---

## How Security Groups Work

```
Internet
    │
    ▼
Security Group ──► EC2 Instance
(inbound rules)     (never sees blocked traffic)
    │
    ▼
Security Group
(outbound rules)
    │
    ▼
Internet / Other resources
```

- The Security Group **lives outside** the instance — blocked traffic never reaches it
- **Stateful**: if an inbound connection is allowed, the return traffic is automatically permitted (no separate outbound rule needed)
- Rules contain: protocol, port range, and source/destination (CIDR or Security Group ID)
- **Only allow rules** — there is no explicit deny

---

## Inbound vs Outbound

| | Inbound Rules | Outbound Rules |
|---|---|---|
| Controls | Traffic entering the resource | Traffic leaving the resource |
| Default | All blocked | All allowed |
| Common restriction | Allow only specific ports from specific sources | Restrict outbound to specific services |

---

## Common Port Reference

| Port | Protocol | Service |
|---|---|---|
| 22 | TCP | SSH — Linux instance administration |
| 3389 | TCP | RDP — Windows instance administration |
| 80 | TCP | HTTP |
| 443 | TCP | HTTPS |
| 21 | TCP | FTP |
| 3306 | TCP | MySQL / MariaDB |
| 5432 | TCP | PostgreSQL |
| 6379 | TCP | Redis |
| 27017 | TCP | MongoDB |

---

## Example Rule Configurations

### Web Server Security Group

| Direction | Protocol | Port | Source | Purpose |
|---|---|---|---|---|
| Inbound | TCP | 80 | `0.0.0.0/0` | HTTP from internet |
| Inbound | TCP | 443 | `0.0.0.0/0` | HTTPS from internet |
| Inbound | TCP | 22 | `10.0.0.0/8` | SSH from VPN only |
| Outbound | All | All | `0.0.0.0/0` | All outbound allowed |

### Database Security Group (private — no internet access)

| Direction | Protocol | Port | Source | Purpose |
|---|---|---|---|---|
| Inbound | TCP | 5432 | `sg-app-servers` | PostgreSQL from app tier only |
| Outbound | All | All | `0.0.0.0/0` | All outbound allowed |

> Using a **Security Group ID as the source** (instead of a CIDR) is the recommended pattern — it allows all EC2 instances in the `sg-app-servers` group to connect, without needing to know their IPs.

---

## Referencing Security Groups

Security Groups can reference each other. This is the standard pattern for multi-tier architectures:

```
Internet → ALB Security Group (port 443)
                │
                │ (ALB SG as source)
                ▼
           App Security Group (port 8080)
                │
                │ (App SG as source)
                ▼
            DB Security Group (port 5432)
```

No IP addresses needed — each tier only accepts traffic from the tier above it.

---

## Scope and Constraints

- Security Groups are scoped to a **VPC** — cannot be reused across VPCs
- One instance can have **multiple Security Groups** (rules are merged)
- One Security Group can be attached to **multiple instances**
- Changes to Security Group rules take effect **immediately**
- A Security Group cannot be deleted while it is still attached to a resource

---

## CLI Operations

```bash
# Create a Security Group
aws ec2 create-security-group \
  --group-name web-sg \
  --description "Web server security group" \
  --vpc-id vpc-12345678

# Add inbound HTTPS rule
aws ec2 authorize-security-group-ingress \
  --group-id sg-12345678 \
  --protocol tcp \
  --port 443 \
  --cidr 0.0.0.0/0

# Add inbound rule using another Security Group as source
aws ec2 authorize-security-group-ingress \
  --group-id sg-db-12345678 \
  --protocol tcp \
  --port 5432 \
  --source-group sg-app-12345678

# Remove a rule
aws ec2 revoke-security-group-ingress \
  --group-id sg-12345678 \
  --protocol tcp \
  --port 22 \
  --cidr 0.0.0.0/0

# List Security Groups in a VPC
aws ec2 describe-security-groups \
  --filters Name=vpc-id,Values=vpc-12345678

# Describe rules for a specific group
aws ec2 describe-security-group-rules \
  --filters Name=group-id,Values=sg-12345678
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| **Timeout** when connecting | Inbound rule missing | Add the required inbound rule |
| **Connection refused** | Instance received traffic but app rejected it | App not running, wrong port, or app firewall |
| **Timeout on outbound** | Outbound rule blocked | Check outbound rules (or NACLs) |
| Instance can't reach internet | Outbound rule missing or NAT Gateway issue | Verify outbound `0.0.0.0/0` rule exists |

> **Key rule:** A timeout means the firewall blocked the traffic. A "connection refused" means the firewall allowed it but the application rejected it.

---

## Best Practices

- Use **Security Group IDs as sources** for inter-service communication, not CIDR ranges
- Maintain a **dedicated SSH Security Group** — keep it separate and tightly restricted
- Never use `0.0.0.0/0` on port 22 (SSH) or 3389 (RDP) in production
- Regularly audit Security Groups with **AWS Config** rules (`restricted-ssh`, `unrestricted-port`)
- Prefer the **principle of least privilege** — open only the ports that specific services need

---

## Summary

Security Groups are the primary instance-level network control mechanism in AWS. They are stateful, support only allow rules, and can reference other Security Groups — making it easy to build secure multi-tier architectures without hardcoding IP addresses. Pair them with NACLs (subnet-level) for a defense-in-depth approach.
