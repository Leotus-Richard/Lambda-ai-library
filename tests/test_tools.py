"""Per-tool request-shape tests.

Each test asserts the exact method, path, query and body a tool puts on the
wire. Expected values come from the OpenAPI spec's own paths and examples,
never from re-running the implementation's own logic.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


def _error_text(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


# --------------------------------------------------------------------- reads


async def test_list_regions_gets_regions_with_bearer_auth(api, connect):
    api.queue(data=[{"name": "us-west-1", "description": "California, USA"}])

    async with connect() as client:
        result = await client.call_tool("list_regions", {})

    request = api.last
    assert request.method == "GET"
    assert request.url.path == "/api/v1/regions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert result.structured_content["result"] == [
        {"name": "us-west-1", "description": "California, USA"}
    ]


async def test_list_images_gets_images(api, connect):
    api.queue(data=[])
    async with connect() as client:
        await client.call_tool("list_images", {})
    assert api.last.url.path == "/api/v1/images"


async def test_list_instance_types_returns_the_keyed_map(api, connect):
    api.queue(data={"gpu_8x_a100": {"instance_type": {"name": "gpu_8x_a100"}}})

    async with connect() as client:
        result = await client.call_tool("list_instance_types", {})

    assert api.last.url.path == "/api/v1/instance-types"
    assert "gpu_8x_a100" in result.structured_content


async def test_list_instances_omits_cluster_filter_when_unset(api, connect):
    api.queue(data=[])
    async with connect() as client:
        await client.call_tool("list_instances", {})
    assert api.last.url.path == "/api/v1/instances"
    assert "cluster_id" not in str(api.last.url)


async def test_list_instances_passes_cluster_filter(api, connect):
    api.queue(data=[])
    async with connect() as client:
        await client.call_tool("list_instances", {"cluster_id": "abc123"})
    assert api.last.url.params["cluster_id"] == "abc123"


async def test_get_instance_uses_the_id_in_the_path(api, connect):
    api.queue(data={"id": "i-1"})
    async with connect() as client:
        await client.call_tool("get_instance", {"instance_id": "i-1"})
    assert api.last.url.path == "/api/v1/instances/i-1"


# --------------------------------------------------------------- filesystems


async def test_list_filesystems_uses_the_hyphenated_read_path(api, connect):
    """The API spells the collection '/file-systems' on read. Do not normalise."""
    api.queue(data=[])

    async with connect() as client:
        await client.call_tool("list_filesystems", {})

    assert api.last.url.path == "/api/v1/file-systems"


async def test_create_filesystem_uses_the_unhyphenated_write_path(api, connect):
    """...but '/filesystems' on write. The two spellings are both real."""
    api.queue(data={"id": "fs-1"})

    async with connect() as client:
        await client.call_tool(
            "create_filesystem", {"name": "my-filesystem", "region": "us-west-1"}
        )

    assert api.last.method == "POST"
    assert api.last.url.path == "/api/v1/filesystems"
    assert api.last_json() == {"name": "my-filesystem", "region": "us-west-1"}


async def test_delete_filesystem_uses_the_unhyphenated_write_path(api, connect):
    api.queue(data={"deleted_ids": ["fs-1"]})

    async with connect() as client:
        await client.call_tool("delete_filesystem", {"filesystem_id": "fs-1"})

    assert api.last.method == "DELETE"
    assert api.last.url.path == "/api/v1/filesystems/fs-1"


async def test_create_filesystem_rejects_a_name_starting_with_a_digit(api, connect):
    async with connect() as client:
        result = await client.call_tool(
            "create_filesystem", {"name": "1bad", "region": "us-west-1"}
        )

    assert result.is_error
    assert not api.requests


# ----------------------------------------------------------------- instances


async def test_launch_instance_sends_only_the_required_fields_by_default(api, connect):
    api.queue(data={"instance_ids": ["i-1"]})

    async with connect() as client:
        await client.call_tool(
            "launch_instance",
            {
                "region_name": "us-west-1",
                "instance_type_name": "gpu_8x_a100",
                "ssh_key_names": ["my-public-key"],
            },
        )

    assert api.last.url.path == "/api/v1/instance-operations/launch"
    assert api.last_json() == {
        "region_name": "us-west-1",
        "instance_type_name": "gpu_8x_a100",
        "ssh_key_names": ["my-public-key"],
    }


async def test_launch_instance_serialises_nested_objects(api, connect):
    api.queue(data={"instance_ids": ["i-1"]})

    async with connect() as client:
        await client.call_tool(
            "launch_instance",
            {
                "region_name": "us-west-1",
                "instance_type_name": "gpu_8x_a100",
                "ssh_key_names": ["k"],
                "image": {"family": "ubuntu-lts"},
                "tags": [{"key": "env", "value": "prod"}],
                "file_system_mounts": [
                    {"file_system_id": "fs-1", "mount_point": "/data/custom"}
                ],
                "firewall_rulesets": ["fw-1", "fw-2"],
            },
        )

    body = api.last_json()
    assert body["image"] == {"family": "ubuntu-lts"}
    assert body["tags"] == [{"key": "env", "value": "prod"}]
    assert body["file_system_mounts"] == [
        {"file_system_id": "fs-1", "mount_point": "/data/custom"}
    ]
    assert body["firewall_rulesets"] == [{"id": "fw-1"}, {"id": "fw-2"}]


async def test_launch_instance_rejects_an_image_with_both_id_and_family(api, connect):
    async with connect() as client:
        result = await client.call_tool(
            "launch_instance",
            {
                "region_name": "us-west-1",
                "instance_type_name": "gpu_8x_a100",
                "ssh_key_names": ["k"],
                "image": {"id": "img-1", "family": "ubuntu-lts"},
            },
        )

    assert result.is_error
    assert not api.requests


async def test_terminate_instances_posts_the_id_list(api, connect):
    api.queue(data={"terminated_instances": []})

    async with connect() as client:
        await client.call_tool("terminate_instances", {"instance_ids": ["i-1", "i-2"]})

    assert api.last.url.path == "/api/v1/instance-operations/terminate"
    assert api.last_json() == {"instance_ids": ["i-1", "i-2"]}


async def test_restart_instances_posts_the_id_list(api, connect):
    api.queue(data={"restarted_instances": []})

    async with connect() as client:
        await client.call_tool("restart_instances", {"instance_ids": ["i-1"]})

    assert api.last.url.path == "/api/v1/instance-operations/restart"
    assert api.last_json() == {"instance_ids": ["i-1"]}


async def test_update_instance_omits_tags_when_not_supplied(api, connect):
    """Omitting tags must leave them untouched, so the key must not be sent."""
    api.queue(data={"id": "i-1"})

    async with connect() as client:
        await client.call_tool(
            "update_instance", {"instance_id": "i-1", "name": "renamed"}
        )

    assert api.last_json() == {"name": "renamed"}


async def test_update_instance_sends_an_empty_tag_list_to_clear_tags(api, connect):
    """An empty list is meaningful: it clears the tags. It must survive."""
    api.queue(data={"id": "i-1"})

    async with connect() as client:
        await client.call_tool("update_instance", {"instance_id": "i-1", "tags": []})

    assert api.last_json() == {"tags": []}


# ------------------------------------------------------------------ ssh keys


async def test_add_ssh_key_omits_public_key_to_request_generation(api, connect):
    api.queue(data={"id": "k-1", "private_key": "-----BEGIN..."})

    async with connect() as client:
        await client.call_tool("add_ssh_key", {"name": "my-public-key"})

    assert api.last_json() == {"name": "my-public-key"}


async def test_add_ssh_key_sends_a_supplied_public_key(api, connect):
    api.queue(data={"id": "k-1"})

    async with connect() as client:
        await client.call_tool(
            "add_ssh_key", {"name": "k", "public_key": "ssh-ed25519 AAAA"}
        )

    assert api.last_json() == {"name": "k", "public_key": "ssh-ed25519 AAAA"}


async def test_delete_ssh_key_uses_the_id_in_the_path(api, connect):
    api.queue(data={})

    async with connect() as client:
        await client.call_tool("delete_ssh_key", {"ssh_key_id": "k-1"})

    assert api.last.method == "DELETE"
    assert api.last.url.path == "/api/v1/ssh-keys/k-1"


# ----------------------------------------------------------------- firewalls


async def test_get_firewall_ruleset_routes_global_to_its_own_path(api, connect):
    api.queue(data={"rules": []})

    async with connect() as client:
        await client.call_tool("get_firewall_ruleset", {"ruleset_id": "global"})

    assert api.last.url.path == "/api/v1/firewall-rulesets/global"


async def test_get_firewall_ruleset_routes_an_id_to_the_id_path(api, connect):
    api.queue(data={"id": "fw-1"})

    async with connect() as client:
        await client.call_tool("get_firewall_ruleset", {"ruleset_id": "fw-1"})

    assert api.last.url.path == "/api/v1/firewall-rulesets/fw-1"


async def test_update_global_firewall_ruleset_patches_the_global_path(api, connect):
    api.queue(data={"rules": []})

    async with connect() as client:
        await client.call_tool(
            "update_firewall_ruleset",
            {
                "ruleset_id": "global",
                "rules": [
                    {
                        "protocol": "tcp",
                        "port_range": [22, 22],
                        "source_network": "0.0.0.0/0",
                        "description": "Allow SSH from anywhere",
                    }
                ],
            },
        )

    assert api.last.method == "PATCH"
    assert api.last.url.path == "/api/v1/firewall-rulesets/global"
    assert api.last_json() == {
        "rules": [
            {
                "protocol": "tcp",
                "port_range": [22, 22],
                "source_network": "0.0.0.0/0",
                "description": "Allow SSH from anywhere",
            }
        ]
    }


async def test_global_firewall_ruleset_cannot_be_renamed(api, connect):
    async with connect() as client:
        result = await client.call_tool(
            "update_firewall_ruleset", {"ruleset_id": "global", "name": "nope"}
        )

    assert result.is_error
    assert not api.requests


async def test_global_firewall_ruleset_cannot_be_deleted(api, connect):
    async with connect() as client:
        result = await client.call_tool(
            "delete_firewall_ruleset", {"ruleset_id": "global"}
        )

    assert result.is_error
    assert not api.requests


async def test_create_firewall_ruleset_sends_name_region_and_rules(api, connect):
    api.queue(data={"id": "fw-1"})

    async with connect() as client:
        await client.call_tool(
            "create_firewall_ruleset",
            {
                "name": "My Firewall Ruleset",
                "region": "us-west-1",
                "rules": [
                    {
                        "protocol": "icmp",
                        "source_network": "0.0.0.0/0",
                        "description": "Allow ping",
                    }
                ],
            },
        )

    assert api.last_json() == {
        "name": "My Firewall Ruleset",
        "region": "us-west-1",
        "rules": [
            {
                "protocol": "icmp",
                "source_network": "0.0.0.0/0",
                "description": "Allow ping",
            }
        ],
    }


async def test_icmp_rule_rejects_a_port_range(api, connect):
    async with connect() as client:
        result = await client.call_tool(
            "create_firewall_ruleset",
            {
                "name": "r",
                "region": "us-west-1",
                "rules": [
                    {
                        "protocol": "icmp",
                        "port_range": [22, 22],
                        "source_network": "0.0.0.0/0",
                        "description": "bad",
                    }
                ],
            },
        )

    assert result.is_error
    assert not api.requests


async def test_tcp_rule_requires_a_port_range(api, connect):
    async with connect() as client:
        result = await client.call_tool(
            "create_firewall_ruleset",
            {
                "name": "r",
                "region": "us-west-1",
                "rules": [
                    {
                        "protocol": "tcp",
                        "source_network": "0.0.0.0/0",
                        "description": "bad",
                    }
                ],
            },
        )

    assert result.is_error
    assert not api.requests


async def test_set_firewall_rules_wraps_the_list_in_a_data_key(api, connect):
    """The PUT body is {"data": [...]}, unlike every other request body."""
    api.queue(data=[])

    async with connect() as client:
        await client.call_tool(
            "set_firewall_rules",
            {
                "rules": [
                    {
                        "protocol": "tcp",
                        "port_range": [22, 22],
                        "source_network": "0.0.0.0/0",
                        "description": "Allow SSH from anywhere",
                    }
                ]
            },
        )

    assert api.last.method == "PUT"
    assert api.last.url.path == "/api/v1/firewall-rules"
    assert api.last_json() == {
        "data": [
            {
                "protocol": "tcp",
                "port_range": [22, 22],
                "source_network": "0.0.0.0/0",
                "description": "Allow SSH from anywhere",
            }
        ]
    }


# ------------------------------------------------------------------- tickets


async def test_list_tickets_repeats_array_filters_as_separate_params(api, connect):
    """Array filters are repeated query params, not comma-separated values."""
    api.queue(data={"tickets": [], "page_token": None})

    async with connect() as client:
        await client.call_tool(
            "list_tickets", {"status": ["open", "pending"], "order_by": "created_at"}
        )

    params = api.last.url.params
    assert params.get_list("status") == ["open", "pending"]
    assert params["order_by"] == "created_at"


async def test_create_ticket_requires_severity_for_an_incident(api, connect):
    async with connect() as client:
        result = await client.call_tool(
            "create_ticket",
            {"subject": "s", "description": "d", "type": "incident"},
        )

    assert result.is_error
    assert "severity" in _error_text(result)
    assert not api.requests


async def test_create_ticket_rejects_severity_on_a_service_request(api, connect):
    async with connect() as client:
        result = await client.call_tool(
            "create_ticket",
            {
                "subject": "s",
                "description": "d",
                "type": "service_request",
                "severity": "sev_1",
            },
        )

    assert result.is_error
    assert not api.requests


async def test_create_ticket_sends_supplied_optional_fields(api, connect):
    api.queue(data={"id": "t-1"})

    async with connect() as client:
        await client.call_tool(
            "create_ticket",
            {
                "subject": "GPU degradation",
                "description": "Performance drops 30%.",
                "type": "incident",
                "severity": "sev_2",
                "instance_id": "i-1",
            },
        )

    assert api.last_json() == {
        "subject": "GPU degradation",
        "description": "Performance drops 30%.",
        "type": "incident",
        "severity": "sev_2",
        "instance_id": "i-1",
    }


async def test_update_ticket_nests_a_comment_under_body(api, connect):
    api.queue(data={"id": "t-1"})

    async with connect() as client:
        await client.call_tool(
            "update_ticket",
            {"ticket_id": "t-1", "status": "solved", "comment": "Closing this."},
        )

    assert api.last.method == "PATCH"
    assert api.last.url.path == "/api/v1/tickets/t-1"
    assert api.last_json() == {"status": "solved", "comment": {"body": "Closing this."}}


@pytest.mark.parametrize(
    ("action", "extra", "method", "path"),
    [
        ("list", {}, "GET", "/api/v1/tickets/t-1/attachments"),
        (
            "initiate_upload",
            {"filename": "logs.gz", "content_type": "application/gzip", "size_bytes": 1024},
            "POST",
            "/api/v1/tickets/t-1/attachments",
        ),
        (
            "complete_upload",
            {"attachment_id": "a-1"},
            "POST",
            "/api/v1/tickets/t-1/attachments/a-1/complete",
        ),
        ("delete", {"attachment_id": "a-1"}, "DELETE", "/api/v1/tickets/t-1/attachments/a-1"),
        (
            "get_download_url",
            {"attachment_id": "a-1"},
            "GET",
            "/api/v1/tickets/t-1/attachments/a-1/download",
        ),
    ],
)
async def test_manage_ticket_attachment_routes_each_action(
    api, connect, action, extra, method, path
):
    api.queue(data={})

    async with connect() as client:
        result = await client.call_tool(
            "manage_ticket_attachment",
            {"action": action, "ticket_id": "t-1", **extra},
        )

    assert not result.is_error, _error_text(result)
    assert api.last.method == method
    assert api.last.url.path == path


async def test_manage_ticket_attachment_requires_an_attachment_id(api, connect):
    async with connect() as client:
        result = await client.call_tool(
            "manage_ticket_attachment", {"action": "delete", "ticket_id": "t-1"}
        )

    assert result.is_error
    assert "attachment_id" in _error_text(result)
    assert not api.requests


async def test_manage_ticket_attachment_requires_upload_metadata(api, connect):
    async with connect() as client:
        result = await client.call_tool(
            "manage_ticket_attachment",
            {"action": "initiate_upload", "ticket_id": "t-1", "filename": "logs.gz"},
        )

    assert result.is_error
    assert "content_type" in _error_text(result)
    assert not api.requests


# --------------------------------------------------------------------- audit


async def test_list_audit_events_passes_the_time_range_and_page_token(api, connect):
    api.queue(data={"events": [], "page_token": None})

    async with connect() as client:
        await client.call_tool(
            "list_audit_events",
            {
                "start": "2025-09-01T10:30:45.123456Z",
                "resource_type": "cloud.api_key",
                "page_token": "abCdEFg0h1I2jKlm34n5O6Pq78r=",
            },
        )

    params = api.last.url.params
    assert api.last.url.path == "/api/v1/audit-events"
    assert params["start"] == "2025-09-01T10:30:45.123456Z"
    assert params["resource_type"] == "cloud.api_key"
    assert params["page_token"] == "abCdEFg0h1I2jKlm34n5O6Pq78r="
    assert "end" not in params
