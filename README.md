# Lambda-ai-library
Un-official. created out of necessity. no warranty. 
automating the lambda ai endpoints for agentic workflows

---

A [Model Context Protocol](https://modelcontextprotocol.io) server for the
Lambda Cloud API v1.10.0. It gives an AI agent 28 tools covering all 34 API
operations: GPU instances, filesystems, SSH keys, firewalls, support tickets,
and the audit log.

It ships read-only. Nothing can spend your money or delete your data until you
opt in.

## Install

```bash
pip install .
```

Python 3.10 or newer. The only runtime dependency is the official `mcp` SDK.

## Configure

Get an API key from the [Lambda API keys
page](https://cloud.lambda.ai/api-keys).

| Variable | Default | What it does |
|---|---|---|
| `LAMBDA_API_KEY` | none, required | Your API key. The server refuses to start without it. |
| `LAMBDA_MCP_ALLOW_WRITE` | unset | Set to `1` to register the 15 mutating tools. Unset, they do not exist. |
| `LAMBDA_API_BASE` | `https://cloud.lambda.ai` | Point at a different host. |

Add it to your MCP client:

```json
{
  "mcpServers": {
    "lambda-cloud": {
      "command": "lambda-mcp",
      "env": {
        "LAMBDA_API_KEY": "your-key-here"
      }
    }
  }
}
```

To allow writes, add `"LAMBDA_MCP_ALLOW_WRITE": "1"` next to the key.

You can also run it with `python -m lambda_mcp`.

## The write gate

I did not want an agent terminating a training run because it misread a prompt,
so mutating tools are withheld at registration time rather than refused at call
time. With `LAMBDA_MCP_ALLOW_WRITE` unset the model sees 13 read tools and never
learns the other 15 exist. That beats a runtime error, which the model reads as
a puzzle to solve.

A second check sits inside the HTTP client and rejects any non-GET request when
writes are off, so a new tool cannot forget the gate.

Tools carry MCP annotations. Clients that surface `destructiveHint` will warn
before `terminate_instances`, `delete_filesystem`, `delete_ssh_key`,
`delete_firewall_ruleset` and `set_firewall_rules`. Do not rely on that alone.
Annotations are hints, and clients may ignore them.

## Tools

### Instances

| Tool | Kind | Description |
|---|---|---|
| `list_instances` | read | Your running instances, with status, IP and region. |
| `get_instance` | read | One instance in detail, including SSH and Jupyter access. |
| `list_instance_types` | read | Specs, hourly price and regional capacity. |
| `launch_instance` | write | Launch an instance. Billing starts immediately. |
| `restart_instances` | write | Restart one or more instances. |
| `terminate_instances` | destructive | Terminate instances permanently. |
| `update_instance` | write | Rename an instance or replace its tags. |

### Images and regions

| Tool | Kind | Description |
|---|---|---|
| `list_images` | read | Machine images instances can boot from. |
| `list_regions` | read | Regions your account can deploy into. |

### Filesystems

| Tool | Kind | Description |
|---|---|---|
| `list_filesystems` | read | Your shared filesystems, regions and usage. |
| `create_filesystem` | write | Create a filesystem in a region. |
| `delete_filesystem` | destructive | Delete a filesystem and its contents. |

### SSH keys

| Tool | Kind | Description |
|---|---|---|
| `list_ssh_keys` | read | Keys available to attach to new instances. |
| `add_ssh_key` | write | Upload a public key, or generate a pair. |
| `delete_ssh_key` | destructive | Delete a key. |

Omit `public_key` from `add_ssh_key` and Lambda generates a pair and returns the
private key once. Lambda does not store it, so save it or lose it.

### Firewalls

`firewall-rules` is one flat account-wide list. Rulesets are per-region objects
you attach to instances. They are separate resources that happen to share a
name, which is worth knowing before you go looking for a rule in the wrong
place.

| Tool | Kind | Description |
|---|---|---|
| `list_firewall_rules` | read | The account-wide inbound rules. |
| `set_firewall_rules` | destructive | Replace every account-wide rule. |
| `list_firewall_rulesets` | read | Rulesets and the instances using them. |
| `get_firewall_ruleset` | read | One ruleset, or pass `global`. |
| `create_firewall_ruleset` | write | Create a ruleset in a region. |
| `update_firewall_ruleset` | write | Rename a ruleset or replace its rules. |
| `delete_firewall_ruleset` | destructive | Delete a ruleset. |

`set_firewall_rules` and `update_firewall_ruleset` replace the whole rule list.
They do not append. Read the current rules first.

The global ruleset applies everywhere. You can read it and change its rules, but
you cannot rename or delete it, and the tools reject both attempts before making
a request.

### Support tickets

Support tickets are a beta API and your account needs them enabled. The tools
are built and tested against the spec either way.

| Tool | Kind | Description |
|---|---|---|
| `list_tickets` | read | One page of tickets, plus a token for the next. |
| `get_ticket` | read | One ticket and its comment history. |
| `create_ticket` | write | Open a ticket. |
| `update_ticket` | write | Comment, change severity or priority, or resolve. |
| `manage_ticket_attachment` | write | List, upload, download or delete attachments. |

`severity` is required for an `incident` and rejected for a `service_request`.

`manage_ticket_attachment` is gated as a write tool because it can upload and
delete, so listing and downloading attachments also needs writes enabled.

### Audit log

| Tool | Kind | Description |
|---|---|---|
| `list_audit_events` | read | One page of account audit events. |

## How coverage is guaranteed

The point of this repo is that endpoints do not go missing quietly.

Every tool declares the operation IDs it covers. `tests/test_coverage.py` reads
the vendored `spec/lambda-cloud-1.10.0.json` and checks that against the running
server:

- Every one of the 34 operation IDs is claimed by exactly one tool. Drop a tool
  and the failure message names the operations you lost.
- No tool claims an operation ID the spec does not have, so a typo fails.
- Every registered tool declares coverage, so nothing slips in undeclared.

Upgrading the spec is then a failing test listing the new operations rather than
a silent gap.

On top of that, each tool has tests pinning the exact method, path, query and
body it puts on the wire. Those exist because the spec has traps. `GET
/api/v1/file-systems` is hyphenated while `POST /api/v1/filesystems` is not, and
tidying that up looks like a cleanup right until every create call 404s. Ticket
filters repeat the query parameter instead of using commas. `PUT
/firewall-rules` is the one request body wrapped in `data`. Omitting `tags` on
`update_instance` keeps the existing tags while sending an empty list clears
them, so the two cases have to stay distinguishable.

I checked the tests by breaking the code on purpose. Normalising the filesystem
path, dropping the `data` wrapper, always sending `tags`, allowing a rename of
the global ruleset, and removing the write gate each fail a test.

## Development

```bash
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

55 tests, no network. A fake transport answers every request, so the suite runs
without an API key.

Tests sit at two boundaries only: the MCP tool boundary, using an in-memory
client that speaks the real protocol, and the outgoing HTTP boundary. Nothing
tests a private function, so internals can be rewritten freely.

## Known limits

- Responses come back whole. A large `list_instance_types` result will use a lot
  of context.
- Retries are reactive. The client backs off on 429 and 5xx and honours
  `Retry-After`, but does not pace itself to Lambda's one request per second.
  Launches are capped tighter still, at one per 12 seconds.
- stdio transport only.
- No auto-pagination. `page_token` is passed through so the model decides how
  deep to go.

