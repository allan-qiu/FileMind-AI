"""Operator-only OAuth setup. Requires official/remote gates; never asks for tokens in chat."""

import argparse
import getpass
import json

from filemind_server.baidu import BaiduCredentials, BaiduNetdiskProvider
from filemind_server.config import Settings
from filemind_server.key_store import server_secret


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["status", "authorize", "exchange", "disconnect"])
    parser.add_argument("--redirect", default="")
    args = parser.parse_args()
    settings = Settings.from_env()
    provider = BaiduNetdiskProvider(
        BaiduCredentials(settings.data_dir),
        app_root=settings.baidu_app_root,
        client_id=server_secret("FILEMIND_BAIDU_CLIENT_ID"),
        client_secret=server_secret("FILEMIND_BAIDU_CLIENT_SECRET"),
        formal_authorized=settings.baidu_formal_authorized,
        remote_enabled=settings.baidu_remote_enabled,
        writes_enabled=settings.baidu_writes_enabled,
    )
    try:
        if args.operation == "authorize":
            print(provider.authorization_url(args.redirect))
        elif args.operation == "exchange":
            provider.exchange(
                getpass.getpass("OAuth code (local only): "),
                getpass.getpass("Returned state (local only): "),
            )
            print("OAuth response stored privately. File operations not tested.")
        elif args.operation == "disconnect":
            print(json.dumps(provider.disconnect()))
        else:
            print(json.dumps(provider.status(), ensure_ascii=False, indent=2))
    except (ValueError, OSError, NotImplementedError):
        raise SystemExit(
            "Baidu configuration/authorization/protocol failure; no secrets logged."
        ) from None


if __name__ == "__main__":
    main()
