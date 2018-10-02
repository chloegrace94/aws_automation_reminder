# Lamda function
# Purpose - determines which running non-static instances are candidates for 
#           stopping and notifies resource owners by direct message through 
#           Slack app
#
#
# Added to GitHub version control: 02/10/2018
# Last updated: 02/10/2018
#

import boto3        # AWS SDK for Python
import logging      # CloudWatch logs
import os
import json  

from botocore.vendored import requests
from datetime import datetime, timedelta, timezone
from base64 import b64decode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Retrieve OAuth Slack bearer token environment variable
B_TOKEN = os.environ['bearer_token']
# Decrypt bearer token
B_TOKEN = "Bearer " + boto3.client('kms').decrypt(CiphertextBlob=b64decode(B_TOKEN))['Plaintext'].decode('utf-8')

# ----------------------------------------------------------------------------------------------------------------------
# Post to Slack
def post_to_slack(message, instance_info):
    
    # Slack users
        # cyoung = @cyoung
        # user_2 = @user_2
        
    owner_dict = {
                    "cyoung" : "xxxxxxxxx",
                    "user_2" : "zzzzzzzzz"
                }
    
    # Decide who to send the message to
    # If the instance_info_list is only 3 long then there was no instance owner
    # tagged, in those circumstances send the message to cyoung specifying that 
    # the instance has no owner tag
    instance_info_list = instance_info.split()

    if len(instance_info_list) == 4:
        
        owner = instance_info_list[3]
        slack_owner = owner_dict[owner]
        
        # For testing
        # Directs all Slack messages to cyoung
        # slack_owner = owner_dict["cyoung"]
        
    else: 
        #
        # WARNING: This is hard coded
        #
        slack_owner = owner_dict["cyoung"]
    
    try:

        slack_data = {
    "text": message,
    "channel": slack_owner,
    "attachments": [
        {
            "fallback": "Sorry, an error has occured",
            "callback_id": "instance_reminder",
            "attachment_type": "default",
            "actions": [
                {
                    "name": instance_info,
                    "text": "Stop",
                    "style": "danger",
                    "type": "button",
                    "value": "stop",
                    "confirm": {
                        "title": "Are you sure?",
                        "text": ":electric_plug:  This will stop your instance",
                        "ok_text": "Shutdown",
                        "dismiss_text": "Cancel"
                    }
                },
                {
                    "name": instance_info,
                    "text": "Keep up",
                    "type": "button",
                    "value": "keep_up"
                }
                
            ]
        }
    ]
        }
        
        logger.info("Sending to Slack: " + str(slack_data))
        response = requests.post(
                "https://slack.com/api/chat.postMessage", data=json.dumps(slack_data),
                headers={'Content-Type': 'application/json', 'Authorization': B_TOKEN}
            )
            
        if response.status_code != 200:
            raise ValueError(
                'Request to slack returned an error %s, the response is:\n%s'
                % (response.status_code, response.text)
            )
        return 0

    except Exception as err:
        logger.error('Error in def post_to_slack(): %s' % str(err))

# ----------------------------------------------------------------------------------------------------------------------
# Find all running static EC2 instances
def ec2_fact_finder(session):
    client = session.client('ec2')
    response = client.describe_instances(
        Filters=[
            {
                'Name': 'instance-state-name',
                'Values': [
                    'running',
                ]
            },
    {
                'Name': 'tag:Static',
                'Values': [
                    'no',
                ]
            }
        ]
    )
    return(response)

# ----------------------------------------------------------------------------------------------------------------------
# EC2 instance candidate finder
def ec2_candidate_finder(response, agelimit, reserved_til, nowdatetime):
    resource_type = 'EC2'
    for instance in (response['Reservations']):
        InstanceId = (instance['Instances'][0]['InstanceId'])
        launch_time = (instance['Instances'][0]['LaunchTime'])
        tags = (instance['Instances'][0]['Tags'])
    
    
        inst_owner = None
        inst_name = None
        reserved_til = None
        for tag in tags:
            if (tag['Key']).upper() == 'NAME':
                inst_name = tag['Value']
            if (tag['Key']).upper() == 'OWNER':
                inst_owner = tag['Value']
            if (tag['Key']).upper() == 'RESERVED_UNTIL':
                
                reserved_til = (datetime.strptime(tag['Value'], '%Y-%m-%d %H:%M:%S.%f+00:00')).replace(tzinfo=timezone.utc)
                logger.info("Reserved tag value: " + str(reserved_til))
    
        # Determines if instance is candidate for stopping
        # Conditions:
        # - launch time was more than agelimit hours ago
        # - instance reserved til tag time-date has past
        logger.info("\nResource type: " + resource_type + "\nInstance: " + inst_name + "\nLaunch: " + str(launch_time) + "\nLimit: " + str(agelimit) + "\nReserved til: " + str(reserved_til) + "\nNow: " + str(nowdatetime))
        if (launch_time <= agelimit) and (reserved_til == None or reserved_til <= nowdatetime):
            
            uptime = nowdatetime - launch_time
            uphours = int((uptime.total_seconds())/3600)
            
            if uphours <= 1:
                h_word = "hour"
            else:
                h_word = "hours"
            
            if inst_owner == None:
                message = ("Hey we have an unclaimed *" + resource_type + "* instance *" + inst_name + "*?\nIt has been up for *" + str(uphours) + "* " + h_word)
                instance_info = (resource_type + ' ' + inst_name + ' ' + InstanceId)
            else:    
                message = ("Hey " + inst_owner + ", do you need to stop your *" + resource_type + "* instance *" + inst_name + "*?\nIt has been up for *" + str(uphours) + "* " + h_word)
                instance_info = (resource_type + ' ' + inst_name + ' ' + InstanceId + ' ' + inst_owner)
    
            # Post to slack
            # Message = string containing message to send to instance owner
            # instance_info = useful information about instance to be sent for 
            # use in post_to_slack function (identifying Slack user from 
            # the resource owner) and further down the pipeline
            post_to_slack(message, instance_info)
    

# ----------------------------------------------------------------------------------------------------------------------
# RDS instance fact finder
def rds_fact_and_candidate_finder(session, rds_agelimit, rds_running_agelimit, reserved_til, nowdatetime):
    resource_type = 'RDS'

    client = session.client('rds')
    db_instances = client.describe_db_instances()

    for inst in db_instances['DBInstances']:
        start_time = None
        # Get instance owner & static value from tags
        arn = inst['DBInstanceArn']
        rdstags = client.list_tags_for_resource(ResourceName=arn)

        inst_owner = None
        inst_static = None
        start_time = None
        reserved_til = None
        for tag in rdstags['TagList']:
            if tag['Key'].upper() == 'OWNER':
                inst_owner = tag['Value']
            if tag['Key'].upper() == 'STATIC':
                inst_static = tag['Value']
            if tag['Key'].upper() == 'STARTED':
                start_time = tag['Value']
                #logger.info("Start tag value: " + str(start_time))
                start_time = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S.%f+00:00')
                start_time = start_time.replace(tzinfo=timezone.utc)
                # For reference, format = 2018-08-17 15:26:34.462614+00:00
            if (tag['Key']).upper() == 'RESERVED_UNTIL':
                reserved_til = (datetime.strptime(tag['Value'], '%Y-%m-%d %H:%M:%S.%f+00:00')).replace(tzinfo=timezone.utc)
                logger.info("Reserved tag value: " + str(reserved_til))
                
                
        # Determines if instance is candidate for stopping
        if start_time != None:
            #
            # Using start_time taken from tag - Instance is tagged by RDS status change Lambda
            #
            # Conditions:
            # - Instance is not stopped or stopping
            # - Instance is not static
            # - Most resent start time (taken from tag) was more than agelimit hours ago
            # - instance reserved til tag time-date has past
            
            # Logging conditions
            logger.info("\nResource type: " + resource_type + "\nInstance: " + inst['DBInstanceIdentifier'] + "\nInstanceStatic?: " + str(inst_static) + "\nStart time: " + str(start_time) + "\nLimit: " + str(rds_running_agelimit) + "\nReserved til: " + str(reserved_til) + "\nNow: " + str(nowdatetime))
            
            if inst.get('DBInstanceStatus') != 'stopped' and inst.get('DBInstanceStatus') != 'stopping' and inst_static == 'no' and (reserved_til == None or reserved_til <= nowdatetime) and start_time <= rds_running_agelimit:
                
                uptime = nowdatetime - start_time
                uphours = int((uptime.total_seconds())/(3600))
                
                if inst_owner == None:
                    message = ("Hey, we have an unclaimed *" + resource_type + "* instance *" + inst['DBInstanceIdentifier'] + "*\nIt has been running for *" + str(uphours) + "* hours")
                    instance_info = (resource_type + ' ' + inst['DBInstanceIdentifier'] + ' ' + str(arn))
                else:
                    message = ("Hey " + inst_owner + ", do you need to stop your *" + resource_type + "* instance *" + inst['DBInstanceIdentifier'] + "*?\nIt has been running for *" + str(uphours) + "* hours")
                    instance_info = (resource_type + ' ' + inst['DBInstanceIdentifier'] + ' ' + str(arn) + ' ' + inst_owner)
                    
                # Post to slack
                # Message = string containing message to send to instance owner
                # instance_info = useful information about instance to be sent for 
                # use in post_to_slack function (identifying Slack user from 
                # the resource owner) and further down the pipeline
                post_to_slack(message, instance_info)
            
        else:
            #
            #Using launch time
            #
            # Conditions:
            # - Instance is not stopped or stopping
            # - Instance is not static
            # - launch time was more than agelimit hours ago
            # - instance "reserved til" tag time-date has past
            
            # Logging conditions
            logger.info("\nResource type: " + resource_type + "\nInstance: " + inst['DBInstanceIdentifier'] + "\nLaunch: " + str(inst.get('InstanceCreateTime')) + "\nLimit: " + str(rds_agelimit) + "\nReserved til: " + str(reserved_til) + "\nNow: " + str(nowdatetime))
            
            if inst.get('DBInstanceStatus') != 'stopped' and inst.get('DBInstanceStatus') != 'stopping' and inst_static == 'no' and (reserved_til == None or reserved_til <= nowdatetime) and inst.get('InstanceCreateTime') <= rds_agelimit:
                
                uptime = nowdatetime - inst.get('InstanceCreateTime')
                updays = int((uptime.total_seconds())/(3600*24))
                
                if inst_owner == None:
                    message = ("Hey, we have an unclaimed *" + resource_type + "* instance *" + inst['DBInstanceIdentifier'] + "\nIt has been launched for *" + str(updays) + "* days")
                    instance_info = (resource_type + ' ' + inst['DBInstanceIdentifier'] + ' ' + str(arn))
                else:
                    message = ("Hey " + inst_owner + ", do you need to stop your *" + resource_type + "* instance *" + inst['DBInstanceIdentifier'] + "*?\nIt has been launched for *" + str(updays) + "* days")
                    instance_info = (resource_type + ' ' + inst['DBInstanceIdentifier'] + ' ' + str(arn) + ' ' + inst_owner)
            
                # Post to slack
                # Message = string containing message to send to instance owner
                # instance_info = useful information about instance to be sent for 
                # use in post_to_slack function (identifying Slack user from 
                # the resource owner) and further down the pipeline
                post_to_slack(message, instance_info)

# ----------------------------------------------------------------------------------------------------------------------
# Main function
def lambda_handler(event, context):

    # Set limits - If instance was launched in the last this many hours, don't ask if they want it brought down
    ec2_limit = 4  # (Hours) For EC2 instances
    rds_limit = 120 #  (Hours) For RDS instances using the launch time
    rds_running_limit = 6 # (Hours) For RDS instances using the up time from its tag
    
    # Calculate date-time values
    nowdatetime = datetime.now(timezone.utc)
    ec2_agelimit = nowdatetime - timedelta(hours=ec2_limit)
    rds_agelimit = nowdatetime - timedelta(hours=rds_limit)
    rds_running_agelimit = nowdatetime - timedelta(hours=rds_running_limit)
    
    # Log limits
    logger.info("EC2 running limit: " + str(ec2_limit) + " hours\nRDS launched limit: " + str(rds_limit) + " hours\nRDS running limit: " + str(rds_running_limit) + " hours")

    # Default reserved_til value for comparison to nowdatetime
    reserved_til = datetime.min
    reserved_til = reserved_til.replace(tzinfo=timezone.utc)

    # Search for instances that are candidates to stopping and send them to 
    # owners via Slack
    try:
        #AWS
        session = boto3.Session()
        # EC2
        ec2_response = ec2_fact_finder(session)
        ec2_candidate_finder(ec2_response, ec2_agelimit, reserved_til, nowdatetime)
        
        # RDS
        rds_fact_and_candidate_finder(session, rds_agelimit, rds_running_agelimit, reserved_til, nowdatetime)
                
    except Exception as err:
        logger.error('Error: %s' % str(err))
