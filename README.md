# ecsSpotFleet
Code to initiate a spotFleet request for an ECS cluster which dynamically adjusts capacity and spins up a on-demand ASG if required

 

## Introduction

Running a container management cluster is a key requirement for a micro-service based infrastructure. ECS (Elastic Container System) is AWS's __**free**__ service which abstracts docker based container management into easily defines services and tasks. It can run on any EC2 server instance, a default AMI is available which contains a small ECS daemon which manages cluster membership and receives instructions from the ECS service to run any tasks and services that you define.

Spot-Fleet is __**another free**__ AWS service which manages a "fleet" of ECS spot instances. Spot prices are typically 80-90% of the cost of on-demand prices so provide huge cost saving potential. However that comes with a risk, if you are out-bid your instances will be terminated (after a 2-minute warning) so they are best used for ephemeral of fault-tolerant workloads. Luckily thats exactly how micro-services should be, typically they are small services that maintain no data locally and are distributed in nature - a perfect fit for running on Spot!

Spot-Fleet already provides a number of mechanisms to deal with the pitfall of instances being terminated. You define your required capacity and the type of instances you are prepared to have and Spot-Fleet will manage the distribution of bids against those instance types. If your out-bid on a specific instance type, it will only affect a small proportion of your overall capacity and Spot-Fleet will automatically spin up more instances on the other instance types to cover your capacity needs. Coupled with ECS and its ability to distribute and maintain your micro-services across whatever instances happen to be available and you have a extremely cost effective self-recovering container management system!

But what happens when all of your instance types get outbid? or if Spot-Fleet cannot obtain the needed resources? Well with CloudWatch hooked into Lambda we can automatically react to this situation and with a backup on-demand Autoscale group we can immediately start to spin up normal prices instances to cover the shortfall. Then when the spot market starts to stabilize Spot-Fleet will automatically regain the cheaper instances and again with CloudWatch and Lambda we can stop our now no longer needed on-demand instances in our Autoscale Group.

So end result. A very cost-effective container management system with the cost benefits of Spot backed up with the peace of mind that on-demand provides, win-win.

  


## Services

The below describes the AWS services used and their uses.

Service| Use| Key Info  
---|---|---  
Spot Fleet| Spot Fleet manages your fleet of spot bids. It also provides autoscaling abilities which can be configured through typical coudwatch metrics| [Autoscale for Spot Fleet](http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-fleet-automatic-scaling.html),  
  
AutoScale Group

| AutoScale is used to manage a on-demand set of instances which are only spun-up if Spot-Fleet is unable to provide required resources|   
  
ECS| 

ECS provides the container management framework. Allowing us to set up services and dictate the number of instance of containers to be maintained etc.

Metrics from ECS are used to directly influence the size of the fleet (physical instances) needed.

|   
  
Lambda| Lambda is used to regularly check that Spot-Fleet is providing the required resource. If it cannot, due to being out-bid for example, it will automatically scale up the ASG on-demand group to compensate.|   
  
CloudWatch| There are a number of metrics from Spot-Fleet and ECS which are then acted upon in CloudWatch to alarm and kick off our Lambda function.|   
  
  
  


## Architecture

![Architecture Diagram](https://github.com/andyfase/ecsSpotFleet/blob/master/spotFleet_ECS_architecture.jpg "Architecture")



Flow| Description  
---|---  
1| Spot-Fleet provides metrics into CloudWatch, including: terminatingCapacity, pendingCapacity and desiredCapacity. See [ECS metrics](http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-fleet-cloudwatch-metrics.html) 
2| CloudWatch will trigger Lambda function in two potential ways 1. Cloudwatch event. This will execute every minute (aka cron). 2. Triggered via alarm threshold of pendingCapacity being greater than zero. In the triggered alarm, Lambda is actually called via a SNS topic. With Cloudwatch event, Lambda can be triggered directly  
3| Lambda function runs and performs a number of checks   1. It downloads latest Spot-Fleet metrics from Cloudwatch 2. Ensures that the fleet hasn't been autoscaled recently 3.Check the pending capacity metric and then grows or shrinks the on-demand Autoscale Group accordingly.  
4| Outside of 1-3 spot-fleet is configured to grow or shrink the required capacity of the flee (desiredCapacity) based on the ECS metric allocatedCapacity. This allows the number of instances to be increased as the number of container instances grows and takes reservations (CPU or memory or both) on the ECS cluster  
5| As each node is spun up, it automatically joins the ECS cluster and ECS manages automatically distributing the containers across whatever instances are available. Instances come and go but ECS manages the automatic re-distribution and registration to the target group on the ALB (Application Load Balancer)  
  
  


## Implementation

### ECS Part 1

First we will create a ECS cluster
```    
aws ecs create-cluster --region <your-region> --cluster-name <your-cluster-name>  
```

For now we will leave it without tasks or services, this will be done after we have EC2 instances that have joined the cluster.

### Spot-Fleet

Spot Fleet can be setup via the console or CLI. In this example I use CLI with a JSON file:

Important parameters to consider in the file are:
    
`AllocationStrategy` - Suggested value "diversified" allows spot-fleet to diversify instances in your fleet across the various 

`TargetCapacity` - Capacity required for overall fleet. 
    
    
`SpotPrice` - Price per capacity unit (can and should be over-rideen in each LaunchSpecification
    
`TerminateInstancesWithExpiration` - Ensures that SpotFleet can also terminate instances that are no longer required
    
`WeightedCapacity` - The amount of "units" each instance provides towards your TargetCapacity. This is defined within each LaunchSpecification

  
> Pro Tip. If you create your Spot-Fleet request within the AWS console, you can download the JSON file it will use and then use it within a script or cloud-formation template.

 
Within each launch configuration ensure that their is base64 encoded user-data that will configure the ecs-agent to join your ECS cluster. This is a simple bash script:
    
```    
#!/bin/bash
echo ECS_CLUSTER=<your_cluster_name> >> /etc/ecs/ecs.config  
```  
Spof-Fleet CLI command:
```
aws ec2 request-spot-fleet --region us-east-1 --spot-fleet-request-config file:///path/to/spotfleet/json/spotFleet.json  
```  
 
### Auto-Scale Group

Create a Auto-Scale Launch Configuration and Auto-Scale group. The configuration should use the same base image (as per Spot-Fleet) but a different instance type as if Spot-Fleet cannot get the required capacity it is likely that the instance types are low on capacity . The auto-scale group will initially be created with a desired-capacity set to zero and a large cooldown value. The cooldown value is ignored when Lambda scales-out the ASG but used when it scales-in. As you pay for an hours capacity as soon as a instance is spun up we want to maximize that value - keeping the instance up as close to that hour as possible, we do this will a large cool-down value (45 minutes in this example).
    
```    
aws autoscaling create-launch-configuration --launch-configuration-name ecsDemandBackup --key-name <mykey> --image-id ami-2d1bce4d --instance-type c3.xlarge --user-data file://<ecs-user-data>  

aws autoscaling create-auto-scaling-group --auto-scaling-group-name ecsDemandBackupGroup --launch-configuration-name ecsDemandBackup --min-size 0 --max-size 200 --desired-capacity 0 --default-cooldown 2700  --availability-zones <list-of-applicable-zones>  
```
  
### Lambda

We use a very simple Lambda function to effectively do 2 things

  1. Check to see if Spot-Fleet autoscaling has kicked in within the last 10 minutes. If it has then naturally pendingCapacity on Spot-Fleet will be positive (it takes time for spot-fleet to spin up instances), so if this is the case the script terminates and does not perform any action on the ASG. This check is done by checking the desiredCapacity metric over the last X minutes to check for changes.
  2. Assuming to desiredCapacity changes have occurred checks to see if pendingCapacity is positive (i.e. there is capacity that has not be fulfilled). It will then determine the number of instances required on the on-demand ASG and attempt to alter the desiredCapacity of the ASG as appropiate
  3. When altering the desiredCapacity of the ASG, if the script is scaling-up it will instruct the ASG to ignore the cool-down period (we need to scale up ASAP in this situation). If we are scaling down then we do not ignore the cool-down. This may mean the command to set the desiredCapacity actually fails, becuase the ASG is still in the cool-down period. This is expected and normal, we don't want to terminate capacity we have already paid-for. As this function is called every minute, the scale-down will occur one minute after the cool-down period ends.

The Lambda function is written in Python and attached below

[Lambda Code](https://github.com/andyfase/ecsSpotFleet/blob/master/ecsManageSpotPendingCapacity.py)

The function will need to assume a role which has access to both CloudWatch (to get metrics) and to ASG (to adjust the desiredCapacity), example policies for this are:
    
    
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "autoscaling:SetDesiredCapacity"
                ],
                "Resource": [
                    "*"
                ]
            },
             {
                "Effect": "Allow",
                "Action": [
                    "autoscaling:DescribeAutoScalingGroups"
                ],
                "Resource": [
                    "*"
                ]
            }
        ]
    }  
  
---  
      
    
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "cloudwatch:GetMetricStatistics"
                ],
                "Resource": [
                    "*"
                ]
            }
        ]
    }  
  
---  
  
  


These policies can clearly be restricted further if required.

Create the Lambda function either in the console or on command line via
    
    
    aws lambda create-function --role 'arn:aws:iam::433468561249:role/service-role/ecsLambdaLogPendingCapacity' --zip-file fileb://ecsManageSpotPendingCapacity.py.zip --runtime python2.7 --function-name <your-function-name> --handler lambda_handler  
  
---  
  
  


### CloudWatch Event

Now we need to create a Cloudwatch to execute our Lambda function every minute 
    
    
    ## add rule
    aws events put-rule --name <name-of-your-function> --schedule-expression "rate(1 minute)" --description "cron for lambda"
    
    
    ## give access to your lambda function to cloudwatch events
    aws lambda add-permission --function-name <name-of-your-function> --statement-id <something-unique> --action 'lambda:InvokeFunction' --principal events.amazonaws.com --source-arn <arn-of-your-function>	
    
    
    ## add event target
    aws events put-targets --rule <name-of-your-rule> --targets '{"Id": "1", "Arn": "<arn-of-lamba-function>", "Input": "{\"fleetID\": \"<your-fleet-id>\"}"}'  
  
---  
  
Notice that the event had the Spot-Fleet ID cardcoded in it. This allows the Lambda function to get the fleetID as the event input and process it.

### ECS Part 2

Now we have our environment setup. ECS tasks and services can be setup. This guide does not go into detail on how to do this. But google is your friend!

  


 
