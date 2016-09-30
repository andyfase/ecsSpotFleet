'''

'''
from __future__ import division

import boto3
import json
import logging
from datetime import timedelta
from datetime import datetime


logger = logging.getLogger()
logger.setLevel(logging.INFO)

asgPerServerVCPU = 4
asgGroupName = 'ecsSpotAutoScale'
defaultTargetCapacity = 32 ## Note this is only used if we cant actually retrieve it


def checkFleetScalled(fleetID):
    ### Function checks if the fleet has been manually scalled in the last 15 minutes
    ### We do this to stop spinning up demand instances which spot can cover if we have just asked it to scale

    cw = boto3.client('cloudwatch')
    try:
        data = cw.get_metric_statistics(
                      Namespace='AWS/EC2Spot',
                      MetricName='TargetCapacity',
                      Dimensions=[
                        {
                            'Name': 'FleetRequestId',
                            'Value': fleetID
                        },
                      ],
                      StartTime=datetime.utcnow() - timedelta(seconds=600),
                      EndTime=datetime.utcnow(),
                      Period=60,
                      Statistics=['Maximum'],
                      Unit='Count')
    except Exception, e:
        ## on error assume it has
        logger.info(str(e))
        return True

    firstMax = None
    for max in data['Datapoints']:
        if firstMax is not None and firstMax != max['Maximum']:
            return True
        if firstMax is None:
            firstMax = max['Maximum']

    return False

def getECSMetrics(fleetID):
    cw = boto3.client('cloudwatch')
    metrics = {'PendingCapacity': None,'TargetCapacity': None }
    try:
        for metric in metrics:
            data = cw.get_metric_statistics(
                      Namespace='AWS/EC2Spot',
                      MetricName=metric,
                      Dimensions=[
                        {
                            'Name': 'FleetRequestId',
                            'Value': fleetID
                        },
                      ],
                      StartTime=datetime.utcnow() - timedelta(seconds=60),
                      EndTime=datetime.utcnow(),
                      Period=60,
                      Statistics=['Maximum'],
                      Unit='Count')
            metrics[metric] = data['Datapoints'][0]['Maximum']
    except Exception, e:
        ## If we failed to get metrics assume something is seriously wrong with the Fleet
        logger.info(str(e))
        metrics = {'PendingCapacity': defaultTargetCapacity,'TargetCapacity': defaultTargetCapacity }
    return metrics

def setASGDesired(numInstances, cooldown):
    asg = boto3.client('autoscaling')
    try:
        asg.set_desired_capacity(
            AutoScalingGroupName=asgGroupName,
            DesiredCapacity=numInstances,
            HonorCooldown=cooldown
        )
    except Exception, e:
        logger.info("Failed to update desired capacity: " + str(numInstances) + ", error: " + str(e))
    return

def describeASG():
    asg = boto3.client('autoscaling')
    return asg.describe_auto_scaling_groups(
        AutoScalingGroupNames=[
            asgGroupName,
        ],
        MaxRecords=1
    )

def lambda_handler(event, context):

     ### we have multiple events triggering function so determine where to get fleetID from
     fleetID = None
     try:
         # Read actual Alarm into variable, and decode into object
         alert = json.loads(event['Records'][0]['Sns']['Message'])
         fleetID  = alert['Trigger']['Dimensions'][0]['value']
     except Exception, e:
         try:
             fleetID = event['fleetID']
         except Exception, e:
             logger.info("Failed to extract fleetID")
             logger.info(event)
             return

     ### check if fleet has been recently scalled and if so ignore trigger
     if checkFleetScalled(fleetID):
        logger.info("yup ignoring")
        return

     ### get current metrics and calculate number of ASG instsnces required (if any)
     metrics = getECSMetrics(fleetID)

     instancesRequired = int((metrics['PendingCapacity'] / asgPerServerVCPU) + 0.999999) ## note we always round up!
     logger.info(metrics['PendingCapacity'])
     logger.info(instancesRequired)
     ### get current size of ASG to determine if we going up or down
     asg = describeASG()
     currentCapacity = asg['AutoScalingGroups'][0]['DesiredCapacity']

     if instancesRequired > currentCapacity:
         logger.info("Scaling UP to " + str(instancesRequired) + " instances")
         setASGDesired(instancesRequired, False) ## override cooldown period - we need to scale up now!
     elif instancesRequired < currentCapacity:
         logger.info("Scaling DOWN to " + str(instancesRequired) + " instances")
         setASGDesired(instancesRequired, True) ## honor cooldown, so we get max value from instances already paid for!
     ## Do nothing wanted and current capacity are same!
