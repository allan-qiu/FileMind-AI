"Seed ONLY the isolated browser QA database. Never touches the normal server DB."

from pathlib import Path

from filemind_server.accounts import create_account
from filemind_server.database import CentralDatabase
from filemind_server.models import Registration

root = Path(__file__).resolve().parents[1]
db = CentralDatabase(root / ".artifacts" / "browser-central" / "central.sqlite3")
db.migrate()
with db.transaction(write=True) as conn:
    for username, display, role in (
        ("qa_user", "联调测试用户", "user"),
        ("qa_admin", "联调管理员", "system_admin"),
    ):
        if not conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
            create_account(
                conn,
                Registration(
                    username=username, display_name=display, password="Browser-QA-only-2026!"
                ),
                role,
            )
print("Isolated QA accounts ready in .artifacts/browser-central only.")
