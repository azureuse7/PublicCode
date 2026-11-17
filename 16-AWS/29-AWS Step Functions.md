# AWS Step Functions: Serverless Workflow Orchestration

> Step Functions is a serverless orchestration service that lets you coordinate multiple AWS services into reliable, visual workflows (state machines). It handles retries, parallel execution, error handling, and state transitions automatically — so you focus on business logic, not plumbing.

---

## How Step Functions Works

```
Start Execution
      │
      ▼
State 1: Task (invoke Lambda / call API)
      │
      ├── Success ──► State 2: Choice (branch on result)
      │                    │
      │               "PURCHASE" ──► State 3a: ProcessPurchase Task
      │               "REFUND"   ──► State 3b: ProcessRefund Task
      │                                   │
      └── Failure ──► State 4: Fail (log error + end)
                                          │
                                          ▼
                                    State 5: Succeed (end)
```

---

## Workflow Types

| Type | Model | Max Duration | Use For |
|---|---|---|---|
| **Standard** | Exactly-once execution | 1 year | Long-running, auditable workflows |
| **Express** | At-least-once execution | 5 minutes | High-volume, short-duration workflows |

---

## State Types (Amazon States Language)

| State | Purpose |
|---|---|
| **Task** | Calls a service (Lambda, ECS, SQS, DynamoDB, HTTP endpoint, etc.) |
| **Choice** | Conditional branching based on input data |
| **Parallel** | Executes multiple branches simultaneously |
| **Map** | Iterates over an array, processing each element |
| **Wait** | Pauses for a fixed duration or until a timestamp |
| **Pass** | Passes input to output (no action); useful for testing |
| **Succeed** | Ends the execution successfully |
| **Fail** | Ends the execution with an error |

---

## Amazon States Language (ASL)

State machines are defined in JSON using ASL.

### Example: User Registration Workflow

```json
{
  "Comment": "User registration: create user → send welcome email → finalize",
  "StartAt": "CreateUser",
  "States": {
    "CreateUser": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:us-east-1:123456789012:function:CreateUser",
      "Next": "SendWelcomeEmail",
      "Retry": [{
        "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException"],
        "IntervalSeconds": 2,
        "MaxAttempts": 3,
        "BackoffRate": 2
      }],
      "Catch": [{
        "ErrorEquals": ["States.ALL"],
        "Next": "HandleFailure",
        "ResultPath": "$.error"
      }]
    },
    "SendWelcomeEmail": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:us-east-1:123456789012:function:SendWelcomeEmail",
      "Next": "FinalizeRegistration",
      "Catch": [{
        "ErrorEquals": ["States.ALL"],
        "Next": "HandleFailure",
        "ResultPath": "$.error"
      }]
    },
    "FinalizeRegistration": {
      "Type": "Succeed"
    },
    "HandleFailure": {
      "Type": "Fail",
      "Error": "RegistrationError",
      "Cause": "An error occurred during user registration."
    }
  }
}
```

---

## Key ASL Features

### Retry

Automatically retry a failed Task state:

```json
"Retry": [{
  "ErrorEquals": ["States.TaskFailed"],
  "IntervalSeconds": 1,
  "MaxAttempts": 3,
  "BackoffRate": 2.0
}]
```

### Catch

Route to an error-handling state when retries are exhausted:

```json
"Catch": [{
  "ErrorEquals": ["States.ALL"],
  "Next": "ErrorHandler",
  "ResultPath": "$.errorInfo"
}]
```

### Parallel State

Execute multiple branches at the same time:

```json
"ProcessInParallel": {
  "Type": "Parallel",
  "Branches": [
    {
      "StartAt": "SendEmail",
      "States": {
        "SendEmail": { "Type": "Task", "Resource": "...", "End": true }
      }
    },
    {
      "StartAt": "UpdateDatabase",
      "States": {
        "UpdateDatabase": { "Type": "Task", "Resource": "...", "End": true }
      }
    }
  ],
  "Next": "Done"
}
```

### Map State

Process each item in an array:

```json
"ProcessOrders": {
  "Type": "Map",
  "ItemsPath": "$.orders",
  "MaxConcurrency": 5,
  "Iterator": {
    "StartAt": "ProcessSingleOrder",
    "States": {
      "ProcessSingleOrder": {
        "Type": "Task",
        "Resource": "arn:aws:lambda:...",
        "End": true
      }
    }
  },
  "Next": "Done"
}
```

---

## Deploying a State Machine

### Using the AWS CLI

```bash
# Create the state machine
aws stepfunctions create-state-machine \
  --name UserRegistrationWorkflow \
  --definition file://state-machine.json \
  --role-arn arn:aws:iam::123456789012:role/StepFunctionsRole \
  --type STANDARD

# Start an execution
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:123456789012:stateMachine:UserRegistrationWorkflow \
  --input '{"userId": "user-123", "email": "alice@example.com"}'

# Describe an execution (check status)
aws stepfunctions describe-execution \
  --execution-arn arn:aws:states:us-east-1:123456789012:execution:UserRegistrationWorkflow:abc-123

# List executions
aws stepfunctions list-executions \
  --state-machine-arn arn:aws:states:us-east-1:123456789012:stateMachine:UserRegistrationWorkflow \
  --status-filter RUNNING
```

---

## IAM Role for Step Functions

Step Functions needs permissions to invoke the services it calls:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": "arn:aws:lambda:us-east-1:123456789012:function:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogDelivery",
        "logs:PutLogEvents"
      ],
      "Resource": "*"
    }
  ]
}
```

---

## Common Use Cases

| Use Case | Pattern |
|---|---|
| **Microservices orchestration** | Chain Lambda functions with error handling |
| **ETL pipelines** | Map state over data batches, parallel transforms |
| **Order processing** | Sequential steps: validate → charge → fulfil → notify |
| **ML workflows** | Preprocess → train → evaluate → deploy |
| **Human approval flows** | Wait for a callback token (`.waitForTaskToken`) |
| **Scheduled batch jobs** | EventBridge cron → Step Functions → multiple tasks |

---

## Step Functions vs Lambda Direct Chaining

| | Step Functions | Lambda → Lambda directly |
|---|---|---|
| Visibility | Visual console, execution history | None |
| Error handling | Built-in Retry + Catch per state | Manual try/except in code |
| Retries | Configurable per state | Manual |
| Long-running flows | Up to 1 year | Max 15 min per Lambda |
| Cost | Per state transition | Per Lambda invocation |

---

## Summary

Use Step Functions whenever you need to orchestrate multiple services in a sequence, with branching, retries, or parallel execution. The visual workflow console makes it easy to monitor executions and debug failures. For short, high-volume workflows, use **Express Workflows** to reduce cost.
