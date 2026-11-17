# Amazon DynamoDB: Managed NoSQL Database

> DynamoDB is a fully managed, serverless NoSQL database delivering single-digit millisecond performance at any scale. It supports key-value and document data models and is the go-to choice for high-traffic applications, gaming leaderboards, IoT, and session management.

---

## Key Features

| Feature | Description |
|---|---|
| **Fully managed** | No servers to provision, patch, or back up |
| **Serverless** | Scales automatically; pay per request (on-demand) or provision capacity |
| **Performance** | Single-digit millisecond reads/writes at any scale |
| **Global Tables** | Multi-region, active-active replication |
| **DynamoDB Streams** | Change data capture — trigger Lambda on inserts/updates/deletes |
| **TTL** | Automatically expire and delete items after a set timestamp |
| **Backup** | On-demand and continuous PITR (Point-in-Time Recovery) backups |
| **Encryption** | At rest via AWS KMS; in transit via TLS |

---

## Core Concepts

### Data Model

```
Table
 └── Items (rows)
       └── Attributes (columns) — schema-less except for the primary key
```

### Primary Keys

| Type | Composition | Use when |
|---|---|---|
| **Partition key** | Single attribute (e.g., `userId`) | Each `userId` is unique |
| **Composite key** | Partition key + Sort key (e.g., `userId` + `timestamp`) | Query ranges within a partition |

### Secondary Indexes

| Index | Partition Key | Sort Key | Scope |
|---|---|---|---|
| **GSI** (Global Secondary Index) | Any attribute | Any attribute | Entire table — separate read/write capacity |
| **LSI** (Local Secondary Index) | Same as table | Different from table | Same partition only — must be defined at creation |

### Capacity Modes

| Mode | How it works | Best for |
|---|---|---|
| **On-Demand** | Pay per request; auto-scales instantly | Unpredictable traffic |
| **Provisioned** | Set RCU/WCU; auto-scaling available | Predictable, high-volume traffic |

---

## Common Use Cases

| Use Case | Why DynamoDB |
|---|---|
| Session / user profile stores | Sub-millisecond lookups by `userId` |
| Gaming leaderboards | Sort by score with a composite key |
| IoT telemetry | Handles millions of writes/sec |
| Shopping carts / order state | Flexible schema, TTL for abandoned carts |
| Serverless backends | Direct Lambda integration, no connection pool overhead |

---

## CLI Operations

### Create a Table

```bash
aws dynamodb create-table \
  --table-name Orders \
  --attribute-definitions \
    AttributeName=userId,AttributeType=S \
    AttributeName=orderId,AttributeType=S \
  --key-schema \
    AttributeName=userId,KeyType=HASH \
    AttributeName=orderId,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST
```

### Put an Item

```bash
aws dynamodb put-item \
  --table-name Orders \
  --item '{
    "userId": {"S": "user-123"},
    "orderId": {"S": "order-456"},
    "status": {"S": "PENDING"},
    "amount": {"N": "99.99"}
  }'
```

### Query Items (by partition key)

```bash
aws dynamodb query \
  --table-name Orders \
  --key-condition-expression "userId = :uid" \
  --expression-attribute-values '{":uid": {"S": "user-123"}}'
```

### Query with Sort Key Range

```bash
aws dynamodb query \
  --table-name Orders \
  --key-condition-expression "userId = :uid AND orderId BETWEEN :start AND :end" \
  --expression-attribute-values '{
    ":uid":   {"S": "user-123"},
    ":start": {"S": "order-100"},
    ":end":   {"S": "order-999"}
  }'
```

### Scan (full table — use sparingly)

```bash
aws dynamodb scan \
  --table-name Orders \
  --filter-expression "status = :s" \
  --expression-attribute-values '{":s": {"S": "PENDING"}}'
```

### Delete an Item

```bash
aws dynamodb delete-item \
  --table-name Orders \
  --key '{
    "userId": {"S": "user-123"},
    "orderId": {"S": "order-456"}
  }'
```

---

## DynamoDB Streams + Lambda

Enable Streams on a table to trigger a Lambda function on every change:

```bash
# Enable streams
aws dynamodb update-table \
  --table-name Orders \
  --stream-specification StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES
```

Then create an Event Source Mapping to connect the stream to your Lambda:

```bash
aws lambda create-event-source-mapping \
  --function-name ProcessOrderChanges \
  --event-source-arn arn:aws:dynamodb:us-east-1:123456789012:table/Orders/stream/2024-01-01T00:00:00.000 \
  --starting-position LATEST
```

---

## Query vs Scan

| | Query | Scan |
|---|---|---|
| Searches by | Primary key or index | All items in table |
| Cost | Low — reads only matching partition | High — reads entire table |
| Speed | Fast | Slow (increases with table size) |
| Use when | You know the partition key | Avoid in production; use for small tables or migrations |

---

## Summary

DynamoDB is purpose-built for applications that need predictable, fast performance at any scale. Design your table access patterns first — DynamoDB rewards intentional key design. Use GSIs for alternate access patterns, Streams for event-driven workflows, and TTL to automatically clean up expired data.
