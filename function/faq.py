import numpy as np
import pandas as pd

from completion import get_completion
from embedding import construct_prompt, get_data, get_document_embeddings


def answer_query_with_context(
    query: str,
    df: pd.DataFrame,
    document_embeddings: dict[tuple[str, str], np.array],
) -> str:
    prompt = construct_prompt(
        query,
        document_embeddings,
        df
    )

    return get_completion(prompt)


def answer_faq_question(question: str) -> str:
    df = get_data()
    document_embeddings = get_document_embeddings()
    answer =  answer_query_with_context(
        question,
        df,
        document_embeddings,
    )
    return answer
