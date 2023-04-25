#!/bin/bash

export PYTHON_RUNTIME=${PYTHON_RUNTIME:-python3.9}

# Check if AWS Creentials and Region have been set
if [ -z "$AWS_ACCESS_KEY_ID" ] || [ -z "$AWS_SECRET_ACCESS_KEY" ]; then
    echo 'Please set up your AWS credentials'
    exit 1
fi
if [ -z "$AWS_REGION" ]; then
    echo 'Please set up your AWS region'
    exit 1
fi

# Check if docker is running
docker info > /dev/null
if [ $? -ne 0 ]; then
    echo 'Docker is not accessible. Please start docker in order to build the Lambda Layer for Python.'
    exit 1
fi

# Set secrets in your `.env` file into this shell session
. ./.env

# Build the Lambda Layer by installing the dependencies with the build image
sam build \
    --use-container \
    --template template.yml \
    --build-image "amazon/aws-sam-cli-build-image-$PYTHON_RUNTIME"

# Change `--stack-name` to your own stack name if needed
sam deploy \
    --region $AWS_REGION \
    --capabilities CAPABILITY_IAM \
    --stack-name chatgpt-faq-slack-bot \
    --resolve-s3 \
    --parameter-overrides \
    "ParameterKey=PythonRunTime,ParameterValue=$PYTHON_RUNTIME ParameterKey=OpenaiApiKey,ParameterValue=$OPENAI_API_KEY ParameterKey=SlackBotToken,ParameterValue=$SLACK_BOT_TOKEN ParameterKey=SlackSigningSecret,ParameterValue=$SLACK_SIGNING_SECRET"
