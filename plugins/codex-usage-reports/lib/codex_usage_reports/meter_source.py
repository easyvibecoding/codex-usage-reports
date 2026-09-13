"""Read-only Codex app-server account and thread usage observations.

The app-server is deliberately treated as an untrusted, short-lived source.  This
module keeps the wire protocol details here so callers receive either bounded
schema-shaped responses or fixed, non-sensitive error codes.  No response is
written to disk and this adapter never performs account mutations.
"""

from __future__ import annotations

import json
import math
import os
import select
import subprocess
import time
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from . import __version__
from .meter_plan import plan_type

# Keep a single frame and the aggregate stream bounded.  The aggregate cap is
# intentionally no larger than one ordinary response; notifications cannot be
# used to make this read grow without bound.
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_PENDING_RESPONSES = 128
MAX_THREAD_ID_INPUTS = 64
READ_CHUNK_BYTES = 64 * 1024
PROCESS_WAIT_SECONDS = 0.5

SOURCE_INITIALIZE = "initialize"
SOURCE_RATE_LIMITS = "rate_limits"
SOURCE_ACCOUNT_USAGE = "account_usage"
SOURCE_THREAD_USAGE = "thread_usage"
SOURCE_ACCOUNT = "account"
SOURCE_TRANSPORT = "transport"

ERR_TIMEOUT = "timeout"
ERR_PROCESS_EXIT = "process_exit"
ERR_EOF = "eof"
ERR_UNSUPPORTED_RPC = "unsupported_rpc"
ERR_RPC = "rpc_error"
ERR_MALFORMED_OUTPUT = "malformed_output"
ERR_OVERSIZED_OUTPUT = "oversized_output"
ERR_MISSING_RESPONSE = "missing_response"
ERR_TRANSPORT = "transport_error"
ERR_SPAWN = "spawn_error"
ERR_NOT_ATTEMPTED = "not_attempted"

# This is intentionally a tuple of fixed strings.  Error details from an
# app-server can contain account information or other secrets and never cross
# this adapter boundary.
SAFE_ERROR_CODES = (
    ERR_TIMEOUT,
    ERR_PROCESS_EXIT,
    ERR_EOF,
    ERR_UNSUPPORTED_RPC,
    ERR_RPC,
    ERR_MALFORMED_OUTPUT,
    ERR_OVERSIZED_OUTPUT,
    ERR_MISSING_RESPONSE,
    ERR_TRANSPORT,
    ERR_SPAWN,
    ERR_NOT_ATTEMPTED,
)
FATAL_ERROR_CODES = frozenset(
    {
        ERR_TIMEOUT,
        ERR_PROCESS_EXIT,
        ERR_EOF,
        ERR_MALFORMED_OUTPUT,
        ERR_OVERSIZED_OUTPUT,
        ERR_TRANSPORT,
    }
)


def _canonical_thread_ids(thread_ids: Iterable[str]) -> list[str]:
    """Validate, canonicalise, and de-duplicate at most eight thread UUIDs."""

    if isinstance(thread_ids, (str, bytes)):
        raise ValueError("thread_ids must be an iterable of UUID strings")
    try:
        values = iter(thread_ids)
    except TypeError as exc:
        raise ValueError("thread_ids must be an iterable of UUID strings") from exc

    result: list[str] = []
    seen: set[str] = set()
    for position, raw in enumerate(values):
        if position >= MAX_THREAD_ID_INPUTS:
            raise ValueError("thread_ids input is too large")
        if isinstance(raw, uuid.UUID):
            canonical = str(raw)
        elif isinstance(raw, str):
            # Accept casing differences but reject alternate UUID spellings
            # (braces, urns, and compact forms) so the output identity is clear.
            try:
                parsed = uuid.UUID(raw)
            except (ValueError, AttributeError, TypeError) as exc:
                raise ValueError("thread_ids must contain canonical UUIDs") from exc
            canonical = str(parsed)
            if raw.lower() != canonical:
                raise ValueError("thread_ids must contain canonical UUIDs")
        else:
            raise ValueError("thread_ids must contain canonical UUIDs")
        if canonical in seen:
            continue
        if len(result) >= 8:
            raise ValueError("thread_ids may contain at most 8 unique UUIDs")
        seen.add(canonical)
        result.append(canonical)
    return result


def _validate_inputs(
    codex_binary: str,
    thread_ids: Iterable[str],
    timeout: float,
    codex_home: Path | None,
) -> tuple[str, list[str], float, Path | None]:
    if not isinstance(codex_binary, str) or not codex_binary:
        raise ValueError("codex_binary must be a non-empty string")
    if isinstance(timeout, bool):
        raise ValueError("timeout must be between 1 and 120 seconds")
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be between 1 and 120 seconds") from exc
    if not math.isfinite(timeout_value) or not 1.0 <= timeout_value <= 120.0:
        raise ValueError("timeout must be between 1 and 120 seconds")
    canonical_ids = _canonical_thread_ids(thread_ids)
    if codex_home is not None:
        try:
            home = Path(codex_home).expanduser()
        except (RuntimeError, TypeError, ValueError) as exc:
            raise ValueError("codex_home must be path-like") from exc
    else:
        home = None
    return codex_binary, canonical_ids, timeout_value, home


def _open_process(codex_binary: str, codex_home: Path | None) -> subprocess.Popen:
    """Spawn the app-server process.

    This small seam is intentionally private so tests can inject a fake stdio
    process without changing the public adapter interface.
    """

    environment = os.environ.copy()
    if codex_home is not None:
        environment["CODEX_HOME"] = os.fspath(codex_home)
    return subprocess.Popen(
        [codex_binary, "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment,
        shell=False,
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(value)


class _StdioTransport:
    """Bounded newline-delimited JSON transport over a binary process pipe."""

    def __init__(self, process: subprocess.Popen) -> None:
        self.process = process
        self._buffer = bytearray()
        self._total_bytes = 0
        self._pending: dict[tuple[type[Any], Any], dict[str, Any]] = {}

    @staticmethod
    def _id_key(request_id: Any) -> tuple[type[Any], Any] | None:
        # JSON-RPC IDs are strings or integers.  Do not hash arbitrary values
        # supplied by a hostile server (and keep bool distinct from int).
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            return None
        return type(request_id), request_id

    def send(self, payload: Mapping[str, Any]) -> None:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        if len(encoded) > MAX_FRAME_BYTES:
            raise _TransportFailure(ERR_OVERSIZED_OUTPUT)
        stream = getattr(self.process, "stdin", None)
        if stream is None:
            raise _TransportFailure(ERR_PROCESS_EXIT)
        try:
            stream.write(encoded)
            stream.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise _TransportFailure(ERR_PROCESS_EXIT) from exc

    def notify(self, method: str) -> None:
        self.send({"method": method})

    def response(
        self, request_id: int, deadline: float
    ) -> tuple[dict[str, Any] | None, str | None]:
        key = self._id_key(request_id)
        if key is None:
            return None, ERR_TRANSPORT
        cached = self._pending.pop(key, None)
        if cached is not None:
            return cached, None

        while True:
            message, event = self._read_message(deadline)
            if event is not None:
                if event == ERR_TIMEOUT and self._pending:
                    return None, ERR_MISSING_RESPONSE
                return None, event
            if message is None:
                return None, ERR_MALFORMED_OUTPUT

            # Server notifications are valid and may occur between any two
            # responses.  A bare object without a method is malformed unless it
            # is a response carrying a request id.
            if "id" not in message or message.get("id") is None:
                if isinstance(message.get("method"), str):
                    continue
                return None, ERR_MALFORMED_OUTPUT

            message_key = self._id_key(message.get("id"))
            if message_key is None:
                return None, ERR_MALFORMED_OUTPUT
            if message_key == key:
                return message, None
            if len(self._pending) >= MAX_PENDING_RESPONSES:
                return None, ERR_OVERSIZED_OUTPUT
            # Keep unmatched responses in memory only.  Their contents are
            # bounded by the frame/aggregate caps above.
            self._pending[message_key] = message

    def _read_message(self, deadline: float) -> tuple[dict[str, Any] | None, str | None]:
        stream = getattr(self.process, "stdout", None)
        if stream is None:
            return None, ERR_PROCESS_EXIT
        try:
            descriptor = stream.fileno()
        except (AttributeError, OSError, ValueError):
            return None, ERR_TRANSPORT

        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                frame = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                return self._decode_frame(frame)
            if len(self._buffer) > MAX_FRAME_BYTES:
                return None, ERR_OVERSIZED_OUTPUT

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, ERR_TIMEOUT
            try:
                ready, _, _ = select.select([descriptor], [], [], remaining)
            except (OSError, ValueError):
                return None, ERR_TRANSPORT
            if not ready:
                if self._process_exited():
                    return None, ERR_PROCESS_EXIT
                return None, ERR_TIMEOUT
            try:
                chunk = os.read(descriptor, READ_CHUNK_BYTES)
            except (OSError, ValueError):
                return None, ERR_TRANSPORT
            if not chunk:
                if self._buffer:
                    # A final line need not carry a newline when a fake or
                    # wrapper server exits after one response.  Parse it once;
                    # incomplete/invalid data is reported as malformed output.
                    frame = bytes(self._buffer)
                    self._buffer.clear()
                    return self._decode_frame(frame)
                return None, ERR_PROCESS_EXIT if self._process_exited() else ERR_EOF
            self._total_bytes += len(chunk)
            if self._total_bytes > MAX_TOTAL_BYTES:
                return None, ERR_OVERSIZED_OUTPUT
            self._buffer.extend(chunk)
            if len(self._buffer) > MAX_FRAME_BYTES and b"\n" not in self._buffer:
                return None, ERR_OVERSIZED_OUTPUT

    def _decode_frame(self, frame: bytes) -> tuple[dict[str, Any] | None, str | None]:
        if len(frame) > MAX_FRAME_BYTES:
            return None, ERR_OVERSIZED_OUTPUT
        try:
            text = frame.decode("utf-8")
            value = json.loads(text, parse_constant=_reject_json_constant)
        except (
            RecursionError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            TypeError,
        ):
            return None, ERR_MALFORMED_OUTPUT
        if not isinstance(value, dict):
            return None, ERR_MALFORMED_OUTPUT
        return value, None

    def _process_exited(self) -> bool:
        try:
            return self.process.poll() is not None
        except (AttributeError, OSError):
            return True


class _TransportFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code if code in SAFE_ERROR_CODES else ERR_TRANSPORT


def _rpc_error_code(reply: Mapping[str, Any]) -> str:
    """Map an error envelope to a fixed code without retaining its details."""

    error = reply.get("error")
    if not isinstance(error, Mapping):
        return ERR_MALFORMED_OUTPUT
    code = error.get("code")
    message = error.get("message")
    if code == -32601 or (
        isinstance(code, str)
        and any(word in code.lower() for word in ("method", "unsupported", "not_found"))
    ) or (
        isinstance(message, str)
        and any(word in message.lower() for word in ("method not found", "unsupported rpc"))
    ):
        return ERR_UNSUPPORTED_RPC
    return ERR_RPC


def _reply_result(
    reply: Mapping[str, Any], source: str
) -> tuple[dict[str, Any] | None, str | None]:
    if "error" in reply:
        if "result" in reply:
            return None, ERR_MALFORMED_OUTPUT
        return None, _rpc_error_code(reply)
    result = reply.get("result")
    if not isinstance(result, dict):
        return None, ERR_MALFORMED_OUTPUT
    # The two live v2 response schemas require these top-level objects.  Keep
    # nested fields opaque so a new app-server release remains readable.
    if source == SOURCE_RATE_LIMITS and not (
        "rateLimits" in result or "rateLimitsByLimitId" in result
    ):
        return None, ERR_MALFORMED_OUTPUT
    if source in (SOURCE_ACCOUNT_USAGE, SOURCE_THREAD_USAGE):
        if not isinstance(result.get("summary"), dict):
            return None, ERR_MALFORMED_OUTPUT
        thread_usage = result.get("threadUsage")
        if thread_usage is not None and not isinstance(thread_usage, dict):
            return None, ERR_MALFORMED_OUTPUT
    if source == SOURCE_ACCOUNT:
        account = result.get("account")
        if account is not None and not isinstance(account, dict):
            return None, ERR_MALFORMED_OUTPUT
    return result, None


_ACCOUNT_TYPES = frozenset({"apiKey", "chatgpt", "amazonBedrock"})


def _safe_account_response(result: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only schema-approved account type/plan fields in memory.

    ``account/read`` includes an email for ChatGPT accounts and may gain other
    identifiers over time.  The meter has no use for either, so the source
    boundary discards every field except the two allowlisted values before the
    response can be observed by a caller.  Unknown values become ``None`` and
    are never echoed into diagnostics or snapshots.
    """

    raw_account = result.get("account")
    if not isinstance(raw_account, Mapping):
        return {"account": None}
    account_kind = raw_account.get("type")
    if not isinstance(account_kind, str) or account_kind not in _ACCOUNT_TYPES:
        return {"account": None}
    account: dict[str, Any] = {"type": account_kind}
    if account_kind == "chatgpt" and "planType" in raw_account:
        account["planType"] = plan_type(raw_account.get("planType"))
    return {"account": account}


def _append_error(result: dict[str, Any], source: str, code: str) -> None:
    safe = code if code in SAFE_ERROR_CODES else ERR_TRANSPORT
    result["errors"].append({"source": source, "code": safe})


def _set_source_failure(result: dict[str, Any], source: str, code: str) -> None:
    _append_error(result, source, code)


def _mark_unattempted_threads(result: dict[str, Any], start: int, code: str) -> None:
    safe = code if code in SAFE_ERROR_CODES else ERR_NOT_ATTEMPTED
    for row in result["thread_usage"][start:]:
        if row["response"] is None and row["error"] is None:
            row["error"] = safe


def _close_process(process: subprocess.Popen | None) -> None:
    """Terminate, bounded-wait, kill if needed, and reap a child process."""

    if process is None:
        return
    stdin = getattr(process, "stdin", None)
    if stdin is not None:
        try:
            stdin.close()
        except (OSError, ValueError):
            pass
    running = False
    try:
        running = process.poll() is None
    except (AttributeError, OSError):
        running = True
    if running:
        try:
            process.terminate()
        except (AttributeError, OSError):
            pass
    try:
        process.wait(timeout=PROCESS_WAIT_SECONDS)
    except (AttributeError, OSError, TypeError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except (AttributeError, OSError):
            pass
        try:
            process.wait(timeout=PROCESS_WAIT_SECONDS)
        except (AttributeError, OSError, TypeError, subprocess.TimeoutExpired):
            pass
    stdout = getattr(process, "stdout", None)
    if stdout is not None:
        try:
            stdout.close()
        except (OSError, ValueError):
            pass


def read_meter_sources(
    *,
    codex_binary: str = "codex",
    thread_ids: Iterable[str] = (),
    timeout: float = 30.0,
    codex_home: Path | None = None,
    include_usage: bool = True,
) -> dict[str, Any]:
    """Read account/rate-limit and account/token-usage snapshots.

    The deadline applies to the complete app-server conversation, including all
    requested threads. ``include_usage=False`` skips account/token history and
    allows only the small rate-limit/account reads used by automatic previews.
    A failed source does not erase earlier successful
    responses; once the stream itself becomes unusable, later thread rows are
    retained with ``not_attempted`` (or the fatal transport code).
    """

    binary, canonical_ids, timeout_value, home = _validate_inputs(
        codex_binary, thread_ids, timeout, codex_home
    )
    if type(include_usage) is not bool or (not include_usage and canonical_ids):
        raise ValueError("usage reads must be enabled when requesting threads")
    started_at = float(time.time())
    deadline = time.monotonic() + timeout_value
    report: dict[str, Any] = {
        "started_at": started_at,
        "finished_at": started_at,
        "rate_limits": None,
        "account_usage": None,
        "account_response": None,
        "thread_usage": [
            {"thread_id": thread_id, "response": None, "error": None}
            for thread_id in canonical_ids
        ],
        "errors": [],
    }
    process: subprocess.Popen | None = None
    transport: _StdioTransport | None = None
    fatal_code: str | None = None

    try:
        try:
            process = _open_process(binary, home)
            transport = _StdioTransport(process)
        except (OSError, ValueError, TypeError):
            _set_source_failure(report, SOURCE_TRANSPORT, ERR_SPAWN)
            _mark_unattempted_threads(report, 0, ERR_NOT_ATTEMPTED)
            return report

        def request(
            request_id: int, method: str, params: Mapping[str, Any], source: str
        ) -> tuple[dict[str, Any] | None, str | None]:
            assert transport is not None
            try:
                transport.send({"id": request_id, "method": method, "params": dict(params)})
                reply, event = transport.response(request_id, deadline)
            except _TransportFailure as exc:
                return None, exc.code
            if event is not None:
                return None, event
            if reply is None:
                return None, ERR_MISSING_RESPONSE
            return _reply_result(reply, source)

        initialize, error = request(
            1,
            "initialize",
            {
                "clientInfo": {"name": "codex-usage-reports", "version": __version__},
                "capabilities": {"experimentalApi": True},
            },
            SOURCE_INITIALIZE,
        )
        if error is not None:
            _set_source_failure(report, SOURCE_INITIALIZE, error)
            # The initialize handshake is required before any account RPC.  A
            # protocol/RPC error here is therefore terminal for this capture,
            # even when the app-server remains alive.
            fatal_code = error
        else:
            assert initialize is not None
            try:
                transport.notify("initialized")
            except _TransportFailure as exc:
                _set_source_failure(report, SOURCE_INITIALIZE, exc.code)
                fatal_code = exc.code

        if fatal_code is None:
            rate_limits, error = request(
                2,
                "account/rateLimits/read",
                {"excludeResetCreditDetails": True},
                SOURCE_RATE_LIMITS,
            )
            if error is None:
                report["rate_limits"] = rate_limits
            else:
                _set_source_failure(report, SOURCE_RATE_LIMITS, error)
                if error in FATAL_ERROR_CODES:
                    fatal_code = error

        if fatal_code is None and include_usage:
            account_usage, error = request(3, "account/usage/read", {}, SOURCE_ACCOUNT_USAGE)
            if error is None:
                report["account_usage"] = account_usage
            else:
                _set_source_failure(report, SOURCE_ACCOUNT_USAGE, error)
                if error in FATAL_ERROR_CODES:
                    fatal_code = error

        for index, thread_id in enumerate(canonical_ids):
            if fatal_code is not None:
                _mark_unattempted_threads(report, index, ERR_NOT_ATTEMPTED)
                break
            response, error = request(
                4 + index,
                "account/usage/read",
                {"threadId": thread_id},
                SOURCE_THREAD_USAGE,
            )
            row = report["thread_usage"][index]
            if error is None:
                row["response"] = response
            else:
                row["error"] = error
                _set_source_failure(report, SOURCE_THREAD_USAGE, error)
                if error in FATAL_ERROR_CODES:
                    fatal_code = error

        # Account identity/plan is a separate optional read.  Keep it after
        # usage calls so an unsupported endpoint cannot hide otherwise valid
        # quota or token observations.  The request is explicitly non-
        # refreshing: sensing must never mutate authentication state.
        if fatal_code is None:
            account_response, error = request(
                4 + len(canonical_ids),
                "account/read",
                {"refreshToken": False},
                SOURCE_ACCOUNT,
            )
            if error is None:
                assert account_response is not None
                report["account_response"] = _safe_account_response(account_response)
            else:
                _set_source_failure(report, SOURCE_ACCOUNT, error)
                if error in FATAL_ERROR_CODES:
                    fatal_code = error

    except (AssertionError, OSError, ValueError, TypeError):
        # Do not let a transport implementation detail escape with raw process
        # or server text.  Validation errors have already been raised before
        # spawning; this branch covers only runtime transport failures.
        _set_source_failure(report, SOURCE_TRANSPORT, ERR_TRANSPORT)
        if fatal_code is None:
            fatal_code = ERR_TRANSPORT
        _mark_unattempted_threads(report, 0, ERR_NOT_ATTEMPTED)
    finally:
        _close_process(process)
        report["finished_at"] = float(time.time())
    return report


__all__ = ["read_meter_sources"]
