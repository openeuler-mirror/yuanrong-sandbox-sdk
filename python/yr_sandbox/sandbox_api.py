"""Sandbox API for openYuanrong, backed by frontend sandbox v1 and RRT.

Sandbox lifecycle is server-side and reached through the frontend HTTP control
plane. Commands, filesystem operations, shell sessions, direct file transfer,
and reverse tunnel helpers are exposed as Python objects on ``Sandbox``.
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

from ._transport import SandboxClient
from .commands import Commands
from .filesystem import Filesystem
from .shell import Shells
from .types import Mount, PortForwarding, SandboxInfo

logger = logging.getLogger(__name__)

TUNNEL_HTTP_PROXY_URL = "http://127.0.0.1:8766"
DEFAULT_CREATE_TIMEOUT = 60
SCHEDULE_TIMEOUT_BUFFER = 30


def _get_create_timeout(timeout: Optional[int]) -> int:
    if timeout is not None:
        value = timeout
    else:
        raw = os.environ.get(
            "YR_SANDBOX_CREATE_TIMEOUT", str(DEFAULT_CREATE_TIMEOUT)
        ).strip()
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(
                "YR_SANDBOX_CREATE_TIMEOUT must be an integer number of seconds"
            ) from exc
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("create_timeout must be a positive integer")
    return value


def _resolve_create_timeouts(
    create_timeout: Optional[int], schedule_timeout: Optional[int]
) -> tuple[int, int]:
    if schedule_timeout is not None and (
        isinstance(schedule_timeout, bool)
        or not isinstance(schedule_timeout, int)
        or schedule_timeout <= 0
    ):
        raise ValueError("schedule_timeout must be a positive integer")

    if create_timeout is None and schedule_timeout is not None:
        return schedule_timeout + SCHEDULE_TIMEOUT_BUFFER, schedule_timeout

    resolved_create = _get_create_timeout(create_timeout)
    if schedule_timeout is None:
        if resolved_create <= SCHEDULE_TIMEOUT_BUFFER:
            raise ValueError(
                f"create_timeout must be greater than {SCHEDULE_TIMEOUT_BUFFER}"
            )
        return resolved_create, resolved_create - SCHEDULE_TIMEOUT_BUFFER

    if schedule_timeout > resolved_create:
        raise ValueError(
            "schedule_timeout must be less than or equal to create_timeout"
        )
    if resolved_create - schedule_timeout < SCHEDULE_TIMEOUT_BUFFER:
        raise ValueError(
            "create_timeout - schedule_timeout must be at least "
            f"{SCHEDULE_TIMEOUT_BUFFER}"
        )
    return resolved_create, schedule_timeout


def _get_tunnel_connect_timeout(timeout: Optional[float]) -> float:
    if timeout is not None:
        value = float(timeout)
    else:
        raw = os.environ.get("YR_TUNNEL_CONNECT_TIMEOUT", "60")
        try:
            value = float(raw)
        except ValueError as e:
            raise ValueError(
                "YR_TUNNEL_CONNECT_TIMEOUT must be a number of seconds"
            ) from e
    if value <= 0:
        raise ValueError("tunnel_connect_timeout must be greater than 0")
    return value


def _compose_gateway_url(*, gateway: str, scheme: str, path: str) -> str:
    """Compose a gateway URL from a frontend-returned path or URL.

    Frontend normally returns a path-only tunnel URL so deployments can choose
    the external gateway address locally. If the frontend returns a full URL,
    keep only its path; the SDK still owns the public gateway host and
    ws/wss scheme selection via YR_GATEWAY_ADDRESS/YR_GATEWAY_TLS.
    """
    if not gateway:
        raise ValueError("YR_GATEWAY_ADDRESS or YR_SERVER_ADDRESS must be set")
    parsed = urlparse(path)
    route = parsed.path or path
    if parsed.query:
        route = f"{route}?{parsed.query}"
    if not route.startswith("/"):
        route = f"/{route}"
    return f"{scheme}://{gateway}{route}"


class Sandbox:
    """High-level sandbox API for openYuanrong sandboxes.

    Usage::

        with Sandbox(image="python:3.12-slim", cpu=2000, memory=4096) as sb:
            sb.files.write("/tmp/hello.txt", "hello world")
            result = sb.commands.run("cat /tmp/hello.txt")
            print(result.stdout)

            sh = await sb.shells.create(cwd="/tmp")
            await sh.run("export FOO=bar")
            result = await sh.run("echo $FOO")  # → bar
    """

    def __init__(
        self,
        image: Optional[str] = None,
        cpu: int = 1000,
        memory: int = 4096,
        runtime: Optional[str] = None,
        cpu_limit: int = 0,
        mem_limit: int = 0,
        idle_timeout: int = 300,
        create_timeout: Optional[int] = None,
        schedule_timeout: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
        name: Optional[str] = None,
        cwd: Optional[str] = None,
        port_forwardings: Optional[List[Union[int, PortForwarding]]] = None,
        mounts: Optional[List[Mount]] = None,
        upstream: Optional[str] = None,
        proxy_port: int = 8766,
        tunnel_connect_timeout: Optional[float] = None,
        detached: bool = False,
        extra_config: Optional[Dict[str, Any]] = None,
    ):
        """Create a new sandbox.

        Args:
            image: Container image to use (e.g. ``"python:3.12-slim"``).
            cpu: CPU scheduling request in milli-cores (default 1000).
            memory: Memory scheduling request in MB (default 4096).
            runtime: Optional sandbox runtime selector forwarded to the frontend
                (for example ``"python3.9"``). If omitted, the frontend default
                runtime is used.
            cpu_limit: CPU cgroup limit in milli-cores (0 = same as *cpu*).
            mem_limit: Memory cgroup limit in MB (0 = same as *memory*).
            idle_timeout: Seconds before idle sandbox is reclaimed (default 300).
            create_timeout: Logical create budget in seconds. Defaults to
                ``YR_SANDBOX_CREATE_TIMEOUT`` or 60 seconds.
            schedule_timeout: Scheduling budget in seconds. Configure either
                this or ``create_timeout``; the other budget is derived with a
                30-second startup buffer. If both are set, their difference
                must be at least 30 seconds.
            env: Environment variables to set in the sandbox.
            name: Logical name for the sandbox instance.
            cwd: Working directory inside the sandbox.
            port_forwardings: Ports to forward from the sandbox. Each entry is
                a port number (defaults to TCP) or a ``PortForwarding`` object.
            mounts: Custom mount specifications for the sandbox.
            upstream: ``host:port`` of a local service to expose inside the
                sandbox via a reverse tunnel. ``host:port`` must be reachable
                from this machine. Requires ``websockets`` and ``httpx``.
            proxy_port: Reserved for API stability. Reverse-tunnel ports are owned by
                the frontend; SDK callers should use ``get_tunnel_url()``.
            tunnel_connect_timeout: Seconds to wait for the reverse tunnel
                WebSocket connection. Defaults to ``YR_TUNNEL_CONNECT_TIMEOUT``
                or 60s. Set to a lower value for standalone dev clusters.
            detached: If True, ``kill()`` / context-manager exit skips teardown.
            extra_config: Extra sandbox-side configuration forwarded to sandboxd.
        """
        # ── port_forwardings ──────────────────────────────────────────────
        self._forwarded_ports: set = set()
        pf_ports: List[str] = []
        if port_forwardings:
            for pf in port_forwardings:
                if isinstance(pf, int):
                    pf_ports.append(str(pf))
                    self._forwarded_ports.add(pf)
                else:
                    pf_ports.append(str(pf.port))
                    self._forwarded_ports.add(pf.port)

        # ── reverse tunnel ────────────────────────────────────────────────
        self._tunnel_client = None
        self._tunnel_url = TUNNEL_HTTP_PROXY_URL

        # ── build create body ─────────────────────────────────────────────
        resolved_create_timeout, resolved_schedule_timeout = _resolve_create_timeouts(
            create_timeout, schedule_timeout
        )
        body: Dict[str, Any] = {
            "namespace": "default",
            "idleTimeoutSeconds": idle_timeout,
            "createTimeoutSeconds": resolved_create_timeout,
            "scheduleTimeoutSeconds": resolved_schedule_timeout,
        }
        if runtime:
            body["runtime"] = runtime
        if image:
            body["image"] = image
            body["rootfs"] = {
                "runtime": "runsc",
                "type": "image",
                "readonly": False,
                "imageurl": image,
            }
        if name:
            body["name"] = name
        body["cpu"] = cpu
        body["memory"] = memory
        body["cpu_limit"] = cpu_limit
        body["mem_limit"] = mem_limit
        if env:
            body["env"] = dict(env)
        if cwd:
            body["cwd"] = cwd
        if mounts:
            body["mounts"] = [m.to_dict() for m in mounts]
        if extra_config:
            body["extra_config"] = extra_config
        if detached:
            body["lifecycle"] = "detached"
        if upstream is not None:
            # Declarative tunnel request. Frontend owns the internal control
            # port, forwarded ports, and RRT_TUNNEL_* env injection, then
            # returns a stable /tunnel/{safeID} URL path.
            body["tunnel"] = {"enabled": True}

        self._detached = detached
        self._image = image
        self._cpu = cpu
        self._memory = memory

        # ── ports: user port_forwardings only ─────────────────────────────
        # Frontend owns RRT_HTTP_PORT=50090 and its sandbox network mapping for
        # /direct. SDK callers should not expose that internal control port.
        self._client = SandboxClient()
        all_ports = list(pf_ports)
        if all_ports:
            # Deduplicate while preserving order
            seen: set = set()
            deduped: List[str] = []
            for p in all_ports:
                if p not in seen:
                    seen.add(p)
                    deduped.append(p)
            body["ports"] = deduped

        create_info = self._create(body)
        self._sid = create_info.get("sandboxId") or create_info.get("instanceId")
        if not self._sid:
            raise RuntimeError(f"create response missing sandbox id: {create_info}")

        # ── reverse tunnel: connect after sandbox is running ──────────────
        if upstream is not None:
            # Build the tunnel WebSocket URL via the sandbox gateway.
            # In K8s this is the Traefik/sandbox gateway; in standalone it is
            # YR_GATEWAY_ADDRESS, else the server address.  The gateway /tunnel
            # route owns the internal tunnel control-port mapping, so clients do
            # not expose 8765 in the URL.
            gateway = os.environ.get("YR_GATEWAY_ADDRESS", "").strip()
            if not gateway:
                gateway = os.environ.get("YR_SERVER_ADDRESS", "").strip()

            tunnel_info = create_info.get("tunnel") or {}
            if not isinstance(tunnel_info, dict):
                tunnel_info = {}
            self._tunnel_url = tunnel_info.get("proxyUrl") or TUNNEL_HTTP_PROXY_URL
            tunnel_url = tunnel_info.get("url") or tunnel_info.get("path")
            safe_id = self._client._safe_id(self._sid)
            tls = os.environ.get("YR_GATEWAY_TLS", "0").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            ws_scheme = "wss" if tls else "ws"
            tunnel_ws_url = _compose_gateway_url(
                gateway=gateway,
                scheme=ws_scheme,
                path=tunnel_url or f"/tunnel/{safe_id}",
            )
            connect_timeout = _get_tunnel_connect_timeout(tunnel_connect_timeout)

            from .tunnel_client import TunnelClient

            # Only carry the platform JWT over a TLS tunnel (wss). Sending it on a
            # plaintext ws:// hop would leak the token; plaintext mode is intended
            # for auth-disabled local/dev frontends.
            tunnel_token = self._client.token if tls else None
            self._tunnel_client = TunnelClient(upstream, token=tunnel_token)
            logger.info(
                "Starting TunnelClient: sandbox_id=%s name=%s url=%s upstream=%s timeout=%.1fs",
                safe_id,
                name or "",
                tunnel_ws_url,
                upstream,
                connect_timeout,
            )
            if self._tunnel_client.start(tunnel_ws_url, timeout=connect_timeout):
                logger.info(
                    "TunnelClient connected: sandbox_id=%s name=%s",
                    safe_id,
                    name or "",
                )
            else:
                self._tunnel_client.stop()
                self._tunnel_client = None
                raise RuntimeError(
                    "TunnelClient connection timeout after "
                    f"{connect_timeout:.1f}s: sandbox_id={safe_id} "
                    f"name={name or ''} url={tunnel_ws_url}. "
                    "The tunnel route may be missing or not ready."
                )

        self._files = Filesystem(self._client, self._sid)
        self._commands = Commands(self._client, self._sid)
        self._shells = Shells(self._client, self._sid)

    # ── sub-resources ──────────────────────────────────────────────────

    @property
    def files(self):
        return self._files

    @property
    def commands(self):
        return self._commands

    @property
    def shells(self):
        return self._shells

    @property
    def id(self) -> str:
        """Sandbox id assigned by the frontend (consistent with the frontend response)."""
        return self._sid

    @property
    def sandbox_id(self) -> str:
        return self._sid

    # ── port forwarding ─────────────────────────────────────────────────

    def get_port_url(self, port: int) -> str:
        """Return the external URL to reach a forwarded port.

        URL format: ``http://{gateway}/{sandbox_id}/{port}``.
        """
        if port not in self._forwarded_ports:
            raise ValueError(
                f"Port {port} is not in forwarded ports: {self._forwarded_ports}"
            )
        gateway = os.environ.get("YR_GATEWAY_ADDRESS", "").strip()
        if not gateway:
            gateway = os.environ.get("YR_SERVER_ADDRESS", "").strip()
        if not gateway:
            raise ValueError("YR_GATEWAY_ADDRESS or YR_SERVER_ADDRESS must be set")
        safe_id = self._client._safe_id(self._sid)
        return f"http://{gateway}/{safe_id}/{port}"

    # ── reverse tunnel ──────────────────────────────────────────────────

    def get_tunnel_url(self) -> str:
        """Return the internal HTTP proxy URL for sandbox code.

        Returns:
            str: e.g. "http://127.0.0.1:8766"
        Raises:
            RuntimeError: if no upstream was configured.
        """
        if self._tunnel_client is None:
            raise RuntimeError("No upstream configured. Pass upstream= to Sandbox().")
        return self._tunnel_url

    def _create(self, body: Dict[str, Any]) -> Dict[str, Any]:
        create_info = getattr(self._client, "create_info", None)
        if callable(create_info):
            return create_info(body)
        sid = self._client.create(body)
        return getattr(self._client, "last_create", None) or {"sandboxId": sid}

    # ── lifecycle ──────────────────────────────────────────────────────

    def is_running(self) -> bool:
        try:
            self._client.invoke(self._sid, "file.exists", {"path": "/"}, timeout=10)
            return True
        except Exception:
            return False

    def get_info(self) -> SandboxInfo:
        state = "running" if self.is_running() else "stopped"
        return SandboxInfo(
            sandbox_id=self._sid,
            state=state,
            cpu=self._cpu,
            memory=self._memory,
            image=self._image,
        )

    def kill(self) -> None:
        if self._tunnel_client is not None:
            self._tunnel_client.stop()
            self._tunnel_client = None
        try:
            self._shells.close()
        except Exception as e:
            logger.debug("shell cleanup during kill failed: %s", e)
        if not self._detached:
            try:
                self._client.delete(self._sid)
            finally:
                self._client.close()

    @classmethod
    def delete(cls, name: str, namespace: str = "default") -> None:
        client = SandboxClient()
        try:
            client.delete(name)
        finally:
            client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.kill()

    def __del__(self):
        try:
            self.kill()
        except Exception:
            pass
