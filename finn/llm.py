"""Thin wrapper over the Anthropic API: text/image content, JSON replies, usage ledger."""
import base64
import json
import os
import re
import time

from . import config, db


def default_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic()


def image_block(png: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": base64.b64encode(png).decode()}}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def cost(model: str, usage: dict) -> float:
    pin, pout = config.MODEL_PRICES_PER_MTOK.get(model, (0.0, 0.0))
    return usage["input_tokens"] / 1e6 * pin + usage["output_tokens"] / 1e6 * pout


def log_usage(model: str, purpose: str, n_items: int, usage: dict) -> float:
    c = cost(model, usage)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO llm_usage(ts,model,purpose,n_items,input_tokens,output_tokens,cost_usd) VALUES (?,?,?,?,?,?,?)",
            (time.time(), model, purpose, n_items, usage["input_tokens"], usage["output_tokens"], c))
    return c


def call_json(client, model: str, system: str, content: list[dict], max_tokens: int, purpose: str):
    """Returns (parsed_json_or_None, usage, cost). Never raises on bad JSON."""
    resp = client.messages.create(model=model, max_tokens=max_tokens, temperature=0, system=system,
                                  messages=[{"role": "user", "content": content}])
    text = "".join(getattr(b, "text", "") for b in resp.content)
    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    c = log_usage(model, purpose, 1, usage)
    m = re.search(r"[\[{].*[\]}]", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        data = None
    return data, usage, c
