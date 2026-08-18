"""One-time GitHub Copilot login for the LiteLLM ``github_copilot`` provider.

Run this once; LiteLLM stores the GitHub access token under
``GITHUB_COPILOT_TOKEN_DIR`` (mounted to ``./.copilot-auth`` on the host by
docker-compose), so the backend can call Copilot models on every later start
without re-authenticating.

Usage (from the project root)::

    docker compose exec backend python copilot_login.py

The script prints a URL and a one-time code. Open the URL in your browser,
enter the code, and approve. The script then exchanges it for a token and
verifies a real chat completion.
"""
from __future__ import annotations

import os
import sys

from litellm.llms.github_copilot.authenticator import Authenticator


def main() -> int:
    auth = Authenticator()
    print(f"Token directory : {auth.token_dir}")
    print("Requesting GitHub device code (a URL + code will appear below)...\n", flush=True)

    try:
        access_token = auth.get_access_token()
    except Exception as exc:  # noqa: BLE001 - surface the reason to the operator
        print(f"\nFAILED to obtain a GitHub access token: {type(exc).__name__}: {exc}")
        return 1
    print(f"\nAccess token acquired and saved to {auth.access_token_file}")

    try:
        api_key = auth.get_api_key()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to exchange the access token for a Copilot API key: {exc}")
        return 1
    print(f"Copilot API key acquired and cached in {auth.api_key_file}")
    print(f"   (token starts with {api_key[:8]}...)")

    model = os.environ.get("LITELLM_MODEL", "github_copilot/gpt-4o")
    print(f"\nVerifying a live completion with model '{model}' ...", flush=True)
    from litellm import completion

    response = completion(
        model=model,
        messages=[{"role": "user", "content": "Reply with exactly: lineage-ok"}],
        extra_headers={
            "editor-version": "vscode/1.85.1",
            "Copilot-Integration-Id": "vscode-chat",
        },
    )
    print("Model replied:", response["choices"][0]["message"]["content"])
    print("\nGitHub Copilot is configured. Restart the backend to pick it up:")
    print("   docker compose restart backend")
    return 0


if __name__ == "__main__":
    sys.exit(main())
