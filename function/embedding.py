import os
from typing import Union

import boto3
import numpy as np
import openai
import pandas as pd
import tiktoken
import time
from numpy.typing import ArrayLike

# # ===== Data File related =====
DATA_BUCKET = os.environ['DATAFILE_S3_BUCKET']
# `/tmp/` is the only writable directory in AWS Lambda.
# For local debugging, you can set this to a local directory like '../'.
LOCAL_DATA_PATH = os.environ.get('LOCAL_DATA_PATH', '/tmp/')
DATA_FILE_PATH = 'data/articles.csv'
EMBEDDINGS_FILE_PATH = 'data/document_embeddings.csv'

# ===== Embedding Related =====
EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL', 'text-embedding-ada-002')
MAX_SECTION_LEN = 2048  # together with completion.COMPLETION_MAX_TOKENS should not exceed 4096
SEPARATOR = '\n---\n'  # For articles
EMBEDDING_ENCODING = tiktoken.encoding_for_model(EMBEDDING_MODEL)
separator_len = len(EMBEDDING_ENCODING.encode(SEPARATOR))

DATA_FILE_INDEX = ['title', 'heading']

# It's a hacky way to perserve a 'Global variable' so that loading of data
# is only done once per Lambda container.
active_data = {}


print('Start processing functions')


def get_data_bucket():
    s3 = boto3.resource('s3', region_name=os.environ['AWS_REGION'])
    return s3.Bucket(DATA_BUCKET)  # type: ignore


def download_data():
    """
    Download the data files from S3 if they don't exist locally.
    Note that this function should only be called when necessary to save unnecessary API calls.
    """
    if not os.path.exists(LOCAL_DATA_PATH + 'data'):
        os.makedirs(LOCAL_DATA_PATH + 'data')

    for relative_file_path in [DATA_FILE_PATH, EMBEDDINGS_FILE_PATH]:
        full_path = LOCAL_DATA_PATH + relative_file_path
        try:
            with open(full_path, 'r') as f:
                pass
        except FileNotFoundError:
            data_bucket = get_data_bucket()
            data_bucket.download_file(relative_file_path, full_path)
            print(f'Downloaded {relative_file_path} from S3 to {full_path}.')


def load_datafile(fname: str) -> pd.DataFrame:
    # check if fname file exists, if not, call `download_data()`
    if not os.path.exists(fname):
        download_data()

    df = pd.read_csv(fname, header=0)
    df = df.set_index(DATA_FILE_INDEX)
    return df


def load_embeddings(fname: str) -> dict[tuple[str, str], list[float]]:
    """
    Read the document embeddings and their keys from a CSV.

    fname is the path to a CSV with exactly these named columns:
        'title', 'heading', '0', '1', ... up to the length of the embedding vectors.
    """
    if not os.path.exists(fname):
        download_data()

    df = pd.read_csv(fname, header=0)
    df = df.set_axis(DATA_FILE_INDEX + list(df.columns[2:]), axis=1)
    max_dim = max([int(c) for c in df.columns if c != 'title' and c != 'heading'])
    return {
           (r.title, r.heading): [r[str(i)] for i in range(max_dim + 1)] for _, r in df.iterrows()
    }


def get_data():
    if active_data.get('df') is None:
        active_data['df'] = load_datafile(LOCAL_DATA_PATH + DATA_FILE_PATH)
    return active_data['df']


def get_document_embeddings():
    if active_data.get('document_embeddings') is None:
        active_data['document_embeddings'] = load_embeddings(LOCAL_DATA_PATH + EMBEDDINGS_FILE_PATH)
    return active_data['document_embeddings']


def get_embedding(text: str, model: str=EMBEDDING_MODEL) -> list[float]:
    result = openai.Embedding.create(
      model=model,
      input=text
    )
    return result['data'][0]['embedding']  # type: ignore


def compute_datafile_tokens(df: pd.DataFrame) -> pd.DataFrame:
    '''
    Compute the number of tokens in each row of the dataframe.

    The dataframe should have the indices 'title' and 'heading', and a column named 'content'.

    Return the dataframe with an additional column named 'tokens'.
    '''

    df['tokens'] = df.content.apply(lambda content: len(EMBEDDING_ENCODING.encode(content)))
    return df


def compute_doc_embeddings(df: pd.DataFrame) -> dict[tuple[str, str], list[float]]:
    '''
    Create an embedding for each row in the dataframe using the OpenAI Embeddings API.

    The dataframe should have the indices 'title' and 'heading', and a column named 'content'.

    Return a dictionary that maps between each embedding vector and the index of the row that it corresponds to.

    A waiting time of 3 seconds is added between each API call to avoid rate limiting.
    '''
    emb = {}
    for idx, r in df.iterrows():
        emb[idx] = get_embedding(r.content)
        time.sleep(3)
        print(f"Processed '{idx}' document for embedding")
    return emb


def prepare_document_embeddings(sync_to_s3: bool = False):
    """
    This function performs the initial training on the initial dataset.
    This should only be called once.
    """
    df = get_data()
    df = compute_datafile_tokens(df)
    df.to_csv(LOCAL_DATA_PATH + DATA_FILE_PATH, index_label=DATA_FILE_INDEX)

    # This will take a  very long time
    document_embeddings = compute_doc_embeddings(df)
    pd.DataFrame(document_embeddings).T.to_csv(
        LOCAL_DATA_PATH + EMBEDDINGS_FILE_PATH,
        index_label=DATA_FILE_INDEX
    )

    if sync_to_s3:
        data_bucket = get_data_bucket()
        # upload back the datafiles to S3
        for file in [DATA_FILE_PATH, EMBEDDINGS_FILE_PATH]:
            response = data_bucket.upload_file(LOCAL_DATA_PATH + file, file)
            print(response)


"""
Functions from below are related to the classification of the closest documents
and the similarity between a given question and the documents
"""


def vector_similarity(
    x: Union[list[float], ArrayLike],
    y: Union[list[float], ArrayLike],
) -> float:
    '''
    Returns the similarity between two vectors.

    Because OpenAI Embeddings are normalized to length 1, the cosine similarity is the same as the dot product.
    '''
    return np.dot(np.array(x), np.array(y))


def order_document_sections_by_query_similarity(
    query: str,
    contexts: dict[tuple[str, str], ArrayLike],
) -> list[tuple[float, tuple[str, str]]]:
    '''
    Find the query embedding for the supplied query, and compare it against all of the pre-calculated document embeddings
    to find the most relevant sections.

    Return the list of document sections, sorted by relevance in descending order.
    '''
    query_embedding = get_embedding(query)

    return list(sorted([
        (
            vector_similarity(query_embedding, doc_embedding),
            doc_index,
        )
        for doc_index, doc_embedding in contexts.items()
    ], reverse=True))


def construct_prompt(question: str, context_embeddings: dict, df: pd.DataFrame) -> str:
    '''
    Fetch relevant articles
    '''
    most_relevant_document_sections = order_document_sections_by_query_similarity(
        question,
        context_embeddings,
    )

    chosen_sections = []
    chosen_sections_len = 0
    chosen_sections_indexes = []

    for _, section_index in most_relevant_document_sections:
        # Add contexts until we run out of space.
        document_section = df.loc[section_index]

        chosen_sections_len += document_section.tokens + separator_len  # type: ignore
        if chosen_sections_len > MAX_SECTION_LEN:
            break

        chosen_sections.append(SEPARATOR + document_section.content)  # type: ignore
        chosen_sections_indexes.append(str(section_index))

    # Useful diagnostic information
    print(f'Selected {len(chosen_sections)} document sections: [', ', '.join(chosen_sections_indexes), ']')

    header = (
        'Answer the question as truthfully as possible using the provided context, ' +
        'and if the answer is not contained within the text below, ' +
        'say \'I don\'t know as I am configured to answer based on my training data.\n' +
        'Context:\n'
    )

    return header + ''.join(chosen_sections) + SEPARATOR +'\n\n Q: ' + question + '\n A:'


def process_new_article(title: str, heading: str, content: str) -> bool:
    new_faq_index = (title, heading)

    df = get_data()
    document_embeddings = get_document_embeddings()

    # check if the index already exists
    if new_faq_index in df.index:
        print(f'FAQ already exists: {new_faq_index}')
        return False

    new_faq_data = {
        'content': content,
        'tokens': len(EMBEDDING_ENCODING.encode(content)),
    }
    df_new = pd.DataFrame([new_faq_data], index=[new_faq_index])

    new_article_embedding = get_embedding(content)

    print(f'Processed new article for {new_faq_index}')

    df = pd.concat([df, df_new])
    df.to_csv(LOCAL_DATA_PATH + DATA_FILE_PATH, index_label=DATA_FILE_INDEX)

    document_embeddings[(title, heading)] = new_article_embedding
    pd.DataFrame(document_embeddings).T.to_csv(LOCAL_DATA_PATH + EMBEDDINGS_FILE_PATH, index_label=DATA_FILE_INDEX)

    # upload back the datafiles to S3
    data_bucket = get_data_bucket()
    for file in [DATA_FILE_PATH, EMBEDDINGS_FILE_PATH]:
        _response = data_bucket.upload_file(LOCAL_DATA_PATH + file, file)

    # update the active data in global variable
    active_data['df'] = df
    active_data['document_embeddings'] = document_embeddings

    print(f'Re-uploaded data file onto S3 for {new_faq_index}.')

    return True
