import json
import threading
import time
import uuid
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from filemind.infrastructure.file_analysis import FileAnalyzer, linked
from filemind.infrastructure.local_repository import LocalRepository
from filemind.services.classification import ClassificationService
from filemind.services.local_organizer import LocalOrganizer


class Changes(FileSystemEventHandler):
    def __init__(self, callback):
        self.callback = callback

    def on_created(self, event):
        if not event.is_directory:
            self.callback(event.src_path)

    on_modified = on_created

    def on_moved(self, event):
        if not event.is_directory:
            self.callback(event.dest_path)


class MonitorManager:
    "One bounded debounce worker for all actual watchdog observers; never a daemon service."

    def __init__(self, repository: LocalRepository, organizer: LocalOrganizer, stable_seconds=2.0):
        self.repository, self.organizer = repository, organizer
        self.stable_seconds = stable_seconds
        self.observers = {}
        self.active_rules = {}
        self.pending, self.seen = {}, {}
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.worker = None

    def rules(self):
        with self.repository.database.transaction() as conn:
            rows = [
                dict(row) for row in conn.execute("SELECT * FROM local_monitor_rules").fetchall()
            ]
        for row in rows:
            row["running"] = row["id"] in self.observers
            row["rules"] = json.loads(row["rules"])
        return rows

    def event(self, rule_id, path, state, message=""):
        with self.repository.database.transaction(True) as conn:
            conn.execute(
                (
                    "INSERT INTO "
                    "local_monitor_events(rule_id,path,state,message,created_at) VALUES "
                    "(?,?,?,?,?)"
                ),
                (rule_id, str(path), state, message, time.time()),
            )
            conn.execute(
                "DELETE FROM local_monitor_events WHERE id NOT IN (SELECT id FROM "
                "local_monitor_events ORDER BY id DESC LIMIT 2000)"
            )

    def events(self):
        with self.repository.database.transaction() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM local_monitor_events ORDER BY id DESC LIMIT 200"
                )
            ]

    def add(self, source: Path, destination: Path, auto_execute=False, rules=()):
        from filemind.infrastructure.file_analysis import reject_links

        reject_links(source.absolute())
        reject_links(destination.absolute())
        source, destination = source.resolve(), destination.resolve()
        if len(self.rules()) >= 16:
            raise ValueError("本客户端最多保存 16 个监控策略，请先移除不再使用的策略。")
        if not source.is_dir() or source == destination:
            raise ValueError("请选择存在的监控目录和不同的整理目标目录。")
        for row in self.rules():
            other = Path(row["source"])
            if source == other or source in other.parents or other in source.parents:
                raise ValueError("不允许重复或重叠的监控目录，避免同一文件被多次整理。")
        rule_id = str(uuid.uuid4())
        with self.repository.database.transaction(True) as conn:
            conn.execute(
                "INSERT INTO local_monitor_rules VALUES (?,?,?,?,?,?)",
                (
                    rule_id,
                    str(source),
                    str(destination),
                    0,
                    int(auto_execute),
                    json.dumps(list(rules)),
                ),
            )
        return rule_id

    def start(self, rule_id: str):
        rule = next(row for row in self.rules() if row["id"] == rule_id)
        if not Path(rule["source"]).is_dir():
            raise ValueError("监控源目录不存在。")
        with self.lock:
            if rule_id in self.observers:
                return
            observer = Observer()
            observer.schedule(
                Changes(lambda path: self.enqueue(rule, path)), rule["source"], recursive=True
            )
            observer.start()
            self.observers[rule_id] = observer
            self.active_rules[rule_id] = rule
            if not self.worker or not self.worker.is_alive():
                self.stop_event.clear()
                self.worker = threading.Thread(
                    target=self.run, name="FileMind-monitor", daemon=True
                )
                self.worker.start()
        with self.repository.database.transaction(True) as conn:
            conn.execute("UPDATE local_monitor_rules SET enabled=1 WHERE id=?", (rule_id,))
        self.event(
            rule_id, rule["source"], "RUNNING", "仅在客户端运行期间监控；新变化将先等待稳定。"
        )

    def enqueue(self, rule, path):
        file = Path(path).absolute()
        destination = Path(rule["destination"])
        if destination == file or destination in file.parents or file.name.startswith(".filemind-"):
            return
        with self.lock:
            key = (rule["id"], str(file))
            if len(self.pending) < 10000 or key in self.pending:
                self.pending[key] = (rule, file, time.monotonic(), None)

    def pause(self, rule_id, persist=True):
        with self.lock:
            observer = self.observers.pop(rule_id, None)
            self.active_rules.pop(rule_id, None)
            self.pending = {k: value for k, value in self.pending.items() if k[0] != rule_id}
        if observer:
            observer.stop()
            observer.join(timeout=3)
        if persist:
            with self.repository.database.transaction(True) as conn:
                conn.execute("UPDATE local_monitor_rules SET enabled=0 WHERE id=?", (rule_id,))

    def remove(self, rule_id):
        self.pause(rule_id)
        with self.repository.database.transaction(True) as conn:
            conn.execute("DELETE FROM local_monitor_rules WHERE id=?", (rule_id,))

    def run(self):
        while not self.stop_event.wait(0.2):
            with self.lock:
                pending = list(self.pending.items())
                active = list(self.active_rules.values())
            for rule in active:
                if not Path(rule["source"]).is_dir():
                    self.event(rule["id"], rule["source"], "PAUSED", "源目录已删除，已暂停。")
                    self.pause(rule["id"])
            for key, (rule, path, since, snapshot) in pending:
                if self.stop_event.is_set():
                    return
                if not Path(rule["source"]).is_dir():
                    self.event(rule["id"], path, "PAUSED", "源目录被删除，已暂停，不会无限重试。")
                    self.pause(rule["id"])
                    continue
                try:
                    current = path.stat()
                    fingerprint = (current.st_size, current.st_mtime_ns)
                    if linked(path) or not path.is_file():
                        raise FileNotFoundError
                    if snapshot != fingerprint:
                        with self.lock:
                            if self.pending.get(key) == (rule, path, since, snapshot):
                                self.pending[key] = (rule, path, time.monotonic(), fingerprint)
                        continue
                    if time.monotonic() - since < self.stable_seconds:
                        continue
                    with self.lock:
                        if self.pending.get(key) != (rule, path, since, snapshot):
                            continue
                        self.pending.pop(key, None)
                    file = FileAnalyzer().analyze(path, self.stop_event)
                    if self.seen.get(str(path)) == file.sha256 + str(file.modified_ns):
                        continue
                    self.seen[str(path)] = file.sha256 + str(file.modified_ns)
                    if len(self.seen) > 10000:
                        self.seen.pop(next(iter(self.seen)))
                    if rule["auto_execute"]:
                        plan = ClassificationService().plan(
                            [file], Path(rule["destination"]), rules=rule["rules"]
                        )
                        report = self.organizer.execute(
                            plan, confirmed=True, cancel=self.stop_event
                        )
                        self.event(
                            rule["id"],
                            path,
                            report["status"],
                            f"明确启用的自动策略；批次 {plan.id}",
                        )
                    else:
                        self.event(
                            rule["id"],
                            path,
                            "WAITING_CONFIRMATION",
                            "文件稳定；请载入智能整理并确认计划。",
                        )
                except FileNotFoundError:
                    with self.lock:
                        self.pending.pop(key, None)
                except Exception as exc:
                    with self.lock:
                        self.pending.pop(key, None)
                    self.event(rule["id"], path, "FAILED", str(exc))

    def start_enabled(self):
        for rule in self.rules():
            if rule["enabled"]:
                try:
                    self.start(rule["id"])
                except Exception as exc:
                    self.event(rule["id"], rule["source"], "FAILED", str(exc))

    def stop(self):
        self.stop_event.set()
        for rule_id in list(self.observers):
            self.pause(rule_id, persist=False)
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=5)
