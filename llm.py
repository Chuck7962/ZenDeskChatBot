"""
llm.py - LLM call function (Azure AI Foundry - Claude)
Portable: swap the endpoint/key/model to move to any provider.
"""

import os
import json
import requests

AZURE_ENDPOINT = os.getenv("AZURE_AI_ENDPOINT", "https://YOUR-RESOURCE.services.ai.azure.com")
AZURE_API_KEY = os.getenv("AZURE_AI_API_KEY", "YOUR_API_KEY_HERE")
AZURE_MODEL = os.getenv("AZURE_AI_MODEL", "claude-sonnet-4-20250514")
AZURE_API_VERSION = os.getenv("AZURE_AI_API_VERSION", "2024-05-01-preview")


def call_llm(messages, system_prompt="", knowledge_context="", max_tokens=4096, temperature=0.7):
    """
    Call Azure AI Foundry (Claude model) with conversation history.

    messages:          list of {"role": "user"/"assistant", "content": "..."}
    system_prompt:     system instructions
    knowledge_context: full text from training docs injected into system message
    """
    # Build system message
    system_parts = []
    if system_prompt.strip():
        system_parts.append(system_prompt.strip())
    if knowledge_context.strip():
        system_parts.append(
            "=== KNOWLEDGE BASE ===\n"
            "Use this information to answer questions. If the answer isn't here, say so.\n\n"
            f"{knowledge_context.strip()}\n"
            "=== END KNOWLEDGE BASE ==="
        )
    full_system = "\n\n".join(system_parts) if system_parts else "You are a helpful assistant."

    url = f"{AZURE_ENDPOINT.rstrip('/')}/models/chat/completions?api-version={AZURE_API_VERSION}"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AZURE_API_KEY}",
    }

    payload = {
        "model": AZURE_MODEL,
        "messages": [{"role": "system", "content": full_system}] + messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        error_msg = str(e)
        if hasattr(e, "response") and e.response is not None:
            try:
                error_msg += " | " + json.dumps(e.response.json())
            except Exception:
                error_msg += f" | Status {e.response.status_code}"
        print(f"[LLM ERROR] {error_msg}")
        return f"Error connecting to AI service. ({error_msg})"
