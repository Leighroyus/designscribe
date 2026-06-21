"""Configuration management."""
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict

CONFIG_FILE = "designscribe.json"

DEFAULT_CONFIG = {
    "watch": {
        "paths": ["src/"],
        "exclude": ["*.test.*", "*.spec.*", "node_modules/", "__pycache__/"],
        "debounce_ms": 2000,
    },
    "llm": {
        "provider": "openrouter",
        "model": "xiaomi/mimo-v2.5-pro",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "diagrams": {
        "format": "png",
        "output": "diagrams/",
    },
    "output": {
        "file": "living-arch.md",
        "max_entries": 100,
    },
    "graph": {
        "engine": "depwire",
        "incremental": True,
    },
}


@dataclass
class Config:
    watch_paths: list[str] = field(default_factory=lambda: ["src/"])
    watch_exclude: list[str] = field(default_factory=lambda: ["*.test.*", "*.spec.*", "node_modules/"])
    debounce_ms: int = 2000
    llm_provider: str = "openrouter"
    llm_model: str = "openai/gpt-4o-mini"
    api_key_env: str = "OPENROUTER_API_KEY"
    diagram_format: str = "png"
    diagram_output: str = "diagrams/"
    output_file: str = "living-arch.md"
    max_entries: int = 100
    graph_engine: str = "depwire"
    graph_incremental: bool = True

    @classmethod
    def load(cls, path: str = CONFIG_FILE) -> "Config":
        """Load config from file, falling back to defaults."""
        p = Path(path)
        if p.exists():
            with open(p) as f:
                data = json.load(f)
            return cls(
                watch_paths=data.get("watch", {}).get("paths", cls.watch_paths),
                watch_exclude=data.get("watch", {}).get("exclude", cls.watch_exclude),
                debounce_ms=data.get("watch", {}).get("debounce_ms", cls.debounce_ms),
                llm_provider=data.get("llm", {}).get("provider", cls.llm_provider),
                llm_model=data.get("llm", {}).get("model", cls.llm_model),
                api_key_env=data.get("llm", {}).get("api_key_env", cls.api_key_env),
                diagram_format=data.get("diagrams", {}).get("format", cls.diagram_format),
                diagram_output=data.get("diagrams", {}).get("output", cls.diagram_output),
                output_file=data.get("output", {}).get("file", cls.output_file),
                max_entries=data.get("output", {}).get("max_entries", cls.max_entries),
                graph_engine=data.get("graph", {}).get("engine", cls.graph_engine),
                graph_incremental=data.get("graph", {}).get("incremental", cls.graph_incremental),
            )
        return cls()

    def save(self, path: str = CONFIG_FILE):
        """Persist config to file."""
        data = {
            "watch": {"paths": self.watch_paths, "exclude": self.watch_exclude, "debounce_ms": self.debounce_ms},
            "llm": {"provider": self.llm_provider, "model": self.llm_model, "api_key_env": self.api_key_env},
            "diagrams": {"format": self.diagram_format, "output": self.diagram_output},
            "output": {"file": self.output_file, "max_entries": self.max_entries},
            "graph": {"engine": self.graph_engine, "incremental": self.graph_incremental},
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
