import pytest

from filemind.core.paths import WorkspacePaths
from filemind.services.application import ApplicationService


@pytest.fixture
def service(tmp_path):
    return ApplicationService.create(WorkspacePaths(tmp_path))
