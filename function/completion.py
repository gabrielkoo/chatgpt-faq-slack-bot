import os

import openai

COMPLETIONS_MODEL = os.environ.get('COMPLETIONS_MODEL', 'gpt-3.5-turbo')
COMPLETION_MAX_TOKENS = 1024
COMPLETIONS_API_PARAMS = {
    'temperature': 0.0,
    'max_tokens': COMPLETION_MAX_TOKENS,  # allocate more tokens for the context
    'model': COMPLETIONS_MODEL,
}


def get_completion(prompt):
    completion_response = openai.ChatCompletion.create(
        messages=[
            {
                'role': 'system',
                'content': (
                    'You are a Slack chatbot, a bot that is trained to answer based on a list ' +
                    'of articles about a particular topic in an organization.'
                ),
            },
            {'role': 'user', 'content': prompt},
        ],
        **COMPLETIONS_API_PARAMS
    )

    try:
        return completion_response['choices'][0]['message']['content']  # type: ignore
    except Exception as e:
        print(f'Completion Error: {e}')
        return 'An error occured with my Completion API call. Please try again.'
