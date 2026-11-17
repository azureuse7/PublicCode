# AWS Fargate: Serverless Container Compute

> Fargate is a serverless compute engine for containers that works with both ECS and EKS. You define CPU, memory, and container image — Fargate provisions, runs, scales, and retires the underlying infrastructure automatically. No EC2 nodes to manage.

---

## Fargate vs EC2 Launch Type

| | Fargate | EC2 Launch Type |
|---|---|---|
| Server management | None — AWS manages it | You manage EC2 instances |
| Scaling | Per-task scaling | Cluster + task scaling |
| Isolation | Each task/pod gets its own kernel VM | Shared EC2 host |
| Pricing | Per vCPU/GB-memory per second | EC2 instance cost |
| Best for | Variable workloads, simplicity | Cost optimisation at high, steady load |

---

## How Fargate Works

```
You define:
  - Container image (ECR, Docker Hub)
  - CPU + Memory allocation
  - Network (VPC, subnet, security group)
  - IAM Task Role (permissions for the container)

AWS handles:
  - Provisioning the compute
  - OS patching
  - Placement and scaling
  - Retiring the compute when the task stops
```

---

## Key Features

| Feature | Description |
|---|---|
| **Serverless** | No EC2 instances to provision, patch, or scale |
| **ECS + EKS integration** | Works as a launch type in ECS and as a Fargate profile in EKS |
| **Per-task isolation** | Each task runs in its own isolated kernel (not a shared host) |
| **Right-sizing** | Specify exact CPU (0.25–16 vCPU) and memory (0.5–120 GB) per task |
| **VPC networking** | Each task gets an ENI and a private IP in your VPC |
| **IAM Task Role** | Grant AWS permissions directly to a container (no instance role needed) |

---

## Common Use Cases

| Use Case | Why Fargate |
|---|---|
| **Microservices** | Independent scaling per service, no node management |
| **Batch processing** | Spin up on-demand, pay only while running |
| **CI/CD runners** | Ephemeral task containers, clean environment per run |
| **Web APIs** | Auto-scale from 0 to N replicas without node management |
| **Scheduled jobs** | Use ECS Scheduled Tasks + EventBridge cron |

---

## Example: Running a Task on ECS Fargate

### Step 1: Create a Task Definition

```json
{
  "family": "my-api-task",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "arn:aws:iam::123456789012:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::123456789012:role/myTaskRole",
  "containerDefinitions": [
    {
      "name": "my-api",
      "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-api:latest",
      "essential": true,
      "portMappings": [
        {
          "containerPort": 8080,
          "protocol": "tcp"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/my-api",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      }
    }
  ]
}
```

Register it:

```bash
aws ecs register-task-definition --cli-input-json file://task-definition.json
```

### Step 2: Create an ECS Cluster

```bash
aws ecs create-cluster --cluster-name my-fargate-cluster
```

### Step 3: Create a Service (long-running tasks)

```bash
aws ecs create-service \
  --cluster my-fargate-cluster \
  --service-name my-api-service \
  --task-definition my-api-task \
  --desired-count 2 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={
    subnets=[subnet-12345678,subnet-23456789],
    securityGroups=[sg-12345678],
    assignPublicIp=ENABLED
  }"
```

### Step 4: Run a One-Off Task (batch job)

```bash
aws ecs run-task \
  --cluster my-fargate-cluster \
  --launch-type FARGATE \
  --task-definition my-api-task \
  --network-configuration "awsvpcConfiguration={
    subnets=[subnet-12345678],
    securityGroups=[sg-12345678],
    assignPublicIp=ENABLED
  }"
```

---

## Fargate on EKS

Create a Fargate profile to run specific Kubernetes pods serverlessly:

```bash
eksctl create fargateprofile \
  --cluster my-eks-cluster \
  --region us-east-1 \
  --name my-fargate-profile \
  --namespace my-namespace
```

Pods in `my-namespace` will now run on Fargate — no EC2 nodes needed for that namespace.

> **Note:** Fargate pods on EKS do not run `kube-proxy` or `DaemonSets`. Use VPC CNI for networking.

---

## IAM Roles for Fargate

| Role | Purpose |
|---|---|
| **Task Execution Role** | Allows ECS/Fargate to pull images from ECR and send logs to CloudWatch |
| **Task Role** | Grants the container itself permissions to call AWS APIs (S3, DynamoDB, etc.) |

Minimum Task Execution Role policy:

```json
{
  "Effect": "Allow",
  "Action": [
    "ecr:GetAuthorizationToken",
    "ecr:BatchCheckLayerAvailability",
    "ecr:GetDownloadUrlForLayer",
    "ecr:BatchGetImage",
    "logs:CreateLogStream",
    "logs:PutLogEvents"
  ],
  "Resource": "*"
}
```

---

## Summary

Fargate eliminates EC2 node management while keeping full container flexibility. It is ideal for teams that want the power of containers without the operational overhead of managing a fleet of servers. For predictable, high-scale steady-state workloads, compare Fargate pricing against EC2 Spot instances to choose the right model.
