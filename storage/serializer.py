# JSON project serialization and loading
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from storage.project import Project

PROJECT_FILE_EXTENSION = ".osgar"
LEGACY_PROJECT_EXTENSION = ".nmproj"


class ProjectSerializer(ABC):
    @abstractmethod
    def save(self, project: Project, path: Path) -> None:
        ...

    @abstractmethod
    def load(self, path: Path) -> Project:
        ...


class JsonProjectSerializer(ProjectSerializer):
    def save(self, project: Project, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(project.to_dict(), indent=2), encoding="utf-8")

    def load(self, path: Path) -> Project:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Project.from_dict(data)
