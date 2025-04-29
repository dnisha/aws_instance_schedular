provider "aws" {
  region = "ap-south-1" # Default region where DynamoDB tables will be created
}

# DynamoDB Tables
resource "aws_dynamodb_table" "config_table" {
  name         = "instance-scheduler-ConfigTable"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "type"
  range_key    = "name"

  # 'name': data['name'],
  # 'type': data['type'],
  # 'action': data['action'],
  # 'status': data['status'],
  # 'until': data['until'],  # 2025-02-10
  # 'minute': data['minute'],
  # 'hour': data['hour'],
  # 'day_of_month': data['day_of_month'],
  # 'month': data['month'],
  # 'week': data['week'],

  attribute {
    name = "name"
    type = "S"
  }

  attribute {
    name = "type"
    type = "S"
  }
  attribute {
    name = "action"
    type = "S"
  }
  attribute {
    name = "active"
    type = "S"
  }


  attribute {
    name = "until"
    type = "S"
  }

  attribute {
    name = "cron_expression"
    type = "S"
  }

  global_secondary_index {
    name            = "ActionIndex"
    hash_key        = "action"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "CronIndex"
    hash_key        = "cron_expression"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "untilIndex"
    hash_key        = "until"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "activeIndex"
    hash_key        = "active"
    projection_type = "ALL"
  }
}

resource "aws_dynamodb_table" "users_table" {
  name         = "instance-scheduler-users"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "username"

  attribute {
    name = "username"
    type = "S"
  }
}

resource "aws_dynamodb_table" "groups_table" {
  name         = "instance-scheduler-groups"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "group_name"

  attribute {
    name = "group_name"
    type = "S"
  }
}

resource "aws_dynamodb_table" "default_schedules_table" {
  name         = "instance-scheduler-default-schedules"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "instance_name"

  attribute {
    name = "service"
    type = "S"
  }

  attribute {
    name = "instance_name"
    type = "S"
  }

  attribute {
    name = "instance_id"
    type = "S"
  }

  attribute {
    name = "account-region"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  global_secondary_index {
    name            = "ServiceIndex"
    hash_key        = "service"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "InstanceIdIndex"
    hash_key        = "instance_id"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "AccountRegionIndex"
    hash_key        = "account-region"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "StatusIndex"
    hash_key        = "status"
    projection_type = "ALL"
  }
}

# IAM Policy for the application
resource "aws_iam_policy" "instance_scheduler_policy" {
  name        = "InstanceSchedulerAppPolicy"
  description = "Policy for Instance Scheduler application"

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem"
        ],
        Resource = [
          aws_dynamodb_table.config_table.arn,
          aws_dynamodb_table.users_table.arn,
          aws_dynamodb_table.groups_table.arn,
          aws_dynamodb_table.default_schedules_table.arn
        ]
      },
      {
        Effect = "Allow",
        Action = [
          "ec2:DescribeInstances",
          "ec2:CreateTags",
          "ec2:DeleteTags"
        ],
        Resource = "*"
      }
    ]
  })
}

# IAM Role for the application (if running on EC2)
resource "aws_iam_role" "instance_scheduler_role" {
  name = "InstanceSchedulerAppRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action = "sts:AssumeRole",
        Effect = "Allow",
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })
}

# IAM User for Local Development (instead of Role)
resource "aws_iam_role" "instance_scheduler_temp_role" {
  name = "InstanceSchedulerTempRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Action    = "sts:AssumeRole",
      Effect    = "Allow",
      Principal = { AWS = "arn:aws:iam::370389955750:user/Mehul" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "instance_scheduler_attachment" {
  #   role       = aws_iam_role.instance_scheduler_role.name
  role       = aws_iam_role.instance_scheduler_temp_role.name
  policy_arn = aws_iam_policy.instance_scheduler_policy.arn
}


