# AWS Lambda: Serverless Functions

> Lambda lets you run code without managing servers. Upload your function, configure a trigger, and AWS handles execution, automatic scaling, and billing per invocation — down to the millisecond.

---

## How Lambda Works

```
Trigger (event source)
    │
    ▼
Lambda Service ──► Spin up execution environment
    │               (container with your runtime + code)
    ▼
Your function code runs
    │
    ▼
Response / output sent to caller or next service
    │
    ▼
Container kept warm (reuse) or terminated (idle)
```

---

## Key Features

| Feature | Detail |
|---|---|
| **No servers** | AWS provisions and manages all compute |
| **Auto-scaling** | Scales from 0 to thousands of concurrent executions automatically |
| **Event-driven** | Triggered by S3, DynamoDB Streams, SQS, SNS, API Gateway, EventBridge, and more |
| **Pay per use** | Billed per request count + duration (ms); 1M free requests/month included |
| **Max timeout** | 15 minutes per invocation |
| **Memory** | 128 MB to 10,240 MB; CPU scales proportionally with memory |
| **Runtimes** | Node.js, Python, Java, Go, Ruby, .NET, and custom runtimes |
| **Layers** | Share libraries and dependencies across functions |
| **VPC support** | Run Lambda inside a VPC to access private resources |

---

## Supported Triggers

| Trigger | Use Case |
|---|---|
| **API Gateway / Function URL** | HTTP endpoint — REST or HTTP API |
| **S3 Events** | Process files on upload (resize images, parse CSV) |
| **DynamoDB Streams** | React to table changes in real time |
| **SQS** | Process messages from a queue (batch processing) |
| **SNS** | Fan-out notifications to functions |
| **EventBridge** | Scheduled tasks (cron), AWS service events |
| **Kinesis** | Real-time stream processing |
| **Cognito** | Custom auth flows, pre/post signup triggers |
| **CloudFront (Lambda@Edge)** | Run code at edge locations |

---

## Common Use Cases

| Use Case | Example |
|---|---|
| **Serverless APIs** | API Gateway → Lambda → DynamoDB |
| **File processing** | S3 upload → Lambda resizes image → save to output bucket |
| **Scheduled jobs** | EventBridge cron → Lambda → cleanup task |
| **Data pipelines** | Kinesis stream → Lambda → Elasticsearch |
| **Real-time notifications** | DynamoDB Streams → Lambda → SNS push |
| **Infrastructure automation** | CloudWatch Alarm → Lambda → auto-remediation |

---

## Creating a Lambda Function

### Using the AWS Management Console

1. Open **Lambda** → **Create function**
2. Choose **Author from scratch**
3. Set the function name, runtime (e.g., Python 3.12), and execution role
4. Write or paste your code in the inline editor
5. Click **Deploy**, then **Test** with a sample event

### Using the AWS CLI

**Step 1: Write your function**

```python
# lambda_function.py
def lambda_handler(event, context):
    name = event.get('name', 'World')
    return {
        'statusCode': 200,
        'body': f'Hello, {name}!'
    }
```

**Step 2: Package and deploy**

```bash
zip function.zip lambda_function.py

aws lambda create-function \
  --function-name HelloFunction \
  --runtime python3.12 \
  --zip-file fileb://function.zip \
  --handler lambda_function.lambda_handler \
  --role arn:aws:iam::123456789012:role/lambda-execution-role
```

**Step 3: Invoke and test**

```bash
aws lambda invoke \
  --function-name HelloFunction \
  --payload '{"name": "Alice"}' \
  --cli-binary-format raw-in-base64-out \
  output.json

cat output.json
# {"statusCode": 200, "body": "Hello, Alice!"}
```

**Update function code**

```bash
zip function.zip lambda_function.py

aws lambda update-function-code \
  --function-name HelloFunction \
  --zip-file fileb://function.zip
```

---

## Environment Variables and Secrets

```bash
# Set environment variables
aws lambda update-function-configuration \
  --function-name HelloFunction \
  --environment Variables={DB_HOST=mydb.cluster.local,DB_PORT=5432}
```

For sensitive values, retrieve from SSM Parameter Store or Secrets Manager inside the function:

```python
import boto3

ssm = boto3.client('ssm')
db_password = ssm.get_parameter(
    Name='/myapp/db_password',
    WithDecryption=True
)['Parameter']['Value']
```

---

## Concurrency and Throttling

| Setting | Description |
|---|---|
| **Unreserved concurrency** | Default pool shared across all functions in the account |
| **Reserved concurrency** | Guarantees a specific number of concurrent executions for a function |
| **Provisioned concurrency** | Pre-warms containers to eliminate cold starts for latency-sensitive functions |

```bash
# Set reserved concurrency
aws lambda put-function-concurrency \
  --function-name HelloFunction \
  --reserved-concurrent-executions 100
```

---

## Lambda IAM Execution Role

The execution role defines what AWS resources the function can access.

Minimum role trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "lambda.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

Attach `AWSLambdaBasicExecutionRole` for CloudWatch Logs access, then add service-specific policies as needed.

---

## Summary

Lambda is the simplest way to run event-driven code on AWS. It removes all infrastructure concerns, scales automatically, and costs nothing when idle. Design Lambda functions to be stateless and short-lived — offload state to DynamoDB, S3, or ElastiCache, and orchestrate multi-step workflows with Step Functions.
