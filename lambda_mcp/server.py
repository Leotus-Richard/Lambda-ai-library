"""MCP tool surface for the Lambda Cloud API.

Twenty-eight tools cover all thirty-four API operations. The mapping is one
tool per operation except where operations share both an intent and a schema (a
firewall ruleset versus the global ruleset) or belong to a low-traffic beta
sub-resource (ticket attachments).

``tests/test_coverage.py`` asserts that mapping against the vendored spec, so
an endpoint cannot be dropped silently.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from lambda_mcp import __version__
from lambda_mcp.client import LambdaClient
from lambda_mcp.models import FilesystemMount, FirewallRule, ImageSpec, TagEntry

#: Tool name -> the OpenAPI operationIds it covers. Populated when the server is
#: built, and asserted against the vendored spec by ``tests/test_coverage.py``.
TOOL_COVERAGE: dict[str, tuple[str, ...]] = {}

#: Tools that mutate state. Withheld entirely unless writes are enabled.
WRITE_TOOLS: set[str] = set()

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False,
    open_world_hint=False,
)
IDEMPOTENT = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True,
    open_world_hint=False,
)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False,
    open_world_hint=False,
)

GLOBAL = "global"

TicketSeverity = Literal["sev_1", "sev_2", "sev_3"]
TicketPriority = Literal["low", "normal", "high", "urgent"]
TicketStatus = Literal["open", "on_hold", "pending", "solved", "confirm_solved"]
TicketType = Literal["service_request", "incident", "automated_event"]


def _rules(rules: list[FirewallRule]) -> list[dict[str, Any]]:
    return [rule.model_dump(exclude_none=True) for rule in rules]


def _entries(items: list[BaseModel] | None) -> list[dict[str, Any]] | None:
    if items is None:
        return None
    return [item.model_dump(exclude_none=True) for item in items]


def _obj(payload: Any) -> dict[str, Any]:
    """Normalise an empty-bodied success response to an empty object."""
    return payload if isinstance(payload, dict) else {}


def _ruleset_path(ruleset_id: str) -> str:
    if ruleset_id == GLOBAL:
        return "/api/v1/firewall-rulesets/global"
    return f"/api/v1/firewall-rulesets/{ruleset_id}"


def build_server(client: LambdaClient, *, allow_write: bool = False) -> MCPServer:
    """Construct the MCP server.

    When ``allow_write`` is false, mutating tools are not registered at all, so
    they never reach the model's tool list and cannot be called by mistake.
    """
    mcp = MCPServer(
        "lambda-cloud",
        version=__version__,
        instructions=(
            "Tools for Lambda Cloud GPU infrastructure. Launching or running "
            "instances costs money by the hour, and terminating an instance or "
            "deleting a filesystem destroys data permanently — confirm those "
            "with the user first. The API allows roughly one request per "
            "second, so prefer list tools over polling in a loop."
        ),
    )

    def tool(*operation_ids: str, write: bool = False, **kwargs: Any):
        def decorate(fn):
            TOOL_COVERAGE[fn.__name__] = operation_ids
            if write:
                WRITE_TOOLS.add(fn.__name__)
                if not allow_write:
                    return fn
            mcp.tool(**kwargs)(fn)
            return fn

        return decorate

    # ---------------------------------------------------------------- instances

    @tool("listInstances", title="List instances", annotations=READ)
    async def list_instances(
        cluster_id: Annotated[
            str | None,
            Field(description="Only return instances in this cluster."),
        ] = None,
    ) -> list[dict[str, Any]]:
        """List your running GPU instances, with their status, IP and region."""
        return await client.request(
            "GET", "/api/v1/instances", params={"cluster_id": cluster_id}
        )

    @tool("getInstance", title="Get instance", annotations=READ)
    async def get_instance(
        instance_id: Annotated[str, Field(description="The instance's unique ID.")],
    ) -> dict[str, Any]:
        """Retrieve one instance in detail, including its SSH and Jupyter access."""
        return await client.request("GET", f"/api/v1/instances/{instance_id}")

    @tool(
        "launchInstance",
        write=True,
        title="Launch instance",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False,
            idempotent_hint=False, open_world_hint=True,
        ),
    )
    async def launch_instance(
        region_name: Annotated[str, Field(description="Region to launch into.")],
        instance_type_name: Annotated[
            str,
            Field(description="Instance type, e.g. 'gpu_8x_a100'. See list_catalog."),
        ],
        ssh_key_names: Annotated[
            list[str],
            Field(description="SSH key names granting access. Exactly one is required."),
        ],
        name: Annotated[
            str | None, Field(max_length=64, description="A name for the instance.")
        ] = None,
        hostname: Annotated[
            str | None,
            Field(
                max_length=63,
                pattern=r"^[a-z0-9][0-9a-z-]{0,62}$",
                description="Hostname to drive into /etc/hostname.",
            ),
        ] = None,
        file_system_names: Annotated[
            list[str] | None,
            Field(description="Filesystems to mount at their default paths."),
        ] = None,
        file_system_mounts: Annotated[
            list[FilesystemMount] | None,
            Field(description="Filesystems to mount at explicit paths. Wins over file_system_names."),
        ] = None,
        image: Annotated[
            ImageSpec | None,
            Field(description="Image to boot. Defaults to the latest Lambda Stack image."),
        ] = None,
        user_data: Annotated[
            str | None,
            Field(description="cloud-init user-data, plain text, max 1MB."),
        ] = None,
        tags: Annotated[list[TagEntry] | None, Field(description="Tags to apply.")] = None,
        firewall_rulesets: Annotated[
            list[str] | None,
            Field(description="IDs of firewall rulesets, which must be in the same region."),
        ] = None,
    ) -> dict[str, Any]:
        """Launch a new GPU instance. This starts billing immediately.

        Rate-limited by the API to one launch every 12 seconds.
        """
        body: dict[str, Any] = {
            "region_name": region_name,
            "instance_type_name": instance_type_name,
            "ssh_key_names": ssh_key_names,
        }
        if name is not None:
            body["name"] = name
        if hostname is not None:
            body["hostname"] = hostname
        if file_system_names is not None:
            body["file_system_names"] = file_system_names
        if file_system_mounts is not None:
            body["file_system_mounts"] = _entries(file_system_mounts)
        if image is not None:
            body["image"] = image.model_dump(exclude_none=True)
        if user_data is not None:
            body["user_data"] = user_data
        if tags is not None:
            body["tags"] = _entries(tags)
        if firewall_rulesets is not None:
            body["firewall_rulesets"] = [{"id": rid} for rid in firewall_rulesets]
        return _obj(
            await client.request(
                "POST", "/api/v1/instance-operations/launch", json=body
            )
        )

    @tool("restartInstance", write=True, title="Restart instances", annotations=WRITE)
    async def restart_instances(
        instance_ids: Annotated[list[str], Field(description="IDs of instances to restart.")],
    ) -> dict[str, Any]:
        """Restart one or more instances."""
        return _obj(
            await client.request(
                "POST",
                "/api/v1/instance-operations/restart",
                json={"instance_ids": instance_ids},
            )
        )

    @tool(
        "terminateInstance",
        write=True,
        title="Terminate instances",
        annotations=DESTRUCTIVE,
    )
    async def terminate_instances(
        instance_ids: Annotated[
            list[str], Field(description="IDs of instances to terminate.")
        ],
    ) -> dict[str, Any]:
        """Terminate instances permanently.

        This is irreversible. Data on instance-local storage is lost; only
        attached filesystems survive.
        """
        return _obj(
            await client.request(
                "POST",
                "/api/v1/instance-operations/terminate",
                json={"instance_ids": instance_ids},
            )
        )

    @tool("postInstance", write=True, title="Update instance", annotations=IDEMPOTENT)
    async def update_instance(
        instance_id: Annotated[str, Field(description="The instance's unique ID.")],
        name: Annotated[
            str | None, Field(max_length=64, description="New name for the instance.")
        ] = None,
        tags: Annotated[
            list[TagEntry] | None,
            Field(
                description=(
                    "Replaces all existing tags. Pass an empty list to clear them; "
                    "omit the field entirely to leave them untouched."
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Rename an instance or replace its tags."""
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if tags is not None:
            body["tags"] = _entries(tags)
        return _obj(
            await client.request("POST", f"/api/v1/instances/{instance_id}", json=body)
        )

    # ------------------------------------------------------------------ catalog

    @tool("listInstanceTypes", title="List instance types", annotations=READ)
    async def list_instance_types() -> dict[str, Any]:
        """List instance types with specs, hourly price and regional capacity.

        Keyed by instance type name. Check this before calling launch_instance,
        since capacity moves and a type may be unavailable in your region.
        """
        return _obj(await client.request("GET", "/api/v1/instance-types"))

    @tool("listImages", title="List images", annotations=READ)
    async def list_images() -> list[dict[str, Any]]:
        """List the machine images instances can be booted from."""
        return await client.request("GET", "/api/v1/images")

    @tool("listRegions", title="List regions", annotations=READ)
    async def list_regions() -> list[dict[str, Any]]:
        """List the regions in which your account can deploy resources."""
        return await client.request("GET", "/api/v1/regions")

    # -------------------------------------------------------------- filesystems

    # Note the path split below: the API spells the collection '/file-systems'
    # on read but '/filesystems' on write. Do not "tidy" these to match.
    @tool("listFilesystems", title="List filesystems", annotations=READ)
    async def list_filesystems() -> list[dict[str, Any]]:
        """List your shared filesystems, their regions and usage."""
        return await client.request("GET", "/api/v1/file-systems")

    @tool("createFilesystem", write=True, title="Create filesystem", annotations=WRITE)
    async def create_filesystem(
        name: Annotated[
            str,
            Field(
                max_length=60,
                pattern=r"^[a-zA-Z]+[0-9a-zA-Z-]*$",
                description="Filesystem name. Must start with a letter.",
            ),
        ],
        region: Annotated[str, Field(description="Region to create it in.")],
    ) -> dict[str, Any]:
        """Create a shared filesystem in a region."""
        return _obj(
            await client.request(
                "POST", "/api/v1/filesystems", json={"name": name, "region": region}
            )
        )

    @tool(
        "filesystemDelete",
        write=True,
        title="Delete filesystem",
        annotations=DESTRUCTIVE,
    )
    async def delete_filesystem(
        filesystem_id: Annotated[str, Field(description="The filesystem's unique ID.")],
    ) -> dict[str, Any]:
        """Delete a filesystem and everything stored on it. Irreversible.

        A filesystem that is still mounted to an instance cannot be deleted.
        """
        return _obj(
            await client.request("DELETE", f"/api/v1/filesystems/{filesystem_id}")
        )

    # ----------------------------------------------------------------- ssh keys

    @tool("listSSHKeys", title="List SSH keys", annotations=READ)
    async def list_ssh_keys() -> list[dict[str, Any]]:
        """List the SSH keys available to attach to new instances."""
        return await client.request("GET", "/api/v1/ssh-keys")

    @tool("addSSHKey", write=True, title="Add SSH key", annotations=WRITE)
    async def add_ssh_key(
        name: Annotated[str, Field(max_length=64, description="Name for the key.")],
        public_key: Annotated[
            str | None,
            Field(
                max_length=4096,
                description=(
                    "An existing public key to store. Omit it to have Lambda "
                    "generate a new pair and return the private key once."
                ),
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Add an SSH key, or generate a new pair.

        When generating, the response contains the private key. Lambda does not
        retain it, so save it immediately; it cannot be retrieved again.
        """
        body: dict[str, Any] = {"name": name}
        if public_key is not None:
            body["public_key"] = public_key
        return _obj(await client.request("POST", "/api/v1/ssh-keys", json=body))

    @tool("deleteSSHKey", write=True, title="Delete SSH key", annotations=DESTRUCTIVE)
    async def delete_ssh_key(
        ssh_key_id: Annotated[str, Field(description="The SSH key's unique ID.")],
    ) -> dict[str, Any]:
        """Delete an SSH key. Irreversible."""
        return _obj(await client.request("DELETE", f"/api/v1/ssh-keys/{ssh_key_id}"))

    # ---------------------------------------------------------------- firewalls

    @tool("firewallRulesetsList", title="List firewall rulesets", annotations=READ)
    async def list_firewall_rulesets() -> list[dict[str, Any]]:
        """List firewall rulesets and the instances they are attached to."""
        return await client.request("GET", "/api/v1/firewall-rulesets")

    @tool(
        "getFirewallRuleset",
        "getGlobalFirewallRuleset",
        title="Get firewall ruleset",
        annotations=READ,
    )
    async def get_firewall_ruleset(
        ruleset_id: Annotated[
            str,
            Field(
                description=(
                    "The ruleset's unique ID, or the literal 'global' for the "
                    "account-wide ruleset applied to every instance."
                )
            ),
        ],
    ) -> dict[str, Any]:
        """Retrieve one firewall ruleset, or the global ruleset."""
        return await client.request("GET", _ruleset_path(ruleset_id))

    @tool(
        "createFirewallRuleset",
        write=True,
        title="Create firewall ruleset",
        annotations=WRITE,
    )
    async def create_firewall_ruleset(
        name: Annotated[str, Field(max_length=64, description="Name for the ruleset.")],
        region: Annotated[str, Field(description="Region the ruleset is deployed in.")],
        rules: Annotated[
            list[FirewallRule], Field(description="The inbound rules to include.")
        ],
    ) -> dict[str, Any]:
        """Create a firewall ruleset that instances in one region can use."""
        return _obj(
            await client.request(
                "POST",
                "/api/v1/firewall-rulesets",
                json={"name": name, "region": region, "rules": _rules(rules)},
            )
        )

    @tool(
        "updateFirewallRuleset",
        "updateGlobalFirewallRuleset",
        write=True,
        title="Update firewall ruleset",
        annotations=IDEMPOTENT,
    )
    async def update_firewall_ruleset(
        ruleset_id: Annotated[
            str,
            Field(description="The ruleset's unique ID, or 'global' for the global ruleset."),
        ],
        name: Annotated[
            str | None,
            Field(max_length=64, description="New name. Not supported for the global ruleset."),
        ] = None,
        rules: Annotated[
            list[FirewallRule] | None,
            Field(description="Replaces every rule in the ruleset. Omit to leave rules alone."),
        ] = None,
    ) -> dict[str, Any]:
        """Rename a firewall ruleset or replace its rules.

        Supplying 'rules' replaces the entire rule list, it does not append.
        """
        is_global = ruleset_id == GLOBAL
        if is_global and name is not None:
            raise ValueError("the global firewall ruleset cannot be renamed")
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if rules is not None:
            body["rules"] = _rules(rules)
        return _obj(
            await client.request("PATCH", _ruleset_path(ruleset_id), json=body)
        )

    @tool(
        "deleteFirewallRuleset",
        write=True,
        title="Delete firewall ruleset",
        annotations=DESTRUCTIVE,
    )
    async def delete_firewall_ruleset(
        ruleset_id: Annotated[str, Field(description="The ruleset's unique ID.")],
    ) -> dict[str, Any]:
        """Delete a firewall ruleset. Irreversible.

        A ruleset still attached to an instance cannot be deleted.
        """
        if ruleset_id == GLOBAL:
            raise ValueError("the global firewall ruleset cannot be deleted")
        return _obj(
            await client.request("DELETE", f"/api/v1/firewall-rulesets/{ruleset_id}")
        )

    @tool("firewallRulesList", title="List firewall rules", annotations=READ)
    async def list_firewall_rules() -> list[dict[str, Any]]:
        """List the account-wide inbound firewall rules.

        These are separate from firewall rulesets, which are per-region and
        attached to specific instances.
        """
        return await client.request("GET", "/api/v1/firewall-rules")

    @tool(
        "firewallRulesSet",
        write=True,
        title="Replace firewall rules",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True,
            idempotent_hint=True, open_world_hint=False,
        ),
    )
    async def set_firewall_rules(
        rules: Annotated[
            list[FirewallRule],
            Field(description="The complete new rule list. Anything omitted is removed."),
        ],
    ) -> list[dict[str, Any]]:
        """Replace every account-wide inbound firewall rule.

        This overwrites the whole list rather than appending. Call
        list_firewall_rules first and resend the rules you intend to keep,
        otherwise you will remove them, potentially locking yourself out of
        running instances.
        """
        return await client.request(
            "PUT", "/api/v1/firewall-rules", json={"data": _rules(rules)}
        )

    # ------------------------------------------------------------------ tickets

    @tool("listTickets", title="List support tickets", annotations=READ)
    async def list_tickets(
        status: Annotated[
            list[TicketStatus] | None, Field(description="Filter by ticket status.")
        ] = None,
        type: Annotated[
            list[TicketType] | None, Field(description="Filter by ticket type.")
        ] = None,
        severity: Annotated[
            list[TicketSeverity] | None, Field(description="Filter by severity.")
        ] = None,
        priority: Annotated[
            list[TicketPriority] | None, Field(description="Filter by priority.")
        ] = None,
        device_serial: Annotated[
            list[str] | None, Field(description="Filter by affected device serial.")
        ] = None,
        order_by: Annotated[
            Literal["created_at", "updated_at"] | None,
            Field(description="Field to sort by."),
        ] = None,
        direction: Annotated[
            Literal["asc", "desc"] | None, Field(description="Sort direction.")
        ] = None,
        page_token: Annotated[
            str | None,
            Field(description="Token from a previous response's page_token."),
        ] = None,
    ) -> dict[str, Any]:
        """List support tickets. Returns one page plus a page_token for the next.

        The Support Ticketing API is in beta and must be enabled on your account.
        """
        return _obj(
            await client.request(
                "GET",
                "/api/v1/tickets",
                params={
                    "status": status,
                    "type": type,
                    "severity": severity,
                    "priority": priority,
                    "device_serial": device_serial,
                    "order_by": order_by,
                    "direction": direction,
                    "page_token": page_token,
                },
            )
        )

    @tool("getTicket", title="Get support ticket", annotations=READ)
    async def get_ticket(
        ticket_id: Annotated[str, Field(description="The ticket's unique ID.")],
    ) -> dict[str, Any]:
        """Retrieve one support ticket, including its comment history."""
        return await client.request("GET", f"/api/v1/tickets/{ticket_id}")

    @tool("createTicket", write=True, title="Create support ticket", annotations=WRITE)
    async def create_ticket(
        subject: Annotated[str, Field(description="A short summary of the issue.")],
        description: Annotated[
            str, Field(description="Details of the issue. Becomes the first public comment.")
        ],
        type: Annotated[
            Literal["incident", "service_request"],
            Field(description="Ticket type."),
        ],
        severity: Annotated[
            TicketSeverity | None,
            Field(
                description=(
                    "Required for incident tickets, and must be omitted for "
                    "service requests."
                )
            ),
        ] = None,
        priority: Annotated[
            TicketPriority | None, Field(description="Priority level.")
        ] = None,
        ip: Annotated[str | None, Field(description="IP of the affected node.")] = None,
        instance_id: Annotated[
            str | None, Field(description="ID of the affected on-demand instance.")
        ] = None,
        cluster_id: Annotated[
            str | None, Field(description="ID of the affected 1CC or Supercluster.")
        ] = None,
        hostname: Annotated[
            str | None, Field(description="Hostname of the affected device.")
        ] = None,
        device_serial: Annotated[
            str | None, Field(description="Serial number of the affected device.")
        ] = None,
        requester_email: Annotated[
            str | None, Field(description="Email to associate with the requester.")
        ] = None,
        affected_devices: Annotated[
            list[str] | None, Field(description="Devices affected by the issue.")
        ] = None,
    ) -> dict[str, Any]:
        """Open a support ticket with Lambda.

        The Support Ticketing API is in beta and must be enabled on your account.
        """
        if type == "incident" and severity is None:
            raise ValueError("severity is required when type is 'incident'")
        if type == "service_request" and severity is not None:
            raise ValueError("severity must be omitted when type is 'service_request'")

        body: dict[str, Any] = {
            "subject": subject,
            "description": description,
            "type": type,
        }
        optional = {
            "severity": severity,
            "priority": priority,
            "ip": ip,
            "instance_id": instance_id,
            "cluster_id": cluster_id,
            "hostname": hostname,
            "device_serial": device_serial,
            "requester_email": requester_email,
            "affected_devices": affected_devices,
        }
        body.update({k: v for k, v in optional.items() if v is not None})
        return _obj(await client.request("POST", "/api/v1/tickets", json=body))

    @tool("updateTicket", write=True, title="Update support ticket", annotations=WRITE)
    async def update_ticket(
        ticket_id: Annotated[str, Field(description="The ticket's unique ID.")],
        status: Annotated[
            Literal["solved"] | None,
            Field(description="Set to 'solved' to resolve the ticket."),
        ] = None,
        severity: Annotated[TicketSeverity | None, Field(description="New severity.")] = None,
        priority: Annotated[TicketPriority | None, Field(description="New priority.")] = None,
        comment: Annotated[
            str | None, Field(description="Text of a new comment to add.")
        ] = None,
        affected_devices: Annotated[
            list[str] | None, Field(description="Replacement list of affected devices.")
        ] = None,
    ) -> dict[str, Any]:
        """Comment on a ticket, change its severity or priority, or resolve it."""
        body: dict[str, Any] = {}
        if status is not None:
            body["status"] = status
        if severity is not None:
            body["severity"] = severity
        if priority is not None:
            body["priority"] = priority
        if comment is not None:
            body["comment"] = {"body": comment}
        if affected_devices is not None:
            body["affected_devices"] = affected_devices
        return _obj(
            await client.request("PATCH", f"/api/v1/tickets/{ticket_id}", json=body)
        )

    @tool(
        "listAttachments",
        "initiateAttachmentUpload",
        "completeAttachmentUpload",
        "deleteAttachment",
        "getAttachmentDownloadUrl",
        write=True,
        title="Manage ticket attachment",
        annotations=WRITE,
    )
    async def manage_ticket_attachment(
        action: Annotated[
            Literal["list", "initiate_upload", "complete_upload", "delete", "get_download_url"],
            Field(description="Which attachment operation to perform."),
        ],
        ticket_id: Annotated[str, Field(description="The ticket's unique ID.")],
        attachment_id: Annotated[
            str | None,
            Field(description="Required for complete_upload, delete and get_download_url."),
        ] = None,
        filename: Annotated[
            str | None,
            Field(max_length=255, description="For initiate_upload: the original filename."),
        ] = None,
        content_type: Annotated[
            str | None,
            Field(description="For initiate_upload: MIME type, e.g. 'application/gzip'."),
        ] = None,
        size_bytes: Annotated[
            int | None,
            Field(ge=1, description="For initiate_upload: file size in bytes."),
        ] = None,
        status: Annotated[
            Literal["pending", "uploaded", "failed", "deleted"] | None,
            Field(description="For list: filter by upload status."),
        ] = None,
    ) -> dict[str, Any]:
        """Work with a support ticket's file attachments.

        Uploading is a three-step flow: 'initiate_upload' returns a presigned
        URL, you PUT or POST the file to that URL yourself, then you call
        'complete_upload' with the returned attachment_id.
        """
        base = f"/api/v1/tickets/{ticket_id}/attachments"

        if action == "list":
            return _obj(
                await client.request("GET", base, params={"status": status})
            )

        if action == "initiate_upload":
            missing = [
                field
                for field, value in (
                    ("filename", filename),
                    ("content_type", content_type),
                    ("size_bytes", size_bytes),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"initiate_upload requires {', '.join(missing)}"
                )
            return _obj(
                await client.request(
                    "POST",
                    base,
                    json={
                        "filename": filename,
                        "content_type": content_type,
                        "size_bytes": size_bytes,
                    },
                )
            )

        if attachment_id is None:
            raise ValueError(f"attachment_id is required for the '{action}' action")

        if action == "complete_upload":
            return _obj(
                await client.request("POST", f"{base}/{attachment_id}/complete")
            )
        if action == "delete":
            return _obj(await client.request("DELETE", f"{base}/{attachment_id}"))
        return _obj(
            await client.request("GET", f"{base}/{attachment_id}/download")
        )

    # -------------------------------------------------------------------- audit

    @tool("getAuditEvents", title="List audit events", annotations=READ)
    async def list_audit_events(
        start: Annotated[
            str | None,
            Field(description="ISO 8601 start of the range, inclusive."),
        ] = None,
        end: Annotated[
            str | None, Field(description="ISO 8601 end of the range, inclusive.")
        ] = None,
        resource_type: Annotated[
            str | None,
            Field(description="Filter to one resource type, e.g. 'cloud.api_key'."),
        ] = None,
        page_token: Annotated[
            str | None,
            Field(description="Token from a previous response, to fetch the next page."),
        ] = None,
    ) -> dict[str, Any]:
        """Read the account audit log. Returns one page plus a next page_token."""
        return _obj(
            await client.request(
                "GET",
                "/api/v1/audit-events",
                params={
                    "start": start,
                    "end": end,
                    "resource_type": resource_type,
                    "page_token": page_token,
                },
            )
        )

    return mcp
