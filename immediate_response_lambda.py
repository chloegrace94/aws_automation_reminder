# Lamda function 
# Purpose - Verifies message, triggers Lambda to process interactive action 
#           response, returns immediate 200 response to Slack to confirm 
#           reciept of message and processing of action
#
# Added to GitHub version control: 02/10/2018
# Last updated: 02/10/2018

import boto3        # AWS SDK for Python
import logging      # CloudWatch logs
import json
import os
import hashlib
import hmac

from base64 import b64decode
from urllib.parse import parse_qs

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Retrieve environment variables
ENCRYPTED_EXPECTED_TOKEN = os.environ['SLACK_TOKEN']
SIGNING_SECRET = os.environ['SIGNING_SECRET']

# Decrypt encrypted environment variables
kms = boto3.client('kms')
expected_token = boto3.client('kms').decrypt(CiphertextBlob=b64decode(ENCRYPTED_EXPECTED_TOKEN))['Plaintext'].decode('utf-8')
signing_secret = boto3.client('kms').decrypt(CiphertextBlob=b64decode(SIGNING_SECRET))['Plaintext'].decode('utf-8')

client = boto3.client('lambda')

# ----------------------------------------------------------------------------------------------------------------------
# Verify the Slack Signature and Verification token
def verify(raw_body, token, headers):
    
    try:
        # Define variables
        version_no = "v0"
        
        # Verify Signing Secret
        slack_signature = headers['X-Slack-Signature']
        slack_request_timestamp = headers['X-Slack-Request-Timestamp']
        
        # Construct and encode
        basestring = f"v0:{slack_request_timestamp}:{raw_body}".encode('utf-8')
        slack_signing_secret = bytes(signing_secret, 'utf-8')
    
        # Create a new HMAC "signature", and return the string presentation
        my_signature = 'v0=' + hmac.new(slack_signing_secret, basestring, hashlib.sha256).hexdigest()
    
        # Compare signatures and verification tokens
        if hmac.compare_digest(my_signature, slack_signature) and token == expected_token:
            return True
        else:
            return False
    
    except Exception as err:
        logger.error('Error: %s' % str(err))
        return False

# ----------------------------------------------------------------------------------------------------------------------
# Main function
def lambda_handler(event, context):
    #logger.info("Event: " + str(event))
    
    try:
        # body
        raw_body = event["body"]
        body = json.loads(parse_qs(raw_body)['payload'][0])
        
        # headers
        headers = event["headers"]
        
        # Verify message
        if not verify(raw_body, body["token"], headers):
            response = {
                "response_type": 'ephemeral',
                "text": 'Message could not be verified, contact DevOps'
            }
            logger.error("Message not verified")
            return response
            
        # # Obtain message information from body
        instance_info = body['actions'][0]['name']
        instance_info_list = instance_info.split()
        resource_type = instance_info_list[0]
        instance_name = instance_info_list[1]
        instance_id_or_arn = instance_info_list[2]
        owner = instance_info_list[3]
        action_type = body['actions'][0]['type']
        # If action type is select, action value is nested under selected options
        if action_type == "button":
            action_value = body['actions'][0]['value']
        elif action_type == "select":
            action_value = body['actions'][0]['selected_options'][0]['value']
        channel_id = body['channel']['id']
        channel_name = body['channel']['name']
        user_id = body['user']['id']
        user_name = body['user']['name']
        #response_url = body['response_url']
        message_ts = body['message_ts']
        original_message = body['original_message']['text']
        
        # # Log important message information
        logger.info("\nResource type: " + str(resource_type) + "\nInstance name: " + str(instance_name) + "\nInstance ID or arn (dependant on EC2 or RDS): " + str(instance_id_or_arn) + "\nResource owner: " + str(owner) + " \nAction type: " + str(action_type) + "\nAction value: " + str(action_value) + "\nChannel ID: " + str(channel_id) + "\nChannel Name: " + str(channel_name) + "\nUser ID: " + str(user_id) + "\nUser Name: " + str(user_name))
        logger.info("\noriginal_message: " + str(original_message))
        
        # Create 200 response message for all actions
        # Only invoke the response lambda if action is stop or reserve
        # as no further actions are required if you are keeping the instance up
        if action_type == "button" and action_value == "keep_up":
            
            message_update = {
        "channel":channel_id,
        "ts":message_ts,
        "text":original_message,
        "attachments": [
            {
                "fallback": ":money_with_wings: Instance *" + str(instance_name) + "* staying up!",
                "callback_id": "instance_reminder",
                "text": ":heavy_check_mark: Instance *" + str(instance_name) + "* staying up!\nWant to reserve this instance for a while?\nI'll stop asking if you want it brought down... :tada:",
                "attachment_type": "default",
                "actions": [
                    {
                        "name": instance_info,
                        "text": "Reserve instance for...",
                        "type": "select",
                        "options": [
                            {
                                "text": "1 Day",
                                "value": "1"
                            },
                            {
                                "text": "2 Days",
                                "value": "2"
                            },
                            {
                                "text": "5 Days",
                                "value": "5"
                            },
                            {
                                "text": "7 Days",
                                "value": "7"
                            },
                            {
                                "text": "10 Days",
                                "value": "10"
                            },
                            {
                                "text": "14 Days",
                                "value": "14"
                            }
                        ]
                    }
                ]
            }
        ]
            }
            
        elif action_type == "button" and action_value == "stop":
            message_response  = ":bomb: Stopping *" + str(instance_name) + "*..."
            # Invoke Lambda using invocation type: 'Event'
            response = client.invoke(
                FunctionName='final_response_lambda',
                InvocationType='Event',
                LogType='None',
                Payload= json.dumps(body),
            )
            logger.info("Lambda invoke: " + str(response))
            
            message_update = {  
              "channel":channel_id,
              "ts":message_ts,
              "text":original_message,
              #"attachment_type": "default",
                "attachments": [
                    {
                        "name": "action_decision",
                        "text": message_response,
                        "fallback": ":x: Sorry, I'm unable to do that for you at the moment"
                    }
                ]
            }
            
        elif action_type == "select":
            if action_value == '1':
                message_response  = ":money_with_wings: Reserving *" + str(instance_name) + "* for *" + action_value + "* day..."
            else:
                message_response  = ":money_with_wings: Reserving *" + str(instance_name) + "* for *" + action_value + "* days..."
            # Invoke final response Lambda using invocation type: 'Event'
            response = client.invoke(
                FunctionName='final_response_lambda',
                InvocationType='Event',
                LogType='None',
                Payload= json.dumps(body),
            )
            logger.info("Lambda invoke: " + str(response))
    
        
            message_update = {  
              "channel":channel_id,
              "ts":message_ts,
              "text":original_message,
              #"attachment_type": "default",
                "attachments": [
                    {
                        "name": "action_decision",
                        "text": message_response,
                        "fallback": ":x: Sorry, I'm unable to do that for you at the moment"
                    }
                ]
            }

        # Return Message update to the API Gateway
        logger.info("\nMessage Update: " + str(message_update))
        return message_update
    
    except Exception as err:
        logger.error('Error: %s' % str(err))
        response = {
            "response_type": 'ephemeral',
            "text": 'Sorry, unable to process that for you. Please contact DevOps'
        }
        return response
