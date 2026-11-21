import time
from datetime import UTC, datetime
from pathlib import Path

from filemind.domain.local import LocalPlan, PlanEntry
from filemind.infrastructure.database import Database


class LocalRepository:
    def __init__(self, database: Database):
        self.database = database

    def begin_task(self, plan: LocalPlan):
        with self.database.transaction(True) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO local_tasks VALUES (?,?,?,?)",
                (plan.id, time.time(), "RUNNING", plan.mode),
            )

    def prepare(self, task_id: str, entry: PlanEntry, temporary: str) -> bool:
        # Summaries are not required to undo and must not be copied into the operation log.
        payload = entry.model_copy(update={"file": entry.file.model_copy(update={"summary": ""})})
        with self.database.transaction(True) as conn:
            if conn.execute("SELECT id FROM local_operations WHERE id=?", (entry.id,)).fetchone():
                return False
            conn.execute(
                (
                    "INSERT INTO "
                    "local_operations(id,task_id,payload,state,temp_path,updated_at) "
                    "VALUES (?,?,?,?,?,?)"
                ),
                (entry.id, task_id, payload.model_dump_json(), "PREPARED", temporary, time.time()),
            )
        return True

    def state(self, op_id: str, state: str, error: str = ""):
        with self.database.transaction(True) as conn:
            row = conn.execute("SELECT * FROM local_operations WHERE id=?", (op_id,)).fetchone()
            if row is None:
                raise ValueError("操作日志不存在。")
            history_id = row["history_id"]
            if state == "COMPLETED" and not history_id:
                entry = PlanEntry.model_validate_json(row["payload"])
                cursor = conn.execute(
                    (
                        "INSERT INTO "
                        "operation_history(action,source_path,target_path,created_at,status) "
                        "VALUES (?,?,?,?,?)"
                    ),
                    (
                        entry.action,
                        entry.file.path,
                        str(Path(entry.target_dir) / entry.target_name),
                        datetime.now(UTC).isoformat(),
                        state,
                    ),
                )
                history_id = cursor.lastrowid
            if history_id:
                conn.execute(
                    "UPDATE operation_history SET status=? WHERE id=?", (state, history_id)
                )
            conn.execute(
                "UPDATE local_operations SET state=?,error=?,history_id=?,updated_at=? WHERE id=?",
                (state, error, history_id, time.time(), op_id),
            )

    def finish_task(self, task_id: str, status: str):
        with self.database.transaction(True) as conn:
            conn.execute("UPDATE local_tasks SET status=? WHERE id=?", (status, task_id))

    def tasks(self):
        with self.database.transaction() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT t.*,count(o.id) count,"
                    "sum(CASE WHEN o.state='UNDONE' THEN 1 ELSE 0 END) undone_count "
                    "FROM local_tasks t LEFT JOIN local_operations o "
                    "ON o.task_id=t.id GROUP BY t.id ORDER BY created_at DESC LIMIT 200"
                ).fetchall()
            ]

    def operations(self, task_id: str | None = None):
        with self.database.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM local_operations"
                + (" WHERE task_id=?" if task_id else "")
                + " ORDER BY updated_at",
                (task_id,) if task_id else (),
            ).fetchall()
        return [dict(row) for row in rows]
