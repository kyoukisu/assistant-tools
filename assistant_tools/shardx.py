from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from typing import cast
from urllib.parse import quote

import httpx

from assistant_tools.models import CommandResult
from assistant_tools.utils import AssistantToolsError

DEFAULT_URL = "http://127.0.0.1:9233"
DEFAULT_TIMEOUT_SECONDS = 300.0


class ShardxClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            trust_env=False,
        )

    @classmethod
    def from_env(cls) -> ShardxClient:
        raw_timeout = os.environ.get("SHARDX_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError as err:
            raise AssistantToolsError(
                f"Invalid SHARDX_TIMEOUT: {raw_timeout}",
                error_type="invalid_config",
                exit_code=2,
            ) from err
        return cls(os.environ.get("SHARDX_URL", DEFAULT_URL), timeout_seconds)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._request(method, path, json=json)
        try:
            payload: Any = response.json()
        except ValueError as err:
            raise AssistantToolsError(
                "ShardX returned invalid JSON",
                error_type="invalid_response",
                exit_code=1,
            ) from err
        if not isinstance(payload, dict):
            raise AssistantToolsError(
                "ShardX returned a non-object JSON response",
                error_type="invalid_response",
                exit_code=1,
            )
        raw_payload = cast(dict[object, Any], payload)
        return {str(key): value for key, value in raw_payload.items()}

    def request_text(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> str:
        return self._request(method, path, json=json).text

    def download(self, path: str, output: Path) -> tuple[Path, str]:
        response = self._request("GET", path)
        output = output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(response.content)
        return output, response.headers.get("content-type", "application/octet-stream")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            response = self.client.request(method, f"{self.base_url}{path}", json=json)
        except httpx.HTTPError as err:
            raise AssistantToolsError(
                f"ShardX unavailable at {self.base_url}: {err}",
                error_type="shardx_unavailable",
                exit_code=1,
            ) from err
        if response.is_success:
            return response
        message = response.text.strip() or response.reason_phrase
        error_type = "shardx_http_error"
        try:
            payload: Any = response.json()
            if isinstance(payload, dict):
                raw_payload = cast(dict[object, Any], payload)
                typed_payload: dict[str, Any] = {
                    str(key): value for key, value in raw_payload.items()
                }
                raw_error: Any = typed_payload.get("error")
                if isinstance(raw_error, dict):
                    raw_error_payload = cast(dict[object, Any], raw_error)
                    typed_error: dict[str, Any] = {
                        str(key): value for key, value in raw_error_payload.items()
                    }
                    message = str(typed_error.get("message", message))
                    error_type = str(
                        typed_error.get("code", typed_error.get("type", error_type))
                    )
                elif typed_payload.get("message") or typed_payload.get("error"):
                    message = str(typed_payload.get("message", typed_payload.get("error")))
                    error_type = str(typed_payload.get("code", error_type))
        except ValueError:
            pass
        raise AssistantToolsError(
            f"ShardX HTTP {response.status_code}: {message}",
            error_type=error_type,
            exit_code=1,
        )


def _part(value: str) -> str:
    return quote(value, safe="")


def _result(command: str, data: dict[str, Any], base_url: str) -> CommandResult:
    return CommandResult(
        ok=True,
        command=f"shardx.{command}",
        provider="shardx",
        data=data,
        error=None,
        meta={"base_url": base_url},
    )


def _ensure_session(client: ShardxClient, args: Any, session: str) -> dict[str, Any]:
    listed = client.request_json("GET", "/sessions")
    sessions: Any = listed.get("sessions")
    if isinstance(sessions, list):
        for raw_item in cast(list[Any], sessions):
            if not isinstance(raw_item, dict):
                continue
            raw_item_payload = cast(dict[object, Any], raw_item)
            item: dict[str, Any] = {
                str(key): value for key, value in raw_item_payload.items()
            }
            if item.get("session_id") == session:
                if args.proxy and item.get("proxy") != args.proxy:
                    raise AssistantToolsError(
                        f"Session {session} already uses proxy {item.get('proxy') or 'unknown'}",
                        error_type="session_config_conflict",
                        exit_code=1,
                    )
                return item
    body: dict[str, Any] = {
        "session_id": session,
        "live": bool(args.live),
    }
    if args.proxy:
        body["proxy"] = args.proxy
    if args.fingerprint:
        body["fingerprint"] = args.fingerprint
    return client.request_json("POST", "/sessions", json=body)


def run(args: Any) -> CommandResult:
    client = ShardxClient.from_env()
    try:
        command = str(args.shardx_command)
        if command == "health":
            return _result(command, client.request_json("GET", "/health"), client.base_url)
        if command == "sessions":
            return _result(command, client.request_json("GET", "/sessions"), client.base_url)
        if command == "identities":
            return _result(command, client.request_json("GET", "/identities"), client.base_url)
        if command == "open":
            if args.identity and args.session:
                raise AssistantToolsError(
                    "Pass either --identity or --session, not both",
                    error_type="invalid_request",
                    exit_code=2,
                )
            if args.identity:
                if args.proxy or args.fingerprint:
                    raise AssistantToolsError(
                        "--proxy and --fingerprint are only valid for disposable sessions",
                        error_type="invalid_request",
                        exit_code=2,
                    )
                session = str(args.identity)
                opened = client.request_json(
                    "POST",
                    f"/identities/{_part(session)}/open",
                    json={"url": args.url, "live": bool(args.live)},
                )
                session_info: dict[str, Any] | None = None
            else:
                session = str(args.session or f"kit-{os.getpid()}")
                session_info = _ensure_session(client, args, session)
                opened = client.request_json(
                    "POST", f"/sessions/{_part(session)}/open", json={"url": args.url}
                )
            observation = client.request_json("GET", f"/sessions/{_part(session)}/observe")
            return _result(
                command,
                {
                    "session": session,
                    "opened": opened,
                    "observation": observation,
                    "session_info": session_info,
                },
                client.base_url,
            )
        if command == "identity":
            sub = str(args.identity_command)
            name = _part(str(args.name))
            if sub == "create":
                body: dict[str, Any] = {"name": str(args.name)}
                if args.proxy:
                    body["proxy"] = args.proxy
                if args.fingerprint:
                    body["fingerprint"] = args.fingerprint
                data = client.request_json("POST", "/identities", json=body)
            elif sub == "open":
                data = client.request_json(
                    "POST",
                    f"/identities/{name}/open",
                    json={"url": str(args.url), "live": bool(args.live)},
                )
            elif sub == "open-last":
                data = client.request_json("POST", f"/identities/{name}/open-last", json={})
            elif sub == "status":
                data = client.request_json("GET", f"/identities/{name}")
            elif sub == "capture-cookies":
                data = client.request_json("POST", f"/identities/{name}/capture-cookies", json={})
            else:
                raise AssistantToolsError(
                    f"Unknown ShardX identity command: {sub}",
                    error_type="unknown_command",
                    exit_code=2,
                )
            return _result(f"identity.{sub}", data, client.base_url)
        session = _part(str(args.session))
        if command == "observe":
            data = client.request_json("GET", f"/sessions/{session}/observe")
        elif command == "read":
            text = client.request_text(
                "POST",
                f"/sessions/{session}/read",
                json={
                    "full_page": bool(args.full_page),
                    "max_chars": args.max_chars,
                    "max_blocks": args.max_blocks,
                    "region": args.region,
                    "include_interactive": False,
                },
            )
            data = {"session": args.session, "text": text}
        elif command == "act":
            data = client.request_json(
                "POST",
                f"/sessions/{session}/act",
                json={
                    "snapshot": args.snapshot,
                    "ref": args.ref,
                    "action": args.action,
                    "text": args.text,
                    "clear": not args.append,
                },
            )
        elif command == "secret-fill":
            env_var = str(args.env_var)
            if not env_var or not env_var.replace("_", "").isalnum() or not env_var[0].isalpha():
                raise AssistantToolsError(
                    "--env-var must be an environment variable name",
                    error_type="invalid_request",
                    exit_code=2,
                )
            secret = os.environ.get(env_var)
            if not secret:
                raise AssistantToolsError(
                    f"Secret environment variable is missing or empty: {env_var}",
                    error_type="missing_secret",
                    exit_code=2,
                )
            client.request_json(
                "POST",
                f"/sessions/{session}/act",
                json={
                    "snapshot": args.snapshot,
                    "ref": args.ref,
                    "action": "fill",
                    "text": secret,
                    "clear": True,
                },
            )
            data = {"session": args.session, "ref": args.ref, "filled": True}
        elif command == "page":
            body: dict[str, Any] = {}
            if args.action == "scroll":
                body = {"down": args.direction != "up", "pages": args.pages}
            elif args.action == "press":
                if not args.key:
                    raise AssistantToolsError(
                        "--key is required for page press",
                        error_type="invalid_request",
                        exit_code=2,
                    )
                body = {"key": args.key}
            elif args.action == "wait":
                body = {"seconds": args.seconds}
            action_result = client.request_json(
                "POST", f"/sessions/{session}/{args.action}", json=body
            )
            observation = client.request_json("GET", f"/sessions/{session}/observe")
            data = {"action": args.action, "result": action_result, "observation": observation}
        elif command == "tabs":
            if args.select:
                selected = client.request_json(
                    "POST",
                    f"/sessions/{session}/tabs/select",
                    json={"ref": args.select},
                )
                observation = client.request_json("GET", f"/sessions/{session}/observe")
                data = {"selected": selected, "observation": observation}
            else:
                data = client.request_json("GET", f"/sessions/{session}/tabs")
        elif command == "close-tab":
            data = client.request_json(
                "POST", f"/sessions/{session}/tabs/close", json={"ref": args.tab}
            )
        elif command == "stop":
            data = client.request_json("POST", f"/sessions/{session}/stop", json={})
        elif command == "screenshot":
            output, mime_type = client.download(
                f"/sessions/{session}/screenshot?format=png", args.output
            )
            data = {
                "session": args.session,
                "path": str(output),
                "mime_type": mime_type,
                "bytes": output.stat().st_size,
            }
        else:
            raise AssistantToolsError(
                f"Unknown ShardX command: {command}",
                error_type="unknown_command",
                exit_code=2,
            )
        return _result(command, data, client.base_url)
    finally:
        client.close()
