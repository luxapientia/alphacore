#!/usr/bin/env python3
import os
import sys
import traceback

from openai import OpenAI

def main() -> int:
    # Prefer your AlphaCore env var if set, but also support the standard var.
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("ALPHACORE_OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("ALPHACORE_OPENAI_BASE_URL")
    model = os.getenv("ALPHACORE_TASK_PROMPT_MODEL", "gpt-5-mini")

    if not api_key and not os.getenv("OPENAI_API_KEY"):
        print("ERROR: missing OPENAI_API_KEY (or ALPHACORE_OPENAI_API_KEY).", file=sys.stderr)
        return 2

    # Helpful debug so you can tell if you're accidentally pointing at a local base_url.
    print(f"Using model: {model}")
    print(f"Using base_url: {base_url or '(default OpenAI)'}")

    client_kwargs = {}
    if api_key:
        client_kwargs["api_key"] = api_key
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)

    prompt = "Say 'hello world' and include a random 6-digit number."
    resp = client.responses.create(
        model=model,
        input=prompt,
        max_output_tokens=20000,
    )

    print("\n--- output_text ---")
    print(resp.output_text)

    # Extra debug: usage + id/model (handy for proving you got a real response)
    usage = getattr(resp, "usage", None)
    print("\n--- debug ---")
    print(f"id: {getattr(resp, 'id', None)}")
    print(f"model: {getattr(resp, 'model', None)}")
    if usage:
        print(f"usage: {usage}")

    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
