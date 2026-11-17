# Amazon RDS: Managed Relational Database Service

> Amazon RDS handles provisioning, patching, backups, monitoring, and scaling for relational databases — so you focus on your application, not the engine. Supports Aurora, PostgreSQL, MySQL, MariaDB, Oracle, and SQL Server.

---

## Supported Database Engines

| Engine | Notes |
|---|---|
| **Amazon Aurora (MySQL)** | Up to 5× faster than MySQL; serverless option available |
| **Amazon Aurora (PostgreSQL)** | Up to 3× faster than PostgreSQL |
| **PostgreSQL** | Popular open-source; strong JSON and extension support |
| **MySQL** | Most widely used open-source RDBMS |
| **MariaDB** | MySQL-compatible, open-source |
| **Oracle** | Enterprise licensing; bring-your-own or license-included |
| **SQL Server** | Microsoft; Express, Web, Standard, Enterprise editions |

---

## Key Features

| Feature | Description |
|---|---|
| **Automated backups** | Daily snapshots + transaction logs; PITR up to 35 days |
| **Multi-AZ** | Synchronous standby replica in a second AZ; automatic failover |
| **Read Replicas** | Asynchronous copies for read-heavy workloads (up to 15 for Aurora) |
| **Storage Auto Scaling** | Automatically expands storage when capacity is low |
| **Encryption** | At rest (KMS) and in transit (TLS/SSL) |
| **IAM authentication** | Connect to MySQL/PostgreSQL with IAM token instead of password |
| **Enhanced Monitoring** | OS-level metrics (CPU, memory, IOPS) at 1-second granularity |
| **Performance Insights** | SQL-level performance analysis — identify slow queries |
| **Maintenance windows** | Patches and minor upgrades applied during your scheduled window |

---

## Multi-AZ vs Read Replicas

| | Multi-AZ | Read Replica |
|---|---|---|
| **Purpose** | High availability / failover | Read scalability |
| **Replication** | Synchronous | Asynchronous |
| **Can be promoted** | Yes (automatic on failure) | Yes (manual) |
| **Serves reads** | No (standby is passive) | Yes |
| **Cross-region** | No | Yes |

---

## Storage Types

| Type | IOPS | Use For |
|---|---|---|
| **gp3** (General Purpose SSD) | 3,000–16,000 | Most workloads — best price/performance |
| **io1 / io2** (Provisioned IOPS) | Up to 256,000 | IOPS-intensive: OLTP, large databases |
| **Magnetic** (standard) | Low | Legacy only — avoid for new deployments |

---

## Creating an RDS Instance

### Using the AWS Management Console

1. Open **RDS** → **Create database**
2. Choose **Standard create** → select engine (e.g., PostgreSQL)
3. Select a template: **Production**, **Dev/Test**, or **Free tier**
4. Configure:
   - DB instance identifier (e.g., `myapp-db`)
   - Master username and password
   - Instance class (e.g., `db.t3.micro` for dev, `db.m6g.large` for production)
   - Storage type and size
   - Multi-AZ: enable for production
5. Configure connectivity: VPC, subnet group, public access, security group
6. Set backup retention, maintenance window, and encryption
7. Click **Create database**

### Using the AWS CLI

```bash
aws rds create-db-instance \
  --db-instance-identifier myapp-db \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --engine-version 15.4 \
  --allocated-storage 20 \
  --storage-type gp3 \
  --master-username admin \
  --master-user-password 'S3cr3tPassw0rd!' \
  --db-name myappdb \
  --vpc-security-group-ids sg-12345678 \
  --db-subnet-group-name my-subnet-group \
  --backup-retention-period 7 \
  --multi-az \
  --no-publicly-accessible \
  --storage-encrypted
```

### Wait for the instance to be available

```bash
aws rds wait db-instance-available \
  --db-instance-identifier myapp-db

# Get the endpoint
aws rds describe-db-instances \
  --db-instance-identifier myapp-db \
  --query 'DBInstances[0].Endpoint.Address' \
  --output text
```

---

## Common CLI Operations

```bash
# Create a manual snapshot
aws rds create-db-snapshot \
  --db-instance-identifier myapp-db \
  --db-snapshot-identifier myapp-db-snapshot-20240101

# Create a read replica
aws rds create-db-instance-read-replica \
  --db-instance-identifier myapp-db-replica \
  --source-db-instance-identifier myapp-db \
  --db-instance-class db.t3.micro

# Modify instance (e.g. change class)
aws rds modify-db-instance \
  --db-instance-identifier myapp-db \
  --db-instance-class db.m6g.large \
  --apply-immediately

# Delete instance (keep final snapshot)
aws rds delete-db-instance \
  --db-instance-identifier myapp-db \
  --final-db-snapshot-identifier myapp-db-final-snapshot
```

---

## Security Best Practices

- Place RDS in **private subnets** — no public accessibility
- Use **security groups** to allow only your app servers (not `0.0.0.0/0`)
- Enable **storage encryption** at creation (cannot be changed later)
- Use **IAM database authentication** instead of static passwords where possible
- Rotate master passwords with **AWS Secrets Manager** (RDS native integration)
- Enable **deletion protection** on production databases

---

## Common Use Cases

| Use Case | Recommended Engine |
|---|---|
| Web and mobile apps | PostgreSQL, MySQL, Aurora MySQL |
| High-performance OLTP | Aurora, io2 provisioned IOPS |
| Enterprise ERP/CRM | Oracle, SQL Server |
| SaaS multi-tenant | PostgreSQL (schema isolation per tenant) |
| Analytics / reporting | Aurora read replicas, or migrate to Redshift |

---

## Summary

RDS takes the heavy lifting out of running a relational database in production. Enable Multi-AZ for zero-downtime failover, read replicas for read scaling, and automated backups with PITR for disaster recovery. For the highest performance and scale, consider Amazon Aurora, which offers native cloud-optimised storage at a fraction of the operational cost of running your own database.
