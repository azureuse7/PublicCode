# VPC: Amazon Virtual Private Cloud

> Amazon VPC lets you launch AWS resources inside a logically isolated virtual network that you define. You control the IP address range, subnets, route tables, internet gateways, NAT gateways, and security rules.

Amazon Virtual Private Cloud (Amazon VPC) lets you provision a logically isolated section of the AWS cloud where you launch resources in a virtual network you define. You have complete control over your networking environment — IP address ranges, subnets, route tables, and gateways.

By default, AWS creates a default VPC per account to get started quickly, but you should create dedicated VPCs for your own applications and projects.

---

## How Traffic Flows

When a user on the internet wants to reach an application inside a VPC:

```
Internet → Internet Gateway → Public Subnet → Load Balancer
                                                    ↓
                                          Route Table (defines path)
                                                    ↓
                                     Security Group (allow/deny)
                                                    ↓
                                           Private Subnet (app/DB)
```

![VPC traffic flow](https://github.com/iam-veeramalla/aws-devops-zero-to-hero/assets/43399466/12cc10b6-724c-42c9-b07b-d8a7ce124e24)

---

## VPC Components

### Virtual Private Cloud (VPC)

A virtual network that closely resembles a traditional data center network. You define the IP address range using CIDR notation (e.g., `10.0.0.0/16`), and all other resources live inside it.

### Subnets

A range of IP addresses within a VPC. Each subnet must reside in a single Availability Zone.

| Subnet Type | Internet Access | Typical Use |
|---|---|---|
| **Public** | Yes, via Internet Gateway | Load balancers, bastion hosts |
| **Private** | No direct access | App servers, databases |

### IP Addressing

You can assign both IPv4 and IPv6 addresses to VPCs and subnets, including bringing your own public IP ranges to AWS.

### Routing

**Route tables** determine where network traffic from a subnet or gateway is directed. Each subnet is associated with one route table.

### Gateways and Endpoints

| Gateway / Endpoint | Purpose |
|---|---|
| **Internet Gateway** | Connects a VPC to the public internet |
| **NAT Gateway** | Lets private subnet instances reach the internet without exposing them to inbound traffic |
| **Virtual Private Gateway** | Connects a VPC to an on-premises network via VPN |
| **Transit Gateway** | Central hub connecting multiple VPCs and on-premises networks |
| **Interface Endpoint** | Private connectivity to AWS services without internet traversal |
| **Gateway Endpoint** | Private route to S3 or DynamoDB |

### Security

| Mechanism | Level | Stateful? | Description |
|---|---|---|---|
| **Security Group** | Instance | Yes | Acts as a virtual firewall; rules allow traffic, implicit deny |
| **NACL** | Subnet | No | Explicit allow/deny rules; evaluated in order |

### VPC Flow Logs

Capture information about IP traffic going to and from network interfaces. Useful for security auditing and troubleshooting.

### VPC Peering

Connect two VPCs to route traffic between them using private IP addresses.

### Traffic Mirroring

Copy network traffic from network interfaces and send it to monitoring appliances for deep packet inspection.

### VPN Connections

Connect your VPC to on-premises networks using **AWS Virtual Private Network (AWS VPN)**.

---

## Common Use Cases

| Use Case | Description |
|---|---|
| **Web Applications** | Public-facing resources in public subnets; databases in private subnets |
| **Hybrid Cloud** | Extend on-premises network into AWS via VPN or AWS Direct Connect |
| **Data Processing** | Isolated environment for EC2, EMR, and analytics workloads |
| **Disaster Recovery** | Replicate on-premises environments into a VPC for failover |

---

## Creating a Simple VPC

### Using the AWS Management Console

1. Go to the **VPC Console** → **Your VPCs** → **Create VPC**
2. Enter a name and CIDR block (e.g., `10.0.0.0/16`), then click **Create VPC**
3. Go to **Subnets** → **Create Subnet**, choose your VPC, and define:
   - Public subnet: `10.0.1.0/24`
   - Private subnet: `10.0.2.0/24`
4. Go to **Internet Gateways** → **Create Internet Gateway**, then **Attach** it to your VPC
5. Go to **Route Tables**, select the route table for the public subnet, and add a route:
   - Destination: `0.0.0.0/0` → Target: your Internet Gateway
6. Go to **Security Groups** → create and configure rules for inbound/outbound traffic

### Using the AWS CLI

**1. Create a VPC:**

```sh
aws ec2 create-vpc --cidr-block 10.0.0.0/16
```

**2. Create subnets:**

```sh
aws ec2 create-subnet \
  --vpc-id vpc-12345678 \
  --cidr-block 10.0.1.0/24 \
  --availability-zone us-west-2a

aws ec2 create-subnet \
  --vpc-id vpc-12345678 \
  --cidr-block 10.0.2.0/24 \
  --availability-zone us-west-2b
```

**3. Create and attach an Internet Gateway:**

```sh
aws ec2 create-internet-gateway

aws ec2 attach-internet-gateway \
  --vpc-id vpc-12345678 \
  --internet-gateway-id igw-12345678
```

**4. Add a route to the Internet Gateway:**

```sh
aws ec2 create-route \
  --route-table-id rtb-12345678 \
  --destination-cidr-block 0.0.0.0/0 \
  --gateway-id igw-12345678
```

**5. Create a security group and allow SSH:**

```sh
aws ec2 create-security-group \
  --group-name my-sg \
  --description "My security group" \
  --vpc-id vpc-12345678

aws ec2 authorize-security-group-ingress \
  --group-id sg-12345678 \
  --protocol tcp \
  --port 22 \
  --cidr 0.0.0.0/0
```

---

## Summary

Amazon VPC gives you full control over your cloud network — from custom IP ranges and isolated subnets to fine-grained security rules and hybrid connectivity options. It is the networking foundation for virtually every AWS workload.

---

## Resources

- [VPC with servers in private subnets and NAT](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-example-private-subnets-nat.html)
- [AWS VPC Refresher](https://aws.plainenglish.io/aws-vpc-refresher-40ac90196ea8)
- [How to Create an AWS VPC in 10 Steps](https://varunmanik1.medium.com/how-to-create-aws-vpc-in-10-steps-less-than-5-min-a49ac12064aa)

![VPC with private subnets and NAT](https://github.com/iam-veeramalla/aws-devops-zero-to-hero/assets/43399466/89d8316e-7b70-4821-a6bf-67d1dcc4d2fb)
