"""WebSocket data-plane client for large file transfer.

Protocol verified against frontend pkg/frontend/api/sandbox/stream.go
(``StreamV1Handler`` / ``sandbox.stream.v1``):

    GET /api/sandbox/v1/sandboxes/{sandboxID}/stream

On connect the server sends a text ``{"type":"hello","protocol":...}`` frame.
Text frames are JSON control frames with this schema (note: ``id`` + ``type``,
NOT ``action``/``args``)::

    {"id": "<op-id>", "type": "file.upload.start", "path": "...", "chunkSize": N}

Binary frames carry payload chunks framed as::

    [4B magic "YRS1"][2B big-endian op-id length][op-id bytes][payload bytes]

Upload:   -> {id, type:"file.upload.start", path}
          <- {id, type:"file.upload.ready", streamId}
          -> YRS1 binary chunks (op-id == id) ...
          <- {id, type:"file.upload.ack", offset, bytesWritten}  (per chunk)
          -> {id, type:"file.upload.finish"}
          <- {id, type:"file.upload.done", bytes}
Download: -> {id, type:"file.download.start", path, chunkSize}
          <- YRS1 binary chunks (op-id == id) ...
          <- {id, type:"file.download.done", eof:true}
Errors:   <- {id, type:"error", code, message}

Shell/process interactive streaming is not exposed by the server-side stream
API yet; see TODO.md.
"""

import asyncio
import json
import struct
from typing import Tuple

import websockets

_MAGIC = b"YRS1"
_CHUNK = 64 * 1024  # match server defaultStreamChunk; server caps at 4 MiB


def encode_frame(op_id: str, payload: bytes) -> bytes:
    """[4B magic][2B op-id len][op-id][payload]."""
    oid = op_id.encode("utf-8")
    if len(oid) > 0xFFFF:
        raise ValueError("op_id too long")
    return _MAGIC + struct.pack(">H", len(oid)) + oid + payload


def decode_frame(frame: bytes) -> Tuple[str, bytes]:
    """Inverse of :func:`encode_frame`; raises on bad magic/length."""
    if frame[:4] != _MAGIC:
        raise ValueError(f"bad frame magic: {frame[:4]!r}")
    (n,) = struct.unpack(">H", frame[4:6])
    return frame[6 : 6 + n].decode("utf-8"), frame[6 + n :]


def _ctrl(op_id: str, frame_type: str, **fields) -> str:
    return json.dumps({"id": op_id, "type": frame_type, **fields})


class StreamClient:
    """Async WebSocket client for file upload/download over the data plane."""

    def __init__(self, ws_url: str, token: str, *, verify_tls: bool = False):
        self._url = ws_url
        self._token = token
        self._ssl = None
        if ws_url.startswith("wss://"):
            import ssl

            ctx = ssl.create_default_context()
            if not verify_tls:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            self._ssl = ctx

    def _connect(self):
        # Auth: the frontend reads the raw JWT from the X-Auth header (same as
        # the HTTP control plane), with ?token= as a browser-friendly fallback.
        return websockets.connect(
            self._url,
            additional_headers={"X-Auth": self._token},
            ssl=self._ssl,
            max_size=None,
            subprotocols=["sandbox.stream.v1"],
        )

    async def upload(
        self,
        local_path: str,
        remote_path: str,
        op_id: str = "up",
        stream_type: str = "file",
    ) -> None:
        async with self._connect() as ws:
            await ws.send(
                _ctrl(
                    op_id, "file.upload.start", path=remote_path, streamType=stream_type
                )
            )
            await self._await_type(ws, op_id, "file.upload.ready")

            with open(local_path, "rb") as f:
                while True:
                    chunk = f.read(_CHUNK)
                    if not chunk:
                        break
                    await ws.send(encode_frame(op_id, chunk))
                    # NOTE: do NOT read acks here by cancelling recv() — cancelling
                    # a websockets recv() mid-flight corrupts the read stream. The
                    # interleaved "file.upload.ack" frames are skipped by the final
                    # _await_type loop instead.

            await ws.send(_ctrl(op_id, "file.upload.finish"))
            await self._await_type(ws, op_id, "file.upload.done")

    async def download(
        self,
        remote_path: str,
        local_path: str,
        op_id: str = "down",
        stream_type: str = "file",
    ) -> None:
        async with self._connect() as ws:
            await ws.send(
                _ctrl(
                    op_id,
                    "file.download.start",
                    path=remote_path,
                    chunkSize=_CHUNK,
                    streamType=stream_type,
                )
            )
            with open(local_path, "wb") as f:
                async for message in ws:
                    if isinstance(message, bytes):
                        frame_op, payload = decode_frame(message)
                        if frame_op == op_id:
                            f.write(payload)
                        continue
                    ctrl = json.loads(message)
                    ftype = ctrl.get("type", "")
                    if ftype == "file.download.done":
                        return
                    if ftype == "error":
                        raise RuntimeError(self._err(ctrl))
                    # ignore hello / other control frames

    # ── control-frame helpers ──────────────────────────────────────────

    async def _await_type(self, ws, op_id: str, want: str) -> dict:
        """Wait for a control frame of ``type == want`` (skipping hello/acks)."""
        async for message in ws:
            if isinstance(message, bytes):
                continue
            ctrl = json.loads(message)
            ftype = ctrl.get("type", "")
            if ftype == want:
                return ctrl
            if ftype == "error":
                raise RuntimeError(self._err(ctrl))
            # hello / file.upload.ack / pong → keep waiting
        raise RuntimeError(f"stream closed before {want!r}")

    @staticmethod
    def _err(ctrl: dict) -> str:
        return f"stream error {ctrl.get('code', '')}: {ctrl.get('message', ctrl)}"


def run_upload(
    ws_url, token, local_path, remote_path, *, verify_tls=False, stream_type="file"
) -> None:
    """Blocking wrapper used by the sync Filesystem API."""
    client = StreamClient(ws_url, token, verify_tls=verify_tls)
    asyncio.run(client.upload(local_path, remote_path, stream_type=stream_type))


def run_download(
    ws_url, token, remote_path, local_path, *, verify_tls=False, stream_type="file"
) -> None:
    """Blocking wrapper used by the sync Filesystem API."""
    client = StreamClient(ws_url, token, verify_tls=verify_tls)
    asyncio.run(client.download(remote_path, local_path, stream_type=stream_type))
