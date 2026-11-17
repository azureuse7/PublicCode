# Amazon Lightsail: Simplified Cloud Platform

> Lightsail is Amazon's simplified VPS product. It bundles compute, SSD storage, networking, and a static IP into predictable flat-rate monthly plans — ideal for developers, small businesses, and anyone who wants cloud hosting without AWS complexity.

---

## Lightsail vs EC2

| | Lightsail | EC2 |
|---|---|---|
| Target audience | Beginners, small projects | Advanced / enterprise workloads |
| Pricing | Fixed monthly plan | Variable (by type, usage, storage) |
| Setup complexity | Simple — pre-configured blueprints | Full control — configure everything |
| Networking | Simplified (built-in static IP, DNS) | Full VPC control |
| Scalability | Limited (peering to full AWS via VPC) | Unlimited instance types and scaling |
| Best for | Blogs, small sites, dev/test | Production, complex architectures |

---

## Key Features

| Feature | Description |
|---|---|
| **Predictable pricing** | Fixed monthly cost — compute, storage, and data transfer bundled |
| **Blueprints** | Pre-configured stacks: WordPress, LAMP, MEAN, Nginx, Node.js, and more |
| **Static IP** | Free static IPv4 address attached to your instance |
| **DNS management** | Built-in DNS zone management without Route 53 setup |
| **SSD block storage** | Attach additional volumes for expanded storage |
| **Snapshots** | Full instance snapshots for backup or clone |
| **Managed databases** | MySQL, PostgreSQL, MariaDB — automated backups + maintenance |
| **VPC peering** | Connect Lightsail to full AWS services in a VPC |
| **Load balancer** | Simple HTTP/HTTPS load balancer across multiple instances |

---

## Instance Plans (sample)

| Plan | RAM | vCPU | SSD | Transfer | Price |
|---|---|---|---|---|---|
| Nano | 512 MB | 2 | 20 GB | 1 TB | ~$3.50/mo |
| Micro | 1 GB | 2 | 40 GB | 2 TB | ~$5/mo |
| Small | 2 GB | 1 | 60 GB | 3 TB | ~$10/mo |
| Medium | 4 GB | 2 | 80 GB | 4 TB | ~$20/mo |
| Large | 8 GB | 2 | 160 GB | 5 TB | ~$40/mo |

---

## Common Use Cases

| Use Case | Description |
|---|---|
| **Personal blogs** | WordPress or Ghost with a few clicks |
| **Small business websites** | Static + CMS sites without DevOps overhead |
| **Dev/test environments** | Spin up a clean environment quickly, delete when done |
| **Prototyping** | Validate an idea without committing to full EC2 setup |
| **Simple APIs** | Node.js or Python API on a single instance |

---

## Launching a WordPress Instance

### Using the Lightsail Console

1. Log in to AWS → open the **Amazon Lightsail** console
2. Click **Create instance**
3. Choose **Linux/Unix** platform
4. Select **WordPress** blueprint
5. Choose a plan (e.g., $5/month)
6. Name your instance (e.g., `my-wordpress`)
7. Click **Create instance** — it launches in under a minute

### Accessing Your WordPress Site

1. In the Lightsail console, go to your instance → **Connect** tab
2. Connect via browser-based SSH or download the SSH key
3. Retrieve the default admin password:

```bash
cat /home/bitnami/bitnami_credentials
```

4. Open your instance's **public IP** in a browser → WordPress login at `/wp-admin`

---

## Using the Lightsail CLI

```bash
# List all instances
aws lightsail get-instances

# Create an instance from a blueprint
aws lightsail create-instances \
  --instance-names my-wordpress \
  --availability-zone us-east-1a \
  --blueprint-id wordpress \
  --bundle-id micro_2_0

# Allocate a static IP
aws lightsail allocate-static-ip --static-ip-name my-static-ip

# Attach static IP to instance
aws lightsail attach-static-ip \
  --static-ip-name my-static-ip \
  --instance-name my-wordpress

# Create a snapshot (backup)
aws lightsail create-instance-snapshot \
  --instance-name my-wordpress \
  --instance-snapshot-name my-wordpress-backup

# Delete an instance
aws lightsail delete-instance --instance-name my-wordpress
```

---

## Connecting Lightsail to Full AWS Services

Lightsail instances live outside your main AWS VPC by default. To access RDS, ElastiCache, or other VPC resources:

1. Go to Lightsail console → **Account** → **VPC peering**
2. Enable VPC peering for the region
3. Your Lightsail instances can now reach resources in the default VPC via private IPs

---

## Summary

Lightsail is the fastest path to a running web server or application on AWS. It trades configurability for simplicity and predictable pricing. When your project outgrows Lightsail, you can snapshot your instance and migrate to EC2 — or use VPC peering to gradually adopt full AWS services.
