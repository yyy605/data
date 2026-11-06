"""Configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import os

import yaml

from .errors import ConfigurationError


@dataclass(frozen=True)
class AppConfig:
    data: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.data)


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path).expanduser()
    if not config_path.is_file():
        raise ConfigurationError(f"Configuration file not found: {config_path}")

    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            raw = yaml.safe_load(config_file)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise ConfigurationError("Configuration root must be a mapping")

    config = {key: value for key, value in raw.items()}
    _validate_required(config)
    return AppConfig(data=config)


def resolve_env(config: Mapping[str, Any], key: str, required: bool = True) -> str | None:
    reference = config.get(key)
    if not isinstance(reference, str) or not reference:
        if required:
            raise ConfigurationError(f"Missing environment variable reference: {key}")
        return None

    value = os.getenv(reference)
    if required and not value:
        raise ConfigurationError(f"Environment variable {reference} is not set")
    return value


def _validate_required(config: Mapping[str, Any]) -> None:
    required_sections = ("team", "members", "github", "lark", "smtp", "storage")
    missing_sections = [section for section in required_sections if section not in config]
    if missing_sections:
        raise ConfigurationError(
            "Missing required configuration section(s): " + ", ".join(missing_sections)
        )

    team = config.get("team")
    if not isinstance(team, Mapping) or not team.get("name"):
        raise ConfigurationError("team.name is required")

    members = config.get("members")
    if not isinstance(members, list) or not members:
        raise ConfigurationError("members must be a non-empty list")

    required_member_fields = ("github_login", "lark_user_id", "name", "email")
    for index, member in enumerate(members):
        if not isinstance(member, Mapping):
            raise ConfigurationError(f"members[{index}] must be a mapping")
        missing_member_fields = [
            field for field in required_member_fields if not member.get(field)
        ]
        if missing_member_fields:
            raise ConfigurationError(
                f"members[{index}] missing required field(s): "
                + ", ".join(missing_member_fields)
            )

    github = config.get("github")
    if not isinstance(github, Mapping) or not github.get("repositories"):
        raise ConfigurationError("github.repositories is required")

    storage = config.get("storage")
    if not isinstance(storage, Mapping) or not storage.get("sqlite_path"):
        raise ConfigurationError("storage.sqlite_path is required")
