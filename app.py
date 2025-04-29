from flask import Flask, request, jsonify
import boto3
import os
from datetime import datetime

REGION_DYNAMO_DB = "ap-south-1"  # region for DynamoDB tables
REGIONS_EC2 = ["eu-central-1", "us-east-1", "ap-south-1"]  # EC2 regions to display

STATE_FILTER_INCLUDE_PATTERNS = ['pending', 'running', 'stopping', 'stopped', 'shutting-down']
NAME_FILTER_EXCLUDE_PATTERNS = ["CI", "terminated"]

# tagging config
DEFAULT_SCHEDULE_TAG_NAME = "scheduled_for" 

MULTI_REGIONAL = len(REGIONS_EC2) > 1 

# DynamoDB connection config
CONFIG_TABLE_NAME = "instance-scheduler-ConfigTable" 

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

def add_tag_to_ec2_instance(instance_id, instance_region, schedule_name, tag_name=DEFAULT_SCHEDULE_TAG_NAME):
    return EC2_CLIENTS[instance_region].create_tags(Resources=[instance_id], Tags=[{"Key": tag_name, "Value": schedule_name}])

def get_filtered_ec2_instances(use_tag: bool):
    """
    Retrieves EC2 instances from specified regions that match state patterns and exclude name patterns.
    
    Returns:
        dict: A dictionary with region as key and list of filtered instances as value
    """
    
    result = {}
    
    for region, ec2_client in EC2_CLIENTS.items():

        filter_pattern = [{
                'Name': 'instance-state-name',
                'Values': STATE_FILTER_INCLUDE_PATTERNS
            }]
        
        if use_tag == True:
            filter_pattern.append({{'Name': "Schedule", 'Values': ['test']}})
        
        # Describe instances with state filter
        response = ec2_client.describe_instances(
            Filters = filter_pattern
        )
        
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
                        # 'InstanceType': instance['InstanceType'],
                        # 'State': instance['State']['Name'],
                        'Name': name,
                        # 'Tags': tags,
                        # 'LaunchTime': instance['LaunchTime'].isoformat(),
                        # 'PrivateIpAddress': instance.get('PrivateIpAddress', ''),
                        # 'PublicIpAddress': instance.get('PublicIpAddress', ''),
                        # 'Region': region,
                        # 'VpcId': instance.get('VpcId', ''),
                        # 'SubnetId': instance.get('SubnetId', ''),
                        # 'ImageId': instance.get('ImageId', '')
                    }
                    instances.append(instance_data)
        
        result[region] = instances
    
    return result

def schedule_factory(data: dict):
    """Create data to insert in config table"""
    minute = data['minute']
    hour = data['hour']
    day_of_month = data['day_of_month']
    month = data['month']
    week = data['week']

    schedule_data = {
        'name': data['name'],
        'type': data['type'],
        'action': data['action'],
        'status': data['status'],
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
def get_instances():
    result = get_filtered_ec2_instances(False)
    return jsonify(result)

@app.route('/api/v1/create_tag', methods=['POST'])
def add_schedule_tag():

    data = request.get_json()

    instance_id = data['instance_id']
    instance_region = data['instance_region']
    schedule_name = data['schedule_name']

    result = add_tag_to_ec2_instance(instance_id, instance_region, schedule_name, tag_name=DEFAULT_SCHEDULE_TAG_NAME)

    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)