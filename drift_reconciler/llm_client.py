"""Shared LLM client — lazily constructed, cached, with provider fallback.

Used by both the main drift-reconciler pipeline and the trivy fix-agent
so they share the same resolution chain without duplicating the function.
"""

import logging
import os

from langchain_aws import ChatBedrockConverse

# Suppress botocore credential-discovery noise ("Both api_key and AWS
# credentials were provided …") that fires every time a Bedrock client is
# instantiated.  This is purely SDK chatter; actual auth errors still
# surface as exceptions.
logging.getLogger("botocore").setLevel(logging.ERROR)

_llm = None


def _get_llm():
    """Lazily construct the LLM client so --region from CLI and env vars
    take effect before the first call.

    Resolution order:
    1. Groq   — if ``GROQ_API_KEY`` is set, use it (free tier, no AWS).
    2. Gemini — if ``GEMINI_API_KEY`` is set, use it (no AWS dependency).
    3. Bedrock — falls back to the Bedrock-specific credentials
       (``AWS_BEDROCK_*``), or the default boto3 credential chain and
       ``AWS_REGION`` when those are unset."""
    global _llm
    if _llm is not None:
        return _llm

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        from langchain_groq import ChatGroq
        _llm = ChatGroq(
            model="openai/gpt-oss-120b",
            temperature=0.1,
            api_key=groq_key,
            # Hard cap — gpt-oss-120b's default verbosity produced
            # multi-section narratives; 1500 tokens is enough for the
            # 2-3-sentence-per-resource analysis AND a full HCL block
            # rewrite in the trivy fix-agent (shared client).
            max_tokens=1500,
        )
        return _llm

    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key:
        from langchain_google_genai import ChatGoogleGenerativeAI
        _llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0.1,
            google_api_key=gemini_key,
        )
        return _llm

    _region = os.environ.get("AWS_REGION", "us-east-1")
    kwargs: dict = dict(
        model="us.amazon.nova-pro-v1:0",
        temperature=0.1,
        region_name=os.environ.get("AWS_BEDROCK_REGION", _region),
    )
    access_key = os.environ.get("AWS_BEDROCK_ACCESS_KEY_ID", "").strip()
    secret_key = os.environ.get("AWS_BEDROCK_SECRET_ACCESS_KEY", "").strip()
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    _llm = ChatBedrockConverse(**kwargs)
    return _llm
