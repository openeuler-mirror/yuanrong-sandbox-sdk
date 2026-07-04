# openyuanrong-sandbox Python SDK

Python SDK for openYuanrong remote sandboxes.  The transport talks to the
frontend **sandbox v1** HTTP/WS interface backed by RRT. It uses the
frontend sandbox API directly.

```python
from yr_sandbox import Sandbox

with Sandbox(image="python:3.12-slim", cpu=2000, memory=4096) as sb:
    sb.files.write("/tmp/hello.txt", "hello world")
    print(sb.commands.run("cat /tmp/hello.txt").stdout)
```

## Configuration

| Var | Meaning |
| --- | --- |
| `YR_SERVER_ADDRESS` | Frontend gateway `host:port` for lifecycle, invoke, direct file IO. Required. |
| `YR_TOKEN` | JWT sent in `X-Auth` where required. |
| `YR_TLS` | Set `1/true/yes` to use HTTPS/WSS for frontend control routes. Default: `0`. |
| `YR_GATEWAY_ADDRESS` | Optional sandbox gateway/router `host:port` for tunnel and user port URLs. Falls back to `YR_SERVER_ADDRESS`. |
| `YR_GATEWAY_TLS` | Set `1/true/yes` to use WSS for gateway tunnel routes. Default: `0`. |
| `YR_STREAM_ADDRESS` | Optional frontend host for `/api/sandbox/v1/.../stream`; defaults to `YR_SERVER_ADDRESS`. |
| `YR_STREAM_TLS` | Optional TLS override for stream routes. |
| `YR_TUNNEL_CONNECT_TIMEOUT` | Reverse tunnel WebSocket connection wait in seconds. Default: `60`. |

## Build

From this directory:

```bash
PYTHON=python3 bash build.sh /tmp/openyuanrong-sandbox-dist
```

From the repository root, the build wrapper does the same:

```bash
PYTHON=python3 bash ../build.sh /tmp/openyuanrong-sandbox-dist
```

## Test

Offline transport/unit checks:

```bash
PYTHONPATH=. python3 tests/test_transport_direct.py
```

Live K8S/frontend checks need `YR_SERVER_ADDRESS`, `YR_GATEWAY_ADDRESS`, and a
valid token:

```bash
PYTHONPATH=. python3 tests/e2e_rrt_direct.py
PYTHONPATH=. python3 examples/reverse_tunnel.py
```

## Runnable examples

Only examples expected to run in ordinary SDK/K8S smoke environments are kept:

- `examples/basic_usage.py`
- `examples/command_stdin.py`
- `examples/persistent_shell.py`
- `examples/tunnel_large_response.py`
- `examples/port_forwarding.py`
- `examples/reverse_tunnel.py`
- `examples/named_sandbox.py`
- `examples/bench_cp.py`

Infra-specific demos should be documented separately instead of being shipped as
runnable SDK examples.

## Architecture

- **Control plane** — `POST /api/sandbox/v1/sandboxes`, `DELETE …/{id}`,
  `POST …/{id}/invoke` with the unified `{action, args}` model (`yr_sandbox/_transport.py`).
- **Direct data plane** — frontend/gateway `/direct/{sandbox}/...` routes for
  command invoke and binary file upload/download.
- **Stream data plane** — `/api/sandbox/v1/sandboxes/{id}/stream` WebSocket with
  `YRS1` binary frames (`yr_sandbox/_stream.py`).
- **Reverse tunnel** — gateway `/tunnel/{sandbox}` WebSocket back to a local
  upstream (`yr_sandbox/tunnel_client.py`). Local upstream requests intentionally ignore
  host proxy environment variables.

See [`TODO.md`](TODO.md) for remaining SDK work.
