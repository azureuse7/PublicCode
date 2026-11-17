# Step Functions + Lambda: Transaction Router

> This guide demonstrates a practical Step Functions state machine that uses a **Choice** state to route transactions to different Lambda handlers based on a `TransactionType` field.

**Reference:** [AWS Step Functions and Lambda Tutorial](https://www.youtube.com/watch?v=s0XFX3WHg0w)

---

## Architecture

```
Input: { "TransactionType": "PURCHASE" | "REFUND" }
    │
    ▼
ProcessTransaction (Choice state)
    │
    ├── TransactionType == "PURCHASE" ──► ProcessPurchase (Lambda)
    │                                           │
    └── TransactionType == "REFUND"  ──► ProcessRefund (Lambda)
                                                │
                                                ▼
                                            End (success)
```

---

## Step 1: Create the Lambda Functions

### ProcessPurchase Lambda

Create a new Lambda function named `ProcessPurchase` with the following code:

```python
import json
import datetime

def lambda_handler(message, context):
    print("Received message from Step Functions:", message)

    return {
        'TransactionType': message['TransactionType'],
        'Timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'Message': 'Purchase processed successfully'
    }
```

### ProcessRefund Lambda

Create a second Lambda function named `ProcessRefund`:

```python
import json
import datetime

def lambda_handler(message, context):
    print("Received message from Step Functions:", message)

    return {
        'TransactionType': message['TransactionType'],
        'Timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'Message': 'Refund processed successfully'
    }
```

---

## Step 2: Create the IAM Role

Create an IAM role that grants Step Functions permission to invoke both Lambda functions.

**Trust policy** (allow Step Functions to assume the role):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "states.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

**Permissions policy** (allow invoking both functions):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "lambda:InvokeFunction",
    "Resource": [
      "arn:aws:lambda:REGION:ACCOUNT_ID:function:ProcessPurchase",
      "arn:aws:lambda:REGION:ACCOUNT_ID:function:ProcessRefund"
    ]
  }]
}
```

```bash
# Create the role
aws iam create-role \
  --role-name StepFunctionsTransactionRole \
  --assume-role-policy-document file://trust-policy.json

# Attach the Lambda invoke policy
aws iam put-role-policy \
  --role-name StepFunctionsTransactionRole \
  --policy-name LambdaInvokePolicy \
  --policy-document file://permissions-policy.json
```

---

## Step 3: Create the State Machine

Copy both Lambda ARNs from the Lambda console (Configuration → Function ARN) and replace the placeholders below.

```json
{
  "Comment": "Routes transactions to the correct Lambda based on TransactionType",
  "StartAt": "ProcessTransaction",
  "States": {
    "ProcessTransaction": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.TransactionType",
          "StringEquals": "PURCHASE",
          "Next": "ProcessPurchase"
        },
        {
          "Variable": "$.TransactionType",
          "StringEquals": "REFUND",
          "Next": "ProcessRefund"
        }
      ],
      "Default": "UnknownTransactionType"
    },
    "ProcessPurchase": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:REGION:ACCOUNT_ID:function:ProcessPurchase",
      "End": true
    },
    "ProcessRefund": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:REGION:ACCOUNT_ID:function:ProcessRefund",
      "End": true
    },
    "UnknownTransactionType": {
      "Type": "Fail",
      "Error": "UnknownTransactionType",
      "Cause": "TransactionType must be PURCHASE or REFUND"
    }
  }
}
```

Deploy via AWS CLI:

```bash
aws stepfunctions create-state-machine \
  --name TransactionRouter \
  --definition file://state-machine.json \
  --role-arn arn:aws:iam::ACCOUNT_ID:role/StepFunctionsTransactionRole \
  --type STANDARD
```

---

## Step 4: Test the State Machine

**Test a PURCHASE:**

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:REGION:ACCOUNT_ID:stateMachine:TransactionRouter \
  --input '{"TransactionType": "PURCHASE", "Amount": 99.99, "CustomerId": "cust-123"}'
```

**Test a REFUND:**

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:REGION:ACCOUNT_ID:stateMachine:TransactionRouter \
  --input '{"TransactionType": "REFUND", "Amount": 49.99, "OrderId": "order-456"}'
```

**Check execution status:**

```bash
aws stepfunctions list-executions \
  --state-machine-arn arn:aws:states:REGION:ACCOUNT_ID:stateMachine:TransactionRouter
```

---

## Monitoring

In the **Step Functions console**:
- Click on the state machine → **Executions** tab
- Click any execution to see the visual workflow with each state highlighted (green = success, red = failure)
- View input/output at each state and any error details

CloudWatch metrics are automatically published:
- `ExecutionsStarted`, `ExecutionsSucceeded`, `ExecutionsFailed`
- `ExecutionTime` — total duration of each execution

---

## Extending the Example

| Enhancement | How |
|---|---|
| Add error handling | Add `Catch` + `Retry` to each Task state |
| Add a third type | Add another `Choice` branch + Lambda |
| Notify on failure | Add a `Catch` → SNS `Task` state |
| Add an approval step | Use `.waitForTaskToken` in a Task state |
| Run in parallel | Wrap both handlers in a `Parallel` state |
