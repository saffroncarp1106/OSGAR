# Recording data model and points
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RecordedPoint:
    x_mm: float
    y_mm: float
    t_s: float
    velocity_mm_s: float = 0.0
    key: int = 0
    is_click_spike: bool = False
    is_synthetic: bool = False
    is_initial_outlier: bool = False

    def to_dict(self) -> dict:
        return {"x_mm": self.x_mm, "y_mm": self.y_mm, "t_s": self.t_s,
                "velocity_mm_s": self.velocity_mm_s, "key": self.key,
                "is_click_spike": self.is_click_spike,
                "is_synthetic": self.is_synthetic,
                "is_initial_outlier": self.is_initial_outlier}

    @classmethod
    def from_dict(cls, data: dict) -> "RecordedPoint":
        return cls(
            x_mm=data["x_mm"],
            y_mm=data["y_mm"],
            t_s=data["t_s"],
            velocity_mm_s=data.get("velocity_mm_s", 0.0),
            key=data.get("key", 0),
            is_click_spike=data.get("is_click_spike", False),
            is_synthetic=data.get("is_synthetic", False),
            is_initial_outlier=data.get("is_initial_outlier", False),
        )


@dataclass
class Recording:
    points: list[RecordedPoint] = field(default_factory=list)
    duration_s: float = 0.0
    name: str = "Recording"
    peak_velocity_mm_s: float = 0.0
    click_points: list[RecordedPoint] = field(default_factory=list)
    key_release_points: list[RecordedPoint] = field(default_factory=list)

    def append(self, x_mm: float, y_mm: float, t_s: float, velocity_mm_s: float = 0.0) -> None:
        self.points.append(RecordedPoint(x_mm, y_mm, t_s, velocity_mm_s))
        self.duration_s = max(self.duration_s, t_s)
        self.peak_velocity_mm_s = max(self.peak_velocity_mm_s, velocity_mm_s)

    def is_empty(self) -> bool:
        return len(self.points) == 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "duration_s": self.duration_s,
            "peak_velocity_mm_s": self.peak_velocity_mm_s,
            "points": [p.to_dict() for p in self.points],
            "click_points": [p.to_dict() for p in self.click_points],
            "key_release_points": [p.to_dict() for p in self.key_release_points],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Recording":
        return cls(
            name=data.get("name", "Recording"),
            duration_s=data.get("duration_s", 0.0),
            peak_velocity_mm_s=data.get("peak_velocity_mm_s", 0.0),
            points=[RecordedPoint.from_dict(p) for p in data.get("points", [])],
            click_points=[RecordedPoint.from_dict(p) for p in data.get("click_points", [])],
            key_release_points=[RecordedPoint.from_dict(p) for p in data.get("key_release_points", [])],
        )
