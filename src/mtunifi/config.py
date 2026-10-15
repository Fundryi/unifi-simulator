import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inform_url: str = "http://127.0.0.1:8080/inform"
    state_dir: Path = Path(".state")
    device_ip: str = "192.0.2.10"
    hostname: str = Field(default="mtunifi-lab", pattern=r"^[A-Za-z0-9-]{1,63}$")
    profile: str = "us8-experimental"
    packet_version: int = Field(default=1, ge=0, le=1)
    encryption: str = "cbc"
    interval: float = Field(default=10, ge=1, le=300)
    timeout: float = Field(default=10, ge=1, le=120)
    trace: bool = False
    synthetic_file: Path | None = None

    @field_validator("synthetic_file", mode="before")
    @classmethod
    def optional_scenario(cls, value):
        return None if value == "" else value

    @field_validator("inform_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/inform"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use an HTTP(S) /inform URL without credentials, query or fragment")
        _ = parsed.port
        return value

    @field_validator("device_ip")
    @classmethod
    def valid_ip(cls, value: str) -> str:
        return str(ipaddress.IPv4Address(value))

    @field_validator("encryption")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        if value not in ("cbc", "gcm"):
            raise ValueError("Expected cbc or gcm")
        return value

    @field_validator("profile")
    @classmethod
    def valid_profile(cls, value: str) -> str:
        if value != "us8-experimental":
            raise ValueError(
                "Only us8-experimental is implemented; model substitution is unsupported"
            )
        return value

    @classmethod
    def from_env(cls) -> "Settings":
        return cls.model_validate(
            {
                name: os.environ[f"MTUNIFI_{name.upper()}"]
                for name in cls.model_fields
                if f"MTUNIFI_{name.upper()}" in os.environ
            }
        )
