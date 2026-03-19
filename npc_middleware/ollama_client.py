import ollama

from npc_middleware.config import EMBED_MODEL, LLM_MODEL, OLLAMA_BASE_URL

_client = ollama.Client(host=OLLAMA_BASE_URL)


def embed(text: str) -> list[float]:
    resp = _client.embed(model=EMBED_MODEL, input=text)
    return resp["embeddings"][0]


def generate(prompt: str, system: str = "") -> str:
    resp = _client.chat(
        model=LLM_MODEL,
        messages=[
            *([] if not system else [{"role": "system", "content": system}]),
            {"role": "user", "content": prompt},
        ],
    )
    return resp["message"]["content"]


def generate_stream(prompt: str, system: str = ""):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    for chunk in _client.chat(model=LLM_MODEL, messages=messages, stream=True):
        token = chunk["message"]["content"]
        if token:
            yield token
