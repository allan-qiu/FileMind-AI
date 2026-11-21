"""Explicit new-key model-list preflight. Does not call chat/completions or write keys."""

import argparse
import json
import os

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-new-key", action="store_true")
    args = parser.parse_args()
    key = os.environ.get("FILEMIND_ACCEPTANCE_NEW_KEY", "")
    if not args.confirm_new_key or not key:
        raise SystemExit(
            "Set a newly rotated FILEMIND_ACCEPTANCE_NEW_KEY locally "
            "and explicitly confirm it. No request sent."
        )
    try:
        with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
            response = client.get(
                "https://api.deepseek.com/models", headers={"Authorization": "Bearer " + key}
            )
        if not response.is_success:
            raise SystemExit(
                f"Model list rejected: HTTP {response.status_code}; no response body logged."
            )
        ids = [model["id"] for model in response.json()["data"]]
        print(
            json.dumps(
                {
                    "model_list_checked": True,
                    "deepseek_flash_available": "deepseek-flash" in ids,
                    "paid_classification_called": False,
                }
            )
        )
    except (httpx.HTTPError, ValueError, KeyError):
        raise SystemExit(
            "Model-list request failed; credentials and response body omitted."
        ) from None


if __name__ == "__main__":
    main()
