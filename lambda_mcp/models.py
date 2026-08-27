"""Request models shared by the tools.

These exist so the model gets a real schema (and real validation errors it can
recover from) for the API's nested request objects, rather than an opaque dict.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

NetworkProtocol = Literal["tcp", "udp", "icmp", "all"]


class FirewallRule(BaseModel):
    """One inbound firewall rule."""

    protocol: NetworkProtocol = Field(description="The protocol the rule applies to.")
    source_network: str = Field(
        description=(
            "Source IPv4 addresses in CIDR notation, for example '1.2.3.4/32'. "
            "Use '0.0.0.0/0' to allow any address. A bare address implies /32."
        )
    )
    description: str = Field(
        max_length=128, description="A human-readable description of the rule."
    )
    port_range: list[int] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description=(
            "Inclusive port range as [min, max]. For a single port list it "
            "twice, e.g. [22, 22]. Required for tcp/udp/all, forbidden for icmp."
        ),
    )

    @model_validator(mode="after")
    def _check_port_range(self) -> "FirewallRule":
        if self.protocol == "icmp":
            if self.port_range is not None:
                raise ValueError("port_range must be omitted when protocol is 'icmp'")
            return self
        if self.port_range is None:
            raise ValueError(
                "port_range is required for the 'tcp', 'udp' and 'all' protocols"
            )
        low, high = self.port_range
        if not (1 <= low <= 65535 and 1 <= high <= 65535):
            raise ValueError("ports must be between 1 and 65535")
        if low > high:
            raise ValueError("port_range must be ordered as [min, max]")
        return self


class TagEntry(BaseModel):
    """A key/value tag."""

    key: str = Field(
        max_length=55,
        pattern=r"^[a-z][a-z0-9-:]+$",
        description="Tag key. Keys starting with 'lambda-ai-' are reserved.",
    )
    value: str = Field(max_length=128, description="Tag value.")


class FilesystemMount(BaseModel):
    """A filesystem mounted at an explicit path."""

    file_system_id: str = Field(description="The ID of the filesystem to mount.")
    mount_point: str = Field(
        max_length=256,
        description=(
            "Absolute mount path on the instance. Must start with /home, "
            "/lambda/nfs or /data."
        ),
    )


class ImageSpec(BaseModel):
    """The machine image to boot, identified by ID or by family."""

    id: str | None = Field(default=None, description="The image's unique identifier.")
    family: str | None = Field(default=None, description="The image family name.")

    @model_validator(mode="after")
    def _exactly_one(self) -> "ImageSpec":
        if bool(self.id) == bool(self.family):
            raise ValueError("specify exactly one of 'id' or 'family'")
        return self
