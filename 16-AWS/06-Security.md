# AWS Security: Security Groups and NACLs

> AWS provides two complementary layers of network security: **Security Groups** (instance-level, stateful) and **Network Access Control Lists / NACLs** (subnet-level, stateless). Understanding when to use each — and how they interact — is essential for securing any AWS workload.

---

## Quick Comparison

| Feature | Security Group | NACL |
|---|---|---|
| **Operates at** | Instance level | Subnet level |
| **Stateful / Stateless** | Stateful | Stateless |
| **Rule types** | Allow only | Allow and Deny |
| **Rule evaluation** | All rules evaluated together | Rules evaluated in numeric order (lowest first) |
| **Applies to** | One or more instances | All resources in the subnet |
| **Default behavior** | Deny all inbound, allow all outbound | Allow all inbound and outbound |
| **Rule changes** | Take effect immediately | May take time to propagate |

---

## Security Groups

Security Groups act as virtual firewalls at the **instance level**, controlling inbound and outbound traffic for EC2 instances (and other resources like RDS, Lambda, etc.).

### Key Characteristics

- Each instance can be associated with **one or more** security groups
- Rules specify protocol, port range, and source/destination (IP, CIDR, or another security group ID)
- **Stateful** — if an inbound rule allows traffic, the return outbound traffic is automatically permitted (and vice versa)
- Only **allow** rules exist; there is no explicit deny
- Rule changes take effect **immediately**

### Inbound vs Outbound Rules

| Direction | Controls | Example Rule |
|---|---|---|
| **Inbound** | Traffic reaching the instance | Allow TCP port 22 from `10.0.0.0/8` (SSH from VPN) |
| **Outbound** | Traffic leaving the instance | Allow TCP port 443 to `0.0.0.0/0` (HTTPS to internet) |

### Example: Web Server Security Group

| Direction | Protocol | Port | Source / Destination | Purpose |
|---|---|---|---|---|
| Inbound | TCP | 80 | `0.0.0.0/0` | HTTP from internet |
| Inbound | TCP | 443 | `0.0.0.0/0` | HTTPS from internet |
| Inbound | TCP | 22 | `10.0.0.0/8` | SSH from internal network only |
| Outbound | All | All | `0.0.0.0/0` | Allow all outbound |

### CLI: Create and Configure a Security Group

```sh
# Create the security group
aws ec2 create-security-group \
  --group-name web-sg \
  --description "Web server security group" \
  --vpc-id vpc-12345678

# Allow inbound HTTP
aws ec2 authorize-security-group-ingress \
  --group-id sg-12345678 \
  --protocol tcp \
  --port 80 \
  --cidr 0.0.0.0/0

# Allow inbound HTTPS
aws ec2 authorize-security-group-ingress \
  --group-id sg-12345678 \
  --protocol tcp \
  --port 443 \
  --cidr 0.0.0.0/0

# Allow SSH from internal network only
aws ec2 authorize-security-group-ingress \
  --group-id sg-12345678 \
  --protocol tcp \
  --port 22 \
  --cidr 10.0.0.0/8
```

---

## Network Access Control Lists (NACLs)

NACLs provide an additional layer of security at the **subnet level**. Every subnet in a VPC must be associated with exactly one NACL (multiple subnets can share the same NACL).

### Key Characteristics

- Rules are numbered and **evaluated in ascending order** — the first matching rule wins
- Support both **Allow** and **Deny** rules (unlike Security Groups)
- **Stateless** — return traffic must be explicitly allowed by a separate outbound rule
- Each subnet can have only **one** NACL; one NACL can cover **many** subnets
- Rule changes may take time to propagate to all resources in the subnet

### Rule Structure

Each NACL rule defines:

| Field | Description |
|---|---|
| **Rule number** | Evaluation order (100, 200, …); use gaps to allow future insertions |
| **Protocol** | TCP, UDP, ICMP, or All |
| **Action** | Allow or Deny |
| **Source / Destination** | CIDR range (e.g., `0.0.0.0/0`) |
| **Port range** | Single port or range (e.g., `1024–65535` for ephemeral ports) |

### Example: Public Subnet NACL

| Rule # | Direction | Protocol | Port | Source / Destination | Action |
|---|---|---|---|---|---|
| 100 | Inbound | TCP | 80 | `0.0.0.0/0` | Allow |
| 110 | Inbound | TCP | 443 | `0.0.0.0/0` | Allow |
| 120 | Inbound | TCP | 1024–65535 | `0.0.0.0/0` | Allow (ephemeral ports) |
| * | Inbound | All | All | `0.0.0.0/0` | Deny |
| 100 | Outbound | TCP | 80 | `0.0.0.0/0` | Allow |
| 110 | Outbound | TCP | 443 | `0.0.0.0/0` | Allow |
| 120 | Outbound | TCP | 1024–65535 | `0.0.0.0/0` | Allow (ephemeral ports) |
| * | Outbound | All | All | `0.0.0.0/0` | Deny |

> **Note on ephemeral ports:** Because NACLs are stateless, you must explicitly allow the ephemeral port range (`1024–65535`) in both directions so that response traffic can flow back to clients.

### CLI: Create and Configure a NACL

```sh
# Create the NACL
aws ec2 create-network-acl --vpc-id vpc-12345678

# Allow inbound HTTP (rule 100)
aws ec2 create-network-acl-entry \
  --network-acl-id acl-12345678 \
  --rule-number 100 \
  --protocol tcp \
  --rule-action allow \
  --ingress \
  --cidr-block 0.0.0.0/0 \
  --port-range From=80,To=80

# Allow inbound ephemeral ports (rule 120)
aws ec2 create-network-acl-entry \
  --network-acl-id acl-12345678 \
  --rule-number 120 \
  --protocol tcp \
  --rule-action allow \
  --ingress \
  --cidr-block 0.0.0.0/0 \
  --port-range From=1024,To=65535
```

---

## Defense-in-Depth: Using Both Together

Security Groups and NACLs are complementary, not redundant. The recommended pattern is:

```
Internet
    │
    ▼
[ NACL ]  ← subnet-level: broad deny rules, block known bad CIDRs
    │
    ▼
[ Security Group ]  ← instance-level: precise allow rules per service
    │
    ▼
EC2 Instance
```

| Layer | Responsibility |
|---|---|
| **NACL** | Block entire IP ranges, add an explicit deny for known threats |
| **Security Group** | Allow only the specific ports/protocols each instance needs |

**Best practices:**
- Keep Security Group rules as specific as possible — avoid `0.0.0.0/0` on sensitive ports
- Use Security Group IDs as sources (instead of CIDRs) to allow traffic between tiers without hardcoding IPs
- Add deny rules in NACLs to block known malicious CIDR ranges at the subnet boundary
- Always allow ephemeral ports (`1024–65535`) in NACL outbound rules to avoid breaking response traffic

---

## Architecture Reference

![Security Groups and NACLs in a VPC](https://github.com/iam-veeramalla/aws-devops-zero-to-hero/assets/43399466/30bbc9e8-6502-438b-8adf-ece8b81edce9)
