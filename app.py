from flask import Flask, render_template, request, jsonify
from flask import url_for
from flask import request
from flask import redirect
import boto3
from botocore.exceptions import ClientError
from apscheduler.schedulers.background import BackgroundScheduler
from logging.config import dictConfig
import os
from datetime import datetime
import re
import atexit

REGION_DYNAMO_DB = "ap-south-1"  # region for DynamoDB tables
REGIONS_EC2 = ["eu-central-1", "us-east-1", "ap-south-1"]  # EC2 regions to display

STATE_FILTER_INCLUDE_PATTERNS = ['pending', 'running', 'stopping', 'stopped', 'shutting-down']
NAME_FILTER_EXCLUDE_PATTERNS = ["CI", "terminated"]

# tagging config
DEFAULT_SCHEDULE_TAG_NAME = 'ScheduledFor'

MULTI_REGIONAL = len(REGIONS_EC2) > 1 

# DynamoDB connection config
CONFIG_TABLE_NAME = "instance-scheduler-ConfigTable" 


# Configure logging
dictConfig({
    'version': 1,
    'formatters': {
        'default': {
            'format': '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
        }
    },
    'handlers': {
        'wsgi': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://flask.logging.wsgi_errors_stream',
            'formatter': 'default'
        }
    },
    'root': {
        'level': 'INFO',
        'handlers': ['wsgi']
    }
})

app = Flask(__name__)

def create_aws_connections():
    """Create AWS connections to DynamoDB and EC2 clients."""
    aws_session = boto3.Session(
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        aws_session_token=os.getenv('AWS_SESSION_TOKEN'),
        region_name=REGION_DYNAMO_DB
    )
    
    dynamodb_resource = aws_session.resource('dynamodb')
    regions_ec2_dict = {
        region: aws_session.client('ec2', region_name=region) 
        for region in REGIONS_EC2
    }
    return dynamodb_resource, regions_ec2_dict

DYNAMODB_RESOURCE, EC2_CLIENTS = create_aws_connections()


def db_put_item(table_name: str, item: dict) -> dict:
    """Put item into DynamoDB table."""
    return DYNAMODB_RESOURCE.Table(table_name).put_item(Item=item)

def db_get_items(
    table_name: str,
    filter_expression: str = None,
    expression_attribute_values: dict = None,
    projection_expression: str = None,
    limit: int = None
) -> list:
    """
    Scan entire DynamoDB table and return all items (with pagination handling).
    
    Args:
        table_name: Name of the DynamoDB table
        filter_expression: Optional filter (e.g., "status = :active")
        expression_attribute_values: Values for filters (e.g., {":active": True})
        projection_expression: Attributes to return (e.g., "id, name")
        limit: Maximum number of items to return (optional)
        
    Returns:
        list: All items matching the criteria
    """
    table = DYNAMODB_RESOURCE.Table(table_name)
    items = []
    scan_args = {}

    # Build scan parameters
    if filter_expression:
        scan_args['FilterExpression'] = filter_expression
    if expression_attribute_values:
        scan_args['ExpressionAttributeValues'] = expression_attribute_values
    if projection_expression:
        scan_args['ProjectionExpression'] = projection_expression
    if limit:
        scan_args['Limit'] = limit

    # Initial scan
    response = table.scan(**scan_args)
    items.extend(response.get('Items', []))

    # Paginate through all results (DynamoDB has 1MB limit per scan)
    while 'LastEvaluatedKey' in response:
        scan_args['ExclusiveStartKey'] = response['LastEvaluatedKey']
        response = table.scan(**scan_args)
        items.extend(response.get('Items', []))
        
        # Early exit if limit reached
        if limit and len(items) >= limit:
            break

    return items[:limit] if limit else items

def add_tag_to_ec2_instance(instance_id, instance_region, schedule_name, tag_name=DEFAULT_SCHEDULE_TAG_NAME):
    return EC2_CLIENTS[instance_region].create_tags(Resources=[instance_id], Tags=[{"Key": tag_name, "Value": schedule_name}])

def should_execute(cron_expression: str, until_date: str) -> bool:
    """
    Check if the current time matches the cron expression and is on or before the until date.
    
    Args:
        cron_expression: A string in cron format (e.g., "5 1 * * *")
        until_date: A string in YYYY-MM-DD format (e.g., "2025-05-10")
        
    Returns:
        bool: True if the process should run now, False otherwise
    """
    try:
        # Parse cron expression
        cron_parts = re.split(r'\s+', cron_expression.strip())
        if len(cron_parts) != 5:
            raise ValueError("Invalid cron expression format")
            
        minute, hour, day_of_month, month, day_of_week = cron_parts
        
        # Parse until date (at midnight to include the entire day)
        until = datetime.strptime(until_date, "%Y-%m-%d").date()
        now = datetime.now()
        current_date = now.date()
        
        # Check if current date is after until date
        if current_date > until:
            return False
        
        # Check minute
        if minute != '*' and str(now.minute) != minute:
            return False
            
        # Check hour
        if hour != '*' and str(now.hour) != hour:
            return False
            
        # Check day of month
        if day_of_month != '*' and str(now.day) != day_of_month:
            return False
            
        # Check month
        if month != '*' and str(now.month) != month:
            return False
            
        # Check day of week (0-6 where 0 is Sunday)
        if day_of_week != '*':
            # Convert cron day of week to Python's weekday (0=Monday)
            cron_dow = int(day_of_week)
            # Sunday special case (cron: 0 or 7 = Sunday, Python: 6 = Sunday)
            if cron_dow == 0 or cron_dow == 7:
                if now.weekday() != 6:  # Python's Sunday
                    return False
            elif (cron_dow - 1) != now.weekday():
                return False
                
        # All checks passed - should run now
        return True
        
    except Exception as e:
        print(f"Error processing cron expression: {e}")
        return False

def get_filtered_ec2_instances(for_tag: str):
    """
    Retrieves EC2 instances from specified regions that match state patterns and exclude name patterns.
    
    Returns:
        dict: A dictionary with region as key and list of filtered instances as value
    """
    result = {}
    
    for region, ec2_client in EC2_CLIENTS.items():
        # Start with the state filter
        filter_pattern = [{
            'Name': 'instance-state-name',
            'Values': STATE_FILTER_INCLUDE_PATTERNS
        }]
        
        # Only add the tag filter if use_tag is True and for_tag is provided
        if for_tag:
            filter_pattern.append({
                'Name': f'tag:{DEFAULT_SCHEDULE_TAG_NAME}',
                'Values': [for_tag]
            })
        
        # Describe instances with filters
        response = ec2_client.describe_instances(Filters=filter_pattern)
        
        instances = []
        
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                # Initialize tags dictionary
                tags = {}
                name = ''
                
                # Process tags if they exist
                if 'Tags' in instance:
                    for tag in instance['Tags']:
                        tags[tag['Key']] = tag['Value']
                        if tag['Key'] == 'Name':
                            name = tag['Value']
                
                # Check if name matches any exclude pattern
                include_instance = True
                for pattern in NAME_FILTER_EXCLUDE_PATTERNS:
                    if pattern.lower() in name.lower():
                        include_instance = False
                        break
                
                if include_instance:
                    instance_data = {
                        'InstanceId': instance['InstanceId'],
                        'Name': name,
                        'CurrentState' : instance['State']['Name']
                        # 'Tags': tags,
                        # 'Region': region,
                    }
                    instances.append(instance_data)
        
        result[region] = instances
    
    return result

def instance_action(action: str, current_state: str, instance_id:str, instance_name: str, region: str, schedule_name: str):

    result = {
        'schedules_processed': 0,
        'instances_modified': 0,
        'state_changes': [],
        'errors': []
    }

    try:
        ec2_client = EC2_CLIENTS[region]

        # print(f"for instance {instance_id} we are performing action as {action}")
        if action.lower() == 'start' and current_state == 'stopped':
            ec2_client.start_instances(InstanceIds=[instance_id])
            result['state_changes'].append({
                'instance_id': instance_id,
                'instance_name': instance_name,
                'region': region,
                DEFAULT_SCHEDULE_TAG_NAME: schedule_name,
                'action': 'start',
                'from_state': 'stopped',
                'to_state': 'pending'
            })
            result['instances_modified'] += 1
            print(f"Started instance {instance_id} ({instance_name}) in region {region}")
            
        elif action.lower() == 'stop' and current_state == 'running':
            ec2_client.stop_instances(InstanceIds=[instance_id])
            result['state_changes'].append({
                'instance_id': instance_id,
                'instance_name': instance_name,
                'region': region,
                DEFAULT_SCHEDULE_TAG_NAME: schedule_name,
                'action': 'stop',
                'from_state': 'running',
                'to_state': 'stopping'
            })
            result['instances_modified'] += 1
            print(f"Stopped instance {instance_id} ({instance_name}) in region {region}")
        
    except ClientError as e:
        result['errors'].append({
            'instance_id': instance_id,
            'region': region,
            'error': str(e),
            'action_attempted': schedule['action'].lower()
        })


def schedule_factory(data: dict):
    """Create data to insert in config table"""
    minute = data['minute']
    hour = data['hour']
    day_of_month = data['day_of_month']
    month = data['month']
    week = data['week']
    status = str(data.get('status', 'false')).lower()

    schedule_data = {
        'name': data['name'],
        'type': data['type'],
        'action': data['action'],
        'active': status,
        'until': data['until'], 
        'cron_expression': f"{minute} {hour} {day_of_month} {month} {week}",
    }

    db_put_item(CONFIG_TABLE_NAME, item=schedule_data)
    return schedule_data

app = Flask(__name__)

@app.route('/api/v1/healthz')
def health_check():
    return jsonify({"status": "healthy"})

@app.route('/api/v1/schedule', methods=['POST'])
def schedule():
    data = request.get_json()
    result = schedule_factory(data=data)
    return jsonify(result)

@app.route('/api/v1/instances', methods=['GET'])
def instances():

    for_tag = request.args.get('for_tag') 

    print(f"for_tag {for_tag}")
    result = get_filtered_ec2_instances(for_tag)
    return jsonify(result)

@app.route('/api/v1/create_tag', methods=['POST'])
def add_schedule_tag():

    data = request.get_json()

    instance_id = data['instance_id']
    instance_region = data['instance_region']
    schedule_name = data['schedule_name']

    result = add_tag_to_ec2_instance(instance_id, instance_region, schedule_name, tag_name=DEFAULT_SCHEDULE_TAG_NAME)

    return jsonify(result)

@app.route('/api/v1/get_schedule/active', methods=['GET'])
def get_all_active_schedules():

    applied_on_instances = []

    ## get all active schedule and perform action

    active_schedules =  db_get_items(CONFIG_TABLE_NAME, filter_expression="active = :active_val", expression_attribute_values={":active_val": "true"})

    for schedule in active_schedules:

        instances =  get_filtered_ec2_instances(schedule['name'])

        print(f"got cron_expression as  {schedule['cron_expression']} and until as {schedule['until']}")

        will_execute = should_execute(cron_expression=schedule['cron_expression'], until_date=schedule['until'])

        print(f"will_execute {will_execute}")

        if will_execute == True:

            for region, instances in instances.items():

                print(f"Region: {region}")

                if instances:  # Check if the list is not empty
                    for instance in instances:
                        # print(f"  Instance ID: {instance['InstanceId']}")
                        # print(f"  Name: {instance['Name']}")

                        applied_on_instances = instance_action(action=schedule['action'], current_state=instance['CurrentState'], instance_id=instance['InstanceId'], instance_name=instance['Name'], region=region, schedule_name=schedule['name'])
                else:
                    print("  No instances in this region {region}")
        
    return jsonify(applied_on_instances)

@app.route('/api/v1/schedule/instance', methods=['GET'])
def schedule_instance():

    data = request.get_json()
    instance_id = data['instance_id']
    schedule_name = data['schedule_name']
    instance_region = data['instance_region']

    result = add_tag_to_ec2_instance(instance_id, instance_region, schedule_name, tag_name=DEFAULT_SCHEDULE_TAG_NAME)

    return jsonify(data)

@app.route('/instances')
def get_instances():
    # Get all instances (pass None to get all instances regardless of tag)
    all_instances = get_filtered_ec2_instances(for_tag=None)
    
    # Flatten the instances from all regions into a single list
    instances_list = []
    for region, instances in all_instances.items():
        for instance in instances:
            instance['Region'] = region  # Add region to each instance
            instances_list.append(instance)
    
    return render_template('instances.html', instances=instances_list)

@app.route('/schedule')
def schedule_job():    
    return render_template('schedule.html')

@app.route('/')
def landing():    
    return render_template('landing.html')

def cron_trigerred():
    print(f"cron trigerred")

# Initialize scheduler
# scheduler = BackgroundScheduler()
# scheduler.add_job(
#     func=return_default_tag_to_instances,
#     trigger="cron",
#     hour=CRON_START_HOUR,
#     minute=CRON_START_MINUTE,
#     timezone=CRON_TIMEZONE
# )

# Test cron
# scheduler.add_job(
#     func=cron_trigerred,
#     trigger="interval",
#     seconds=15,
#     timezone="Asia/Kolkata"
# )   

# scheduler.start()
# atexit.register(lambda: scheduler.shutdown())