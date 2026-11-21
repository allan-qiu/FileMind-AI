from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class TaskSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()
    progress = Signal(object, object)


class Worker(QRunnable):
    def __init__(self, operation, cancel):
        super().__init__()
        self.operation, self.cancel = operation, cancel
        self.signals = TaskSignals()

    @Slot()
    def run(self):
        try:
            self.signals.result.emit(self.operation(self.cancel, self.signals.progress.emit))
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


class TaskController(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()
    progress = Signal(object, object)

    def __init__(self, parent):
        super().__init__(parent)
        self.busy = False
        self.cancel_event = Event()
        self.worker = None

    def submit(self, operation):
        if self.busy:
            return False
        self.busy = True
        self.cancel_event = Event()
        self.worker = Worker(operation, self.cancel_event)
        self.worker.signals.result.connect(self.result)
        self.worker.signals.error.connect(self.error)
        self.worker.signals.progress.connect(self.progress)
        self.worker.signals.finished.connect(self.complete)
        QThreadPool.globalInstance().start(self.worker)
        return True

    @Slot()
    def complete(self):
        self.busy = False
        self.worker = None
        self.finished.emit()

    def cancel(self):
        self.cancel_event.set()
