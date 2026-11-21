import json
import uuid
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QProgressBar,
    QScrollArea,
    QStyle,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from filemind.services.library import LibraryClient
from filemind.ui.tasks import TaskController
from filemind.ui.widgets import DataTable, button, label


class LibraryDialog(QDialog):
    def __init__(self, server, token, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FileMind · Global Library 全局文件库")
        self.resize(1160, 880)
        self.client = LibraryClient(server, token)
        self.rows, self.selected, self.conflicts = [], None, []
        self.offset, self.total, self.closing = 0, 0, False
        self.worker = TaskController(self)
        self.worker.result.connect(self.completed)
        self.worker.error.connect(self.failed)
        self.worker.progress.connect(
            lambda n, total: self.progress.setValue(int(100 * n / max(1, total)))
        )
        self.worker.finished.connect(self.controls)
        outer = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        body.addWidget(label("全局文件库", "PageTitle"))
        body.addWidget(label("同步目录，按需下载。逻辑移动不改变实际存储位置。", "Muted"))
        self.status = label("正在同步…", "Muted")
        body.addWidget(self.status)
        filters = QGridLayout()
        self.search, self.category, self.extension = QLineEdit(), QLineEdit(), QLineEdit()
        self.search.setPlaceholderText("搜索文件名")
        self.category.setPlaceholderText("分类包含")
        self.extension.setPlaceholderText("类型，如 pdf")
        self.folder, self.sort, self.view = QComboBox(), QComboBox(), QComboBox()
        self.folder.addItem("全部目录", None)
        for title, value in [
            ("最近修改", "recent"),
            ("文件名称", "name"),
            ("文件大小", "size"),
            ("归档时间", "archived"),
        ]:
            self.sort.addItem(title, value)
        for title, value in [("全部文件", "all"), ("收藏", "favorite"), ("回收站", "trash")]:
            self.view.addItem(title, value)
        for i, (name, widget) in enumerate(
            [
                ("文件名", self.search),
                ("分类", self.category),
                ("文件类型", self.extension),
                ("目录", self.folder),
                ("排序", self.sort),
                ("视图", self.view),
            ]
        ):
            widget.setAccessibleName(name)
            filters.addWidget(widget, i // 3, i % 3)
        body.addLayout(filters)
        nav = QHBoxLayout()
        self.refresh_button = button("搜索 / 同步")
        self.previous, self.next = button("上一页"), button("下一页")
        self.new_folder = button("新建逻辑目录")
        self.refresh_button.clicked.connect(self.reset)
        self.search.returnPressed.connect(self.reset)
        self.previous.clicked.connect(lambda: self.page(-50))
        self.next.clicked.connect(lambda: self.page(50))
        self.new_folder.clicked.connect(self.create_folder)
        for w in [self.refresh_button, self.previous, self.next, self.new_folder]:
            nav.addWidget(w)
        body.addLayout(nav)
        self.tabs = QTabWidget()
        self.table = DataTable(("文件名", "分类 / 目录", "大小", "版本", "状态", "修改时间"))
        self.table.itemSelectionChanged.connect(self.choose)
        self.conflict_table = DataTable(
            ("文件", "基础版本", "当前版本", "候选版本", "来源设备", "原因 / 状态")
        )
        self.tabs.addTab(self.table, "文件目录 / 回收站")
        self.tabs.addTab(self.conflict_table, "冲突中心")
        body.addWidget(self.tabs)
        conflict_actions = QHBoxLayout()
        for title, action in [
            ("冲突：保留当前", "current"),
            ("冲突：选择候选", "candidate"),
            ("冲突：候选另存", "save_as"),
        ]:
            w = button(title)
            w.clicked.connect(lambda checked=False, a=action: self.resolve(a))
            conflict_actions.addWidget(w)
        body.addLayout(conflict_actions)
        self.info = label("选择文件查看详情和版本。", "Muted")
        self.info.setWordWrap(True)
        body.addWidget(self.info)
        form = QFormLayout()
        self.name_edit, self.category_edit, self.directory_edit = (
            QLineEdit(),
            QLineEdit(),
            QLineEdit(),
        )
        self.favorite = QCheckBox("收藏")
        for name, widget in [
            ("文件名称", self.name_edit),
            ("逻辑分类", self.category_edit),
            ("逻辑目录", self.directory_edit),
            ("收藏", self.favorite),
        ]:
            widget.setAccessibleName(name)
            form.addRow(name, widget)
        self.versions = QComboBox()
        self.versions.setAccessibleName("文件版本")
        self.versions.currentIndexChanged.connect(self.version_info)
        form.addRow("版本（历史不可变）", self.versions)
        self.version_label = label("", "Muted")
        self.version_label.setWordWrap(True)
        form.addRow(self.version_label)
        body.addLayout(form)
        actions = QGridLayout()
        for i, (title, callback) in enumerate(
            [
                ("保存名称 / 分类 / 逻辑目录", self.save),
                ("软删除", self.delete),
                ("从回收站恢复", self.restore),
                ("下载所选版本", self.download),
                ("分享此版本", self.share_version),
                ("上传新版本", self.upload),
                ("设所选版本为当前", self.restore_version),
                ("撤销最近名称 / 目录编辑", self.undo),
                ("加载更多历史版本", self.more_versions),
                ("取消当前传输", self.worker.cancel),
            ]
        ):
            w = button(title)
            w.clicked.connect(callback)
            actions.addWidget(w, i // 3, i % 3)
        body.addLayout(actions)
        self.progress = QProgressBar()
        body.addWidget(self.progress)
        body.addWidget(
            label(
                "会话目录缓存：退出/关闭清空；离线仅当前会话私人元数据，无法实时确认撤权。", "Muted"
            )
        )
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.load)
        self.timer.start()
        self.load()

    def submit(self, action):
        if self.closing or self.worker.busy:
            return
        self.worker.submit(action)
        self.controls()

    def controls(self):
        self.refresh_button.setEnabled(not self.worker.busy)
        self.previous.setEnabled(not self.worker.busy and self.offset > 0)
        self.next.setEnabled(not self.worker.busy and self.offset + 50 < self.total)
        if self.closing and not self.worker.busy:
            self.client.close()

    def reset(self):
        self.offset = 0
        self.load()

    def page(self, delta):
        self.offset = max(0, self.offset + delta)
        self.load()

    def load(self):
        if self.worker.busy or self.closing:
            return
        query = {
            "q": self.search.text(),
            "category": self.category.text(),
            "extension": self.extension.text(),
            "directory": self.folder.currentData(),
            "sort": self.sort.currentData(),
            "offset": self.offset,
            "trash": "true" if self.view.currentData() == "trash" else "false",
            "favorite": "true" if self.view.currentData() == "favorite" else "false",
        }

        def work(cancel, progress):
            synced = self.client.sync(cancel)
            if synced["state"] != "SYNCED":
                rows = [
                    r
                    for r in synced["items"]
                    if not r.get("deleted_at") and query["q"].lower() in r["filename"].lower()
                ]
                return {"kind": "load", "sync": synced, "items": rows, "total": len(rows)}
            result = self.client.search(**query)
            return {
                "kind": "load",
                "sync": synced,
                **result,
                "folders": self.client.request("GET", "/library/folders"),
                "conflicts": self.client.request("GET", "/library/conflicts"),
            }

        self.submit(work)

    def completed(self, result):
        if self.closing:
            return
        kind = result.get("kind")
        if kind == "load":
            self.rows, self.total = result["items"], result["total"]
            self.table.blockSignals(True)
            from datetime import datetime

            self.table.set_rows(
                [
                    (
                        r["filename"],
                        r["category"] + " / " + r["directory"],
                        str(r["size_bytes"]),
                        str(r["version"]),
                        "已删除" if r["deleted_at"] else "正常",
                        datetime.fromtimestamp(r["updated_at"]).strftime("%Y-%m-%d %H:%M"),
                    )
                    for r in self.rows
                ]
            )
            for i in range(len(self.rows)):
                self.table.item(i, 0).setIcon(
                    self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
                )
            self.table.blockSignals(False)
            state = result["sync"]["state"]
            self.status.setText(
                ("已同步" if state == "SYNCED" else "离线：仅会话私人缓存，筛选仅文件名，未同步")
                + f" · 修订 {result['sync']['revision']}"
                + f" · {self.offset + 1 if self.total else 0}–"
                + f"{min(self.offset + 50, self.total)} / {self.total}"
            )
            if "folders" in result:
                selected = self.folder.currentData()
                self.folder.clear()
                self.folder.addItem("全部目录", None)
                for folder in result["folders"]["items"]:
                    self.folder.addItem(folder["path"] or "根目录", folder["path"])
                index = self.folder.findData(selected)
                self.folder.setCurrentIndex(max(0, index))
                self.conflicts = result["conflicts"]
                self.conflict_table.set_rows(
                    [
                        (
                            r["filename"],
                            r["base_version_id"],
                            r["current_version_id"],
                            r["candidate_version_id"],
                            r["device_id"] or r["actor_id"],
                            r["reason"] + " / " + r["state"],
                        )
                        for r in self.conflicts
                    ]
                )
        elif kind == "detail":
            self.selected = result["file"]
            row = self.selected
            self.name_edit.setText(row["filename"])
            self.category_edit.setText(row["category"])
            self.directory_edit.setText(row["directory"])
            self.favorite.setChecked(bool(row["favorite"]))
            self.info.setText(
                f"文件 ID {row['file_id']} · {row['summary']}"
                f" · 实际存储目录 {row['storage_directory']}"
                f" · 来源 {json.dumps(row['sources'], ensure_ascii=False)}"
            )
            self.versions.clear()
            for v in row["versions"]:
                self.versions.addItem(
                    f"Version {v['version']} · {v['filename']} · {v['size_bytes']} B", v
                )
        elif kind == "versions":
            for v in result["items"]:
                self.versions.addItem(
                    f"Version {v['version']} · {v['filename']} · {v['size_bytes']} B", v
                )
            self.status.setText("已加载历史版本" if result["items"] else "已经显示全部历史版本")
        else:
            self.status.setText(
                result.get("state", "操作已提交，下次同步刷新；新版本归档状态请查看归档任务。")
            )
            self.selected = None
            self.versions.clear()
            QTimer.singleShot(50, self.load)

    def failed(self, message):
        if not self.closing:
            self.status.setText("操作失败：" + message)
            self.rows, self.selected = [], None
            self.table.set_rows([])
            self.versions.clear()
            self.name_edit.clear()
            self.category_edit.clear()
            self.directory_edit.clear()
            self.conflict_table.set_rows([])
            self.info.setText("请重新同步并选择文件。")

    def choose(self):
        index = self.table.currentRow()
        if 0 <= index < len(self.rows):
            fid = self.rows[index]["file_id"]
            self.submit(
                lambda cancel, progress: {"kind": "detail", "file": self.client.detail(fid)}
            )

    def version_info(self):
        v = self.versions.currentData()
        self.version_label.setText(
            (
                f"SHA-256 {v['sha256']} · 来源设备 {v.get('device_id') or '未记录'}"
                f" · 时间 {v['created_at']}"
            )
            if v
            else ""
        )

    def more_versions(self):
        if self.selected:
            fid, offset = self.selected["file_id"], self.versions.count()
            self.submit(
                lambda c, p: {
                    "kind": "versions",
                    "items": self.client.request(
                        "GET", f"/library/{fid}/versions?offset={offset}&limit=200"
                    ),
                }
            )

    def save(self):
        if self.selected:
            file = self.selected
            changes = {
                "filename": self.name_edit.text(),
                "category": self.category_edit.text(),
                "directory": self.directory_edit.text(),
                "favorite": self.favorite.isChecked(),
            }
            self.submit(lambda c, p: self.client.operate(file, changes))

    def delete(self):
        if self.selected:
            file = self.selected
            self.submit(lambda c, p: self.client.operate(file, kind="DELETE"))

    def restore(self):
        if self.selected:
            file = self.selected
            self.submit(lambda c, p: self.client.operate(file, kind="RESTORE"))

    def restore_version(self):
        if self.selected and self.versions.currentData():
            file, version = self.selected, self.versions.currentData()
            self.submit(
                lambda c, p: self.client.request(
                    "POST",
                    f"/library/{file['file_id']}/current-version",
                    {
                        "version_id": version["id"],
                        "expected_revision": file["revision"],
                        "idempotency_key": str(uuid.uuid4()),
                    },
                )
            )

    def undo(self):
        if self.selected:
            last = next((r for r in self.selected["history"] if r["kind"] == "PATCH"), None)
            if last:
                before, file = json.loads(last["before_json"]), self.selected
                changes = {k: before[k] for k in ["filename", "category", "directory", "favorite"]}
                self.submit(lambda c, p: self.client.operate(file, changes))

    def download(self):
        version = self.versions.currentData()
        if version and not self.worker.busy:
            name, _ = QFileDialog.getSaveFileName(
                self, "下载到新文件（不覆盖）", version["filename"]
            )
            if name:
                self.submit(
                    lambda cancel, progress: self.client.download(
                        version, Path(name), cancel, progress
                    )
                )

    def share_version(self):
        version = self.versions.currentData()
        if version and not self.worker.busy:
            from filemind.ui.share_dialog import ShareDialog

            ShareDialog(self.client.control.server, self.client.control.token, self, version).exec()

    def upload(self):
        if self.selected and not self.worker.busy:
            file = self.selected
            name, _ = QFileDialog.getOpenFileName(self, "上传新版本")
            if name:
                self.submit(
                    lambda cancel, progress: self.client.upload_version(
                        file, Path(name), cancel, progress
                    )
                )

    def create_folder(self):
        path, ok = QInputDialog.getText(self, "新建逻辑目录", "路径（用 / 分层）")
        if ok and path:
            self.submit(
                lambda c, p: self.client.request("POST", "/library/folders", {"path": path})
            )

    def resolve(self, action):
        index = self.conflict_table.currentRow()
        if 0 <= index < len(self.conflicts):
            conflict = self.conflicts[index]

            def work(c, p):
                file = self.client.detail(conflict["file_id"])
                return self.client.request(
                    "POST",
                    f"/library/conflicts/{conflict['id']}/resolve",
                    {
                        "action": action,
                        "expected_revision": file["revision"],
                        "idempotency_key": str(uuid.uuid4()),
                    },
                )

            self.submit(work)

    def done(self, result):
        self.closing = True
        self.timer.stop()
        self.worker.cancel()
        if not self.worker.busy:
            self.client.close()
        super().done(result)
