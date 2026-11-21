from dataclasses import dataclass

from filemind.core.config import AppConfig, ConfigStore
from filemind.core.paths import WorkspacePaths
from filemind.domain.models import Theme
from filemind.infrastructure.database import Database
from filemind.infrastructure.file_analysis import LocalScanner
from filemind.infrastructure.local_repository import LocalRepository
from filemind.services.classification import AIProvider, ClassificationService
from filemind.services.demo import DemoService
from filemind.services.local_organizer import LocalOrganizer
from filemind.services.monitoring import MonitorManager


@dataclass
class ApplicationService:
    paths: WorkspacePaths
    config_store: ConfigStore
    config: AppConfig
    database: Database
    demo: DemoService
    scanner: LocalScanner
    ai: AIProvider | None
    watcher: MonitorManager
    executor: LocalOrganizer
    local: LocalOrganizer
    classification: ClassificationService
    monitor: MonitorManager

    @classmethod
    def create(cls, paths: WorkspacePaths) -> "ApplicationService":
        paths.ensure()
        store = ConfigStore(paths.config_file)
        config = store.load()
        database = Database(paths.database_file)
        database.initialize()
        local = LocalOrganizer(LocalRepository(database))
        monitor = MonitorManager(local.repository, local)
        return cls(
            paths,
            store,
            config,
            database,
            DemoService(),
            LocalScanner(),
            None,
            monitor,
            local,
            local,
            ClassificationService(),
            monitor,
        )

    def save_preferences(self, theme: Theme, width: int, height: int, maximized: bool) -> None:
        config = AppConfig(
            theme=theme, window_width=width, window_height=height, maximized=maximized
        )
        self.config_store.save(config)
        self.config = config

    def history_count(self) -> int:
        return self.database.history_count()
