import argparse
import getpass
import sys

import uvicorn

from filemind_server.accounts import create_account
from filemind_server.config import Settings
from filemind_server.database import CentralDatabase
from filemind_server.models import Registration


def main() -> None:
    parser = argparse.ArgumentParser(description="FileMind local central service")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    commands.add_parser("migrate")
    admin = commands.add_parser("create-admin")
    admin.add_argument("--username", required=True)
    admin.add_argument("--password-stdin", action="store_true")
    args = parser.parse_args()
    if args.command == "serve":
        uvicorn.run(
            "filemind_server.app:create_app",
            factory=True,
            host="127.0.0.1",
            port=args.port,
            workers=1,
            limit_concurrency=32,
            timeout_keep_alive=5,
            access_log=False,
        )
        return
    settings = Settings.from_env()
    db = CentralDatabase(settings.data_dir / "central.sqlite3")
    db.migrate()
    if args.command == "migrate":
        print("Central schema ready; V1 database unchanged.")
        return
    password = (
        sys.stdin.readline().rstrip("\r\n")
        if args.password_stdin
        else getpass.getpass("Password: ")
    )
    try:
        data = Registration(username=args.username, display_name="系统管理员", password=password)
    except ValueError:
        raise SystemExit("用户名需为 3–32 位字母/数字/下划线，密码至少 10 个字符。") from None
    with db.transaction(write=True) as conn:
        create_account(conn, data, role="system_admin")
    print("Administrator created. No default account or password is provided.")


if __name__ == "__main__":
    main()
