#!/bin/python3
"""
This is a Slack app that uses the Slack Events API and
AWS Lambda to provide a Slack bot that can be used to
answer questions based on articles in a knowledge base.
"""
import json

from slack_bolt import App
from slack_bolt.adapter.aws_lambda import SlackRequestHandler
from slack_sdk.errors import SlackApiError

app = App(process_before_response=True)


def send_message_to_user(user_id, message):
    try:
        # Open a direct message channel with the user
        response = app.client.conversations_open(users=user_id)
        channel_id = response['channel']['id']  # type: ignore

        # Send the message to the user
        app.client.chat_postMessage(channel=channel_id, text=message)

    except SlackApiError as e:
        print(f"Error sending message: {e}")


@app.event('message')
def handle_message_events(body, say):
    # Only importing here to optimize Lambda start up time
    from completion import get_completion
    from embedding import get_data, get_document_embeddings, construct_prompt

    event = body['event']
    message = event['text']

    # It's possible that the message is from a bot user.
    # If so, we don't want to respond to it or it might cause
    # a infinite loop - and putting your AWS bill on fire!
    is_user_message = (
        event['type'] == 'message' and
        (not event.get('subtype')) and
        (not event.get('bot_id'))
    )
    if not is_user_message:
        return

    print(f'User said `{message}`.')

    df = get_data()
    embeddings = get_document_embeddings()

    prompt_with_articles_as_context = construct_prompt(message, embeddings, df)

    reply_text = get_completion(prompt_with_articles_as_context)

    say(reply_text)


@app.command('/submit_train_article')
def handle_submit_train_article_command(ack, respond, command):
    ack()

    try:
        _response = app.client.views_open(
            trigger_id=command['trigger_id'],
            view={
                'type': 'modal',
                'callback_id': 'submit_train_article_view',
                'title': {'type': 'plain_text', 'text': 'Feed new article to Bot'},
                'blocks': [
                    {
                        'type': 'input',
                        'block_id': 'title_block',
                        'label': {'type': 'plain_text', 'text': 'Title'},
                        'element': {'type': 'plain_text_input', 'action_id': 'title_input'},
                    },
                    {
                        'type': 'input',
                        'block_id': 'heading_block',
                        'label': {'type': 'plain_text', 'text': 'Heading'},
                        'element': {'type': 'plain_text_input', 'action_id': 'heading_input'},
                    },
                    {
                        'type': 'input',
                        'block_id': 'content_block',
                        'label': {'type': 'plain_text', 'text': 'Content'},
                        'element': {'type': 'plain_text_input', 'multiline': True, 'action_id': 'content_input'},
                    },
                ],
                'submit': {'type': 'plain_text', 'text': 'Submit'},
            },
        )
    except Exception as e:
        print('Error opening modal: {}'.format(e))

    respond('Modal opened')


@app.view('submit_train_article_view')
def handle_new_train_article_submission(ack, body):
    values = body['view']['state']['values']
    title = values['title_block']['title_input']['value']
    heading = values['heading_block']['heading_input']['value']
    content = values['content_block']['content_input']['value']

    print(f'Adding training data: {title}, {heading}, {content}')

    ack(text='Training data submitted!')

    # "Lazy loading" to avoid long Lambda start up times
    from embedding import process_new_article
    result = process_new_article(title, heading, content)

    if result:
        # send message with Slack client `client`
        send_message_to_user(
            body['user']['id'],
            f'New training data added: with index ({title}, {heading})',
        )
    else:
        send_message_to_user(
            body['user']['id'],
            'Something went wrong when adding new training data!',
        )


def lambda_handler(event, context):
    # Sometimes Slack might fire a 2nd attempt, etc
    # if the first attempt fails or expires after some time.
    # In that case we can try to achieve idempotency by checking
    # the X-Slack-Retry-Num and skip processing the event, so that
    # only the first attempt is processed.
    # Ref: https://api.slack.com/apis/connections/events-api#retries
    if event['headers'].get('x-slack-retry-num'):
        return {
            'statusCode': 200,
            'body': 'ok',
        }

    # For debugging
    print(json.dumps(event))

    slack_handler = SlackRequestHandler(app=app)
    return slack_handler.handle(event, context)
