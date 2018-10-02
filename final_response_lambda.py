# Lambda function
# Purpose - Handles replies to messages from reminder_lambda
#
#
# Added to GitHub version control: 02/10/2018
# Last updated: 02/10/2018

import boto3        # AWS SDK for Python
import logging      # CloudWatch logs
import json
import os
import time

from botocore.vendored import requests
from base64 import b64decode
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse
from urllib.parse import parse_qs

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Region (https://docs.aws.amazon.com/lambda/latest/dg/current-supported-versions.html)
region = os.environ['AWS_REGION']

# ----------------------------------------------------------------------------------------------------------------------
# Post to Slack
def post_to_slack(channel_id, message_ts, original_message, message_response, response_url):

    try:
        slack_data = {  
       "channel":channel_id,
       "ts":message_ts,
       "text":original_message,
       #"attachment_type": "default",
        "attachments": [
            {
                "name": "action_decision",
                "text": message_response,
                "fallback": "Sorry, I'm unable to do that for you at the moment"
            }
        ]
    }

        logger.info("\nResponse Message: " + str(slack_data))

        response = requests.post(
            response_url, data=json.dumps(slack_data),
            headers={'Content-Type': 'application/json'}
        )
        
        if response.status_code != 200:
            raise ValueError(
                'Request to slack returned an error %s, the response is:\n%s'
                % (response.status_code, response.text)
            )

    except Exception as err:
        logger.error('Error: %s' % str(err))
# ----------------------------------------------------------------------------------------------------------------------

# Stop & start RDS instances
def stop_start_rds(session, inst_name, action, instance_id_or_arn):

    try:
        client = session.client('rds', region_name=region)
        db_instances = client.describe_db_instances(DBInstanceIdentifier=inst_name)

        # get the instance if it can be found
        if len(db_instances) != 0:
            db_instance = db_instances['DBInstances'][0]
        else:
            msg = ("Sorry!, I can't do that for you right now, I can't find instance *" + inst_name + "*")
            return msg
            raise Exception("Instance *" + inst_name + "* does not exist or is not currently launched")

        curr_state = db_instance.get('DBInstanceStatus')

        if (action.upper() == "STOP" and curr_state.upper() == 'AVAILABLE'):

            client.stop_db_instance(DBInstanceIdentifier=inst_name)
            msg  = ":heavy_check_mark: *" + inst_name + "* successfuly stopping"

        else:
            msg = ('*RDS* instance *' + inst_name + '* is currently *'
                  + curr_state
                  + '*, no action needed at this time'
                  )
        return msg #, response_type

    except Exception as err:
        logger.error('Error: %s' % str(err))
        error_message = ("Sorry, the *RDS* instance *" + inst_name + "* does not exist or is not currently launched")
        return error_message 

# ----------------------------------------------------------------------------------------------------------------------
# # Stop & start EC2 instances
def stop_start_ec2(session, inst_name, action, instance_id_or_arn):

    try:

        ec2 = session.resource('ec2', region_name=region)
        inst = ec2.Instance(id=instance_id_or_arn)
        # Get current state
        curr_state = inst.state['Name']

        if (action.upper() == "STOP" and curr_state.upper() == 'RUNNING'):

            inst.stop()
            msg = ":heavy_check_mark: *" + inst_name + "* successfuly stopping"

        else:
            msg = ("*EC2* instance *" + inst_name + "* is currently *" + curr_state + "*, no action needed at this time")
        return msg 

    except Exception as err:
        logger.error('Error: %s' % str(err))
        error_message = ("Sorry <@" + user + ">, the *EC2* instance *" + inst_name + "* cannot be found")
        return error_message
        
# ----------------------------------------------------------------------------------------------------------------------
# Instance tagging function
def instance_tagger(action_value, resource_type, instance_id_or_arn, instance_name, user_id):
    
    # Slack users
        # cyoung = @cyoung
        # user_2 = @user_2
        
    owner_dict = {
                    "cyoung" : "xxxxxxxxx",
                    "user_2" : "zzzzzzzzz"
                }
    
    Reserved_by = (list(owner_dict.keys())[list(owner_dict.values()).index(user_id)])
    logger.info("\nTagging with Reserved_by : " + str(Reserved_by))
    
    # Calculate date-time values
    nowdatetime = datetime.now(timezone.utc)
    Reserved_until = nowdatetime + timedelta(hours=(float(action_value)*24))
    logger.info("\n Tagging with Reserved_until : " + str(Reserved_until))
    
    try:
        #Add tag EC2
        if (resource_type).upper() == 'EC2':
            client = boto3.client('ec2')
            
            # Is instance still running?

            response = client.create_tags(
                Resources=[
                    instance_id_or_arn,
                ],
                Tags=[
                    {
                        'Key': 'Reserved_until',
                        'Value': str(Reserved_until),
                    },
                    {
                        'Key': 'Reserved_by',
                        'Value': str(Reserved_by),
                    },
                ],
            )

        # Reserve RDS with tag    
        elif (resource_type).upper() == 'RDS':    
            client = boto3.client('rds')
            
            # Is instance still running?
            response = client.describe_db_instances(
                DBInstanceIdentifier=instance_id_or_arn,
            )
            
            if response['DBInstances'][0]['DBInstanceStatus'] != 'stopped':

                response = client.add_tags_to_resource(
                        ResourceName=instance_id_or_arn,
                        Tags=[
                            {
                                'Key': 'Reserved_until',
                                'Value': str(Reserved_until)
                            },
                            {
                                'Key': 'Reserved_by',
                                'Value': str(Reserved_by),
                            },
                        ]
                    )
            else:
                logger.error('Error: %s' % str(err))
                message = (":x: Sorry your instance *" + instance_name + "* cannot be reserved as it is no longer running")
                return(message)
                
        # Confirmation message
        if action_value == '1':
            message  = ":heavy_check_mark: Your instance *" + str(instance_name) + "* has been reserved for *" + action_value + "* day"
        else:
            message  = ":heavy_check_mark: Your instance *" + str(instance_name) + "* has been reserved for *" + action_value + "* days"
        
    except Exception as err:
        logger.error('Error: %s' % str(err))
        message = (":x: Sorry your instance *" + instance_name + "* cannot be reserved at this time")
        
    return(message)

# ----------------------------------------------------------------------------------------------------------------------
# Main function
def lambda_handler(event, context):
    
    # # Obtain message information from event
    instance_info = event['actions'][0]['name'].split()
    resource_type = instance_info[0]
    instance_name = instance_info[1]
    instance_id_or_arn = instance_info[2]
    owner = instance_info[3]
    action_type = event['actions'][0]['type']
    # If action type is select, action value is nested under selected options
    if action_type == "button":
        action_value = event['actions'][0]['value']
    elif action_type == "select":
        action_value = event['actions'][0]['selected_options'][0]['value']
    channel_id = event['channel']['id']
    channel_name = event['channel']['name']
    user_id = event['user']['id']
    user_name = event['user']['name']
    response_url = event['response_url']
    message_ts = event['message_ts']
    original_message = event['original_message']['text']

    # # Log important message information
    logger.info("\nResource type: " + str(resource_type) + "\nInstance name: " + str(instance_name) + "\nInstance ID or arn (dependant on EC2 or RDS): " + str(instance_id_or_arn) + "\nResource owner: " + str(owner) + " \nAction type: " + str(action_type) + "\nAction value: " + str(action_value) + "\nChannel ID: " + str(channel_id) + "\nChannel Name: " + str(channel_name) + "\nUser ID: " + str(user_id) + "\nUser Name: " + str(user_name))
    logger.info("\noriginal_message: " + str(original_message))

    #
    # Perform actions requested by interactive buttons
    #

    session = boto3.Session()

    # #
    # # /stop
    # #
    if action_type == "button" and action_value == "stop":
        action = "STOP"
        if (resource_type).upper() == 'RDS':
            message = stop_start_rds(session, instance_name, action, instance_id_or_arn)
        else:
            message = stop_start_ec2(session, instance_name, action, instance_id_or_arn)
    
    # #
    # # Reserve 
    # #
    elif action_type == "select":
        message = instance_tagger(action_value, resource_type, instance_id_or_arn, instance_name, user_id)

    # Post updated action successful message to Slack
    logger.info("message: " + str(message))
    post_to_slack(channel_id, message_ts, original_message, message, response_url)
