"""Owner-only, loopback-only credential save/validation; never enables a model.

A submitted key lives only in this request unless the human explicitly confirms
the standalone save or validation's ``save_for_later`` option. The private
environment file is never automatically loaded by the runtime or either launcher.
Only the explicitly confirmed saved-key validation POST reads a stored credential.
No endpoint returns a credential. No provider reply is forwarded.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import secrets
import stat
import threading
import time
from collections import deque
from contextvars import ContextVar
from functools import wraps
from html import escape
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute

from .api import OwnerIdentity
from .config import ROOT, settings, local_database

MODEL = "gpt-6-luna"
PROVIDER_URL = "https://api.openai.com/v1/responses"
ADMIN_OWNER_ID = "owner-alex"
MAX_BODY_BYTES = 4096
MAX_PROVIDER_BYTES = 65536
MAX_SAVED_BYTES = len(b"OPENAI_API_KEY=") + 512 + 1
PROVIDER_TIMEOUT_SECONDS = 90.0
PROVIDER_CONNECT_TIMEOUT_SECONDS = 10.0
SECRET_DIRECTORY = ROOT / "data" / "provider-secrets"
SECRET_FILENAME = "openai.env"
SECRET_PATH = SECRET_DIRECTORY / SECRET_FILENAME
NO_STORE = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
_SECRET_REQUEST = ContextVar("twinteams_secret_request", default=False)


class _SuppressCredentialTransportLogs(logging.Filter):
    def filter(self, record):
        return not _SECRET_REQUEST.get()


class _SuppressConfigAccessLogs(logging.Filter):
    def filter(self, record):
        # uvicorn logs after this request's context has ended. Suppress this
        # route entirely, including a mistaken credential-bearing query string.
        args = record.args
        return not (
            isinstance(args, tuple)
            and len(args) >= 3
            and isinstance(args[2], str)
            and args[2].startswith("/api/local-provider-config")
        )


logging.getLogger("uvicorn.access").addFilter(_SuppressConfigAccessLogs())


# httpcore emits through its concrete protocol loggers, rather than its parent.
# Suppress both ordinary HTTPX request logs and wire-debug logs for this request.
for _name in (
    "httpx",
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
    "httpcore.http_proxy",
    "httpcore.socks_proxy",
):
    logging.getLogger(_name).addFilter(_SuppressCredentialTransportLogs())


def _result(status, code, message, *, validated=False, saved=False):
    return JSONResponse(
        status_code=status,
        headers=NO_STORE,
        content={
            "validated": validated,
            "model": MODEL,
            "error_code": code,
            "message": message,
            "saved": saved,
            "enabled": False,
        },
    )


def _local_available():
    # A remote Runtime database must not inherit this local-only UI.
    runtime_database = os.getenv("AGENT_DB_URL", settings.database_url)
    return (
        os.getenv("TWINTEAMS_LOCAL_PROVIDER_CONFIG", "").strip().lower() == "true"
        and settings.local_identity_enabled
        and settings.tenant_id != "acceptance-fixture"
        and os.getenv("AGENT_API", "") != "twinteams-browser-acceptance-offline"
        and local_database(runtime_database)
    )


def _header_values(request, name):
    return request.headers.getlist(name)


def _request_guard(request):
    if not _local_available():
        return _result(
            403, "local_only", "Provider validation is disabled outside local development."
        )
    if not request.client:
        return _result(403, "local_only", "Provider validation requires a loopback connection.")
    try:
        peer_is_loopback = ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        peer_is_loopback = False
    if not peer_is_loopback:
        return _result(403, "local_only", "Provider validation requires a loopback connection.")
    # Never trust proxy metadata, even when uvicorn's proxy middleware is active.
    if any(
        name.lower() == b"forwarded"
        or name.lower().startswith(b"x-forwarded-")
        or name.lower() in {b"x-real-ip", b"x-original-host", b"x-original-url"}
        for name, _ in request.scope["headers"]
    ):
        return _result(
            403, "proxy_not_allowed", "Provider validation does not accept proxy headers."
        )
    hosts = _header_values(request, "host")
    if len(hosts) != 1 or not re.fullmatch(
        r"(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?",
        hosts[0],
        re.ASCII,
    ):
        return _result(403, "invalid_origin", "Use this application's loopback address.")
    try:
        host_port = urlsplit("http://" + hosts[0]).port
    except ValueError:
        return _result(403, "invalid_origin", "Use this application's loopback address.")
    if host_port is not None and not 1 <= host_port <= 65535:
        return _result(403, "invalid_origin", "Use this application's loopback address.")
    if request.method == "POST":
        origins = _header_values(request, "origin")
        scheme = request.scope.get("scheme")
        if scheme not in {"http", "https"} or origins != [f"{scheme}://{hosts[0]}"]:
            return _result(
                403,
                "invalid_origin",
                "Validation must be submitted from this application's same-origin page.",
            )
        fetch_sites = _header_values(request, "sec-fetch-site")
        if fetch_sites and fetch_sites not in (["same-origin"], ["none"]):
            return _result(403, "invalid_origin", "Cross-site validation requests are refused.")
        if _header_values(request, "x-twinteams-config") != ["1"]:
            return _result(
                403,
                "missing_config_guard",
                "Submit validation from the provider configuration page.",
            )
    return None


class _SensitiveRoute(APIRoute):
    """Contain auth, parsing and transport failures before global exception logs."""

    def get_route_handler(self):
        original = super().get_route_handler()

        @wraps(original)
        async def guarded(request):
            token = _SECRET_REQUEST.set(True)
            try:
                failure = _request_guard(request)
                if failure is not None:
                    return failure
                return await original(request)
            except HTTPException as exc:
                if exc.status_code == 401:
                    return _result(
                        401,
                        "authentication_required",
                        "Sign in with the local AAA administrator account.",
                    )
                return _result(
                    403,
                    "admin_required",
                    "Only the local AAA administrator may use provider configuration.",
                )
            except Exception:  # noqa: BLE001 - sensitive boundary must contain every failure
                # Do not stringify/log exceptions: request/transport exceptions may
                # retain a credential-bearing request or an echoed provider body.
                return _result(
                    500,
                    "validation_unavailable",
                    "Validation could not complete. No model was enabled.",
                )
            finally:
                _SECRET_REQUEST.reset(token)

        return guarded


router = APIRouter(prefix="/api/local-provider-config", route_class=_SensitiveRoute)


def _require_admin(owner):
    if owner != ADMIN_OWNER_ID:
        raise HTTPException(403, "Local administrator required")


class _UnsafeCredentialStore(Exception):
    """Never carries path contents or credential data."""


def _directory_flags():
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _check_secret_file(directory_fd):
    try:
        info = os.stat(SECRET_FILENAME, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise _UnsafeCredentialStore()
    return True


def _secret_directory_fd(*, create=False):
    # Open every mutable path component relative to an already-open directory.
    # O_NOFOLLOW refuses links; a replacement cannot redirect a later operation.
    root_fd = os.open(ROOT, _directory_flags())
    data_fd = None
    try:
        data_fd = os.open("data", _directory_flags(), dir_fd=root_fd)
        if create:
            try:
                os.mkdir("provider-secrets", 0o700, dir_fd=data_fd)
            except FileExistsError:
                pass
        directory_fd = os.open("provider-secrets", _directory_flags(), dir_fd=data_fd)
        info = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            os.close(directory_fd)
            raise _UnsafeCredentialStore()
        return directory_fd
    finally:
        if data_fd is not None:
            os.close(data_fd)
        os.close(root_fd)


def _saved_status():
    directory_fd = None
    try:
        directory_fd = _secret_directory_fd()
        return _check_secret_file(directory_fd), True
    except FileNotFoundError:
        return False, True
    except (OSError, _UnsafeCredentialStore):
        return False, False
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def _read_saved_key():
    """Request-only read; never called by startup, status, or page rendering."""
    directory_fd = _secret_directory_fd()
    file_fd = None
    raw = bytearray()
    chunk = b""
    match = None
    try:
        file_fd = os.open(
            SECRET_FILENAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        info = os.fstat(file_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or not 1 <= info.st_size <= MAX_SAVED_BYTES
        ):
            raise _UnsafeCredentialStore()
        while True:
            chunk = os.read(file_fd, MAX_SAVED_BYTES + 1 - len(raw))
            if not chunk:
                break
            raw.extend(chunk)
            chunk = b""
            if len(raw) > MAX_SAVED_BYTES:
                raise _UnsafeCredentialStore()
        after = os.fstat(file_fd)
        if (
            after.st_uid != info.st_uid or after.st_mode != info.st_mode
            or after.st_nlink != 1 or after.st_size != info.st_size
            or after.st_mtime_ns != info.st_mtime_ns
            or after.st_ctime_ns != info.st_ctime_ns
        ):
            raise _UnsafeCredentialStore()
        match = re.fullmatch(rb"OPENAI_API_KEY=(sk-[A-Za-z0-9_-]{5,509})\n", raw, re.ASCII)
        if match is None:
            raise _UnsafeCredentialStore()
        return match[1].decode("ascii")
    finally:
        match = None
        chunk = b""
        raw[:] = b"\0" * len(raw)
        raw.clear()
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory_fd)


def _save_key(api_key):
    """Atomic private environment-file write, only after explicit human consent."""
    directory_fd = _secret_directory_fd(create=True)
    temporary_name = ".openai-" + secrets.token_hex(16) + ".tmp"
    file_fd = None
    encoded = bytearray()
    try:
        _check_secret_file(directory_fd)
        file_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        os.fchmod(file_fd, 0o600)
        if not re.fullmatch(r"sk-[A-Za-z0-9_-]{5,509}", api_key, re.ASCII):
            raise _UnsafeCredentialStore()
        encoded.extend(("OPENAI_API_KEY=" + api_key + "\n").encode("ascii"))
        view = memoryview(encoded)
        try:
            while view:
                written = os.write(file_fd, view)
                if written <= 0:
                    raise _UnsafeCredentialStore()
                view = view[written:]
        finally:
            view.release()
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = None
        _check_secret_file(directory_fd)
        os.replace(temporary_name, SECRET_FILENAME, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        encoded.clear()
        if file_fd is not None:
            os.close(file_fd)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


@router.get("")
async def status(owner: OwnerIdentity):
    _require_admin(owner)
    saved, storage_available = _saved_status()
    return JSONResponse(
        headers=NO_STORE,
        content={
            "available": True,
            "model": MODEL,
            "provider": "openai",
            "endpoint_host": "api.openai.com",
            "storage": "saved_locally" if saved else "not_saved",
            "saved": saved,
            "storage_available": storage_available,
            "storage_path": str(SECRET_PATH),
            "enabled": False,
            "admin_owner_id": ADMIN_OWNER_ID,
        },
    )


@router.get("/save-page", response_class=HTMLResponse)
async def save_page(owner: OwnerIdentity):
    """Standalone human-only save form. No frontend build or provider exchange."""
    _require_admin(owner)
    nonce = secrets.token_urlsafe(24)
    path = escape(str(SECRET_PATH))
    saved, storage_available = _saved_status()
    page = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>保存 OpenAI API key</title><style nonce="__NONCE__">
body{font:16px/1.6 system-ui,sans-serif;background:#f4f5f7;color:#20212a;margin:0;padding:40px 20px}main{max-width:660px;margin:auto;background:white;border:1px solid #dddfe7;border-radius:18px;padding:28px}h1{font-size:26px;margin:0 0 12px}p{margin:12px 0}code{overflow-wrap:anywhere;font-size:14px}label{display:block;margin:20px 0 8px}input[type=password]{box-sizing:border-box;width:100%;padding:13px;font:inherit;border:1px solid #979ca8;border-radius:8px}button{font:inherit;border:0;border-radius:8px;background:#4859c8;color:white;padding:12px 18px;margin-top:20px;cursor:pointer}button:disabled{opacity:.5;cursor:default}.notice{background:#f1f3fc;padding:14px;border-radius:9px}.confirm{display:flex;gap:10px;align-items:flex-start}.confirm input{margin-top:7px}#result{min-height:26px;white-space:pre-line}.muted{color:#5e6370;font-size:14px}a{color:#3448ad}
</style></head><body><main><h1>保存 OpenAI API key</h1>
<p>这是单独的本机保存页面。请亲自在下方输入现有的 key，不要发到聊天中。</p>
<div class="notice"><strong>下方保存操作只保存，不验证，也不调用 OpenAI。</strong><p>将 OPENAI_API_KEY 保存到此服务器的私有环境变量文件：</p><code>__PATH__</code>
<p>这是未加密的本地明文文件，目录权限 0700，文件权限 0600。保存会替换此路径已有的文件；旧 key 不会被读取或显示。</p>
<p>不会更改全局 shell 环境或当前运行进程。之后仅在你明确要求的流程中加载使用，不会自动启用模型、重放请求或发送聊天内容。</p></div>
<form id="save-form" autocomplete="off"><label for="api-key">OpenAI API key</label>
<input id="api-key" type="password" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" required aria-describedby="privacy">
<p id="privacy" class="muted">提交或离开页面时清空输入。不会写入浏览器存储、应用消息或日志，也不会返回 key 的任何部分。</p>
<label class="confirm"><input id="confirm-save" type="checkbox"><span>我确认按上述路径和明文权限保存 OPENAI_API_KEY，替换该路径的已有文件；仅供之后我明确要求的流程使用。</span></label>
<button id="save-button" type="submit" disabled>保存到本机环境变量文件</button></form>
<section aria-labelledby="verify-title"><h2 id="verify-title">验证已保存的 Key</h2>
<p>点击下方按钮即确认一次会产生少量 OpenAI API 费用的验证。仅发送固定提示词「Reply with OK.」，使用 GPT-6 Luna，store: false，最多 32 个输出 token。不会发送任何应用聊天、历史、记忆或工作区内容。</p>
<p>服务器仅在你点击后读取已保存的私有文件。不会显示 key、再次保存、启用模型或重放积压任务。不会自动重试；超时或断线仍可能产生费用。当前网络连接可能失败。</p>
<button id="verify-saved-button" type="button" disabled>使用已保存 Key 验证 GPT-6 Luna（会产生少量 API 费用）</button></section>
<p id="result" role="status" aria-live="polite"></p><p class="muted">仅限本机 AAA 管理员。<a href="http://127.0.0.1:8010/">返回正常应用 / 重新登录 DEV-AAA</a></p></main>
<script nonce="__NONCE__">
'use strict';
const form=document.getElementById('save-form'), field=document.getElementById('api-key'), confirm=document.getElementById('confirm-save'), button=document.getElementById('save-button'), result=document.getElementById('result');
const verifyButton=document.getElementById('verify-saved-button');
let busy=false, controller=null, safeSaved=__SAFE_SAVED__;
function update(){button.disabled=busy||!confirm.checked||!field.value.length;verifyButton.disabled=busy||!safeSaved;}
field.addEventListener('input',update);confirm.addEventListener('change',update);
update();
window.addEventListener('pagehide',()=>{field.value='';if(controller)controller.abort();});
form.addEventListener('submit',async event=>{
 event.preventDefault();if(busy||!confirm.checked||!field.value.length)return;
 busy=true;let key=field.value,body='';field.value='';result.textContent='正在保存…';update();controller=new AbortController();
 try{
  body=JSON.stringify({api_key:key,confirm_local_save:true});key='';
  const pending=fetch('/api/local-provider-config/save',{method:'POST',credentials:'include',cache:'no-store',headers:{'Content-Type':'application/json','X-Twinteams-Config':'1'},body,signal:controller.signal});body='';
  const response=await pending,data=await response.json();
  if(response.ok&&data&&data.saved===true&&data.enabled===false&&data.error_code===null){result.textContent='已保存 OPENAI_API_KEY 到上述本机私有环境变量文件。未调用 OpenAI，未启用模型。';confirm.checked=false;safeSaved=true;}
  else if(data&&data.error_code==='invalid_key')result.textContent='Key 格式不符。只接受官方 sk- 格式的字母、数字、连字符和下划线，不接受空格、换行或其他字符。输入已清空。';
  else if(data&&data.error_code==='authentication_required')result.textContent='登录已过期。请在正常应用登录 DEV-AAA，再回到此页面。输入已清空。';
  else if(data&&data.error_code==='storage_unavailable')result.textContent='无法确认保存。本机私有文件位置不可用或保存未完成。输入已清空。';
  else result.textContent='无法确认保存。请确认使用本机 AAA 管理员和此页面。输入已清空。';
 }catch{result.textContent='连接中断，无法确认是否已保存。输入已清空。不要在聊天中发送 key。';}
 finally{key='';body='';field.value='';busy=false;controller=null;update();}
});
verifyButton.addEventListener('click',async()=>{
 if(busy||!safeSaved)return;
 busy=true;field.value='';confirm.checked=false;update();controller=new AbortController();result.textContent='正在使用已保存的 Key 验证 GPT-6 Luna… 请勿重复提交。';
 try{
  const response=await fetch('/api/local-provider-config/verify-saved',{method:'POST',credentials:'include',cache:'no-store',headers:{'Content-Type':'application/json','X-Twinteams-Config':'1'},body:JSON.stringify({confirm_billed_call:true}),signal:controller.signal}),data=await response.json();
  const messages={provider_timeout:'验证超时，结果未确认。仍可能产生费用；未自动重试。',provider_connection_failed:'服务器连接 OpenAI 失败，验证结果未确认。仍可能产生费用；未自动重试。',provider_authentication_failed:'OpenAI 未接受已保存的 Key 或其权限。',model_unavailable:'此 OpenAI 项目无法使用 GPT-6 Luna。',provider_limit:'OpenAI 返回额度或速率限制。请先检查项目，再决定是否重试。',provider_rejected:'OpenAI 拒绝了固定验证请求。',provider_invalid_response:'OpenAI 未返回完整有效的 GPT-6 Luna 响应。',provider_unavailable:'OpenAI 暂时无法完成验证。',provider_redirect_refused:'官方服务返回了意外重定向，已拒绝跟随。',authentication_required:'登录已过期。请在正常应用登录 DEV-AAA，再回到此页面。',storage_unavailable:'已保存的私有文件不存在、不安全或格式无效。未调用 OpenAI。',validation_in_progress:'已有验证正在进行。请等待，不要重复提交。',validation_rate_limited:'近期已尝试验证。请等待并检查 OpenAI 账户后再明确重试。'};
  if(response.ok&&data&&data.validated===true&&data.enabled===false&&data.error_code===null)result.textContent='GPT-6 Luna 验证成功。未启用模型，未发送应用聊天，未重放任务。';
  else result.textContent=messages[data&&data.error_code]||'无法确认验证结果。未自动重试，未启用模型。';
  if(data&&data.error_code==='storage_unavailable')safeSaved=false;
 }catch{result.textContent='浏览器与本机服务的连接中断，验证结果未确认。仍可能产生费用；未自动重试。';}
 finally{field.value='';busy=false;controller=null;update();}
});
</script></body></html>"""
    return HTMLResponse(
        page.replace("__NONCE__", nonce).replace("__PATH__", path).replace(
            "__SAFE_SAVED__", "true" if saved and storage_available else "false"
        ),
        headers={
            **NO_STORE,
            "Content-Security-Policy": (
                "default-src 'none'; connect-src 'self'; "
                f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            ),
            "X-Frame-Options": "DENY",
        },
    )


@router.post("/save")
async def save_only(request: Request, owner: OwnerIdentity):
    """Explicit credential storage only. Never calls or enables a provider."""
    _require_admin(owner)
    try:
        async with asyncio.timeout(5):
            data, failure = await _read_input(request, save_only=True)
    except TimeoutError:
        return _result(408, "invalid_request", "The local save form upload timed out.")
    if failure is not None:
        return failure
    try:
        try:
            _save_key(data["api_key"])
        except Exception:  # Never expose filesystem details or credential data.
            return _result(500, "storage_unavailable", "Local credential saving could not be confirmed.")
        return _result(
            200, None, "OPENAI_API_KEY saved to the private local environment file. No provider call was made; no model was enabled.",
            saved=True,
        )
    finally:
        data.clear()


class _AttemptGate:
    """Process-local bounded attempts, without retaining keys or session tokens."""

    def __init__(self, interval=30.0, hourly_limit=5):
        self.interval = interval
        self.hourly_limit = hourly_limit
        self.lock = threading.Lock()
        self.active = False
        self.started = deque(maxlen=hourly_limit)

    def begin(self):
        with self.lock:
            now = time.monotonic()
            if self.active:
                return "validation_in_progress"
            while self.started and self.started[0] < now - 3600:
                self.started.popleft()
            if (self.started and now - self.started[-1] < self.interval) or len(
                self.started
            ) >= self.hourly_limit:
                return "validation_rate_limited"
            self.active = True
            self.started.append(now)
            return None

    def finish(self):
        with self.lock:
            self.active = False


_attempt_gate = _AttemptGate()


def _provider_client():
    # No inherited proxy, alternate endpoint, redirect, custom CA or retry config.
    return httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(
            PROVIDER_TIMEOUT_SECONDS, connect=PROVIDER_CONNECT_TIMEOUT_SECONDS
        ),
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


async def _read_input(request, *, save_only=False, saved_key=False):
    lengths = _header_values(request, "content-length")
    if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]{1,7}", lengths[0])):
        return None, _result(400, "invalid_request", "Submit a valid JSON validation request.")
    if lengths and int(lengths[0]) > MAX_BODY_BYTES:
        return None, _result(
            413, "request_too_large", "Validation requests are limited to 4096 bytes."
        )
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        return None, _result(
            415, "json_required", "Submit the key using the provider configuration form."
        )
    raw = bytearray()
    try:
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_BODY_BYTES:
                return None, _result(
                    413, "request_too_large", "Validation requests are limited to 4096 bytes."
                )
            raw.extend(chunk)
        if lengths and len(raw) != int(lengths[0]):
            return None, _result(400, "invalid_request", "Submit a valid JSON validation request.")
        try:
            data = json.loads(raw, object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError, RecursionError):
            return None, _result(400, "invalid_request", "Submit a valid JSON validation request.")
        if saved_key:
            if not isinstance(data, dict) or set(data) != {"confirm_billed_call"} or data["confirm_billed_call"] is not True:
                return None, _result(400, "confirmation_required", "Explicitly confirm one billed validation using the saved key.")
            return data, None
        if (
            not isinstance(data, dict)
            or set(data) - ({"api_key", "confirm_local_save"} if save_only else {"api_key", "confirm_billed_call", "save_for_later"})
            or data.get("confirm_local_save" if save_only else "confirm_billed_call") is not True
            or (not save_only and type(data.get("save_for_later", False)) is not bool)
        ):
            return None, _result(
                400,
                "confirmation_required",
                "Explicitly confirm the local credential save." if save_only else "Explicitly confirm the billed validation call and optional local save.",
            )
        key = data.get("api_key")
        if not isinstance(key, str) or not re.fullmatch(r"sk-[A-Za-z0-9_-]{5,509}", key, re.ASCII):
            return None, _result(
                400, "invalid_key", "Enter an official OpenAI API key in the password field."
            )
        return data, None
    finally:
        raw.clear()


async def _provider_exchange(api_key):
    request_object = None
    response_bytes = bytearray()
    try:
        async with _provider_client() as client:  # noqa: SIM117 - keep scoped cleanup explicit
            async with client.stream(
                "POST",
                PROVIDER_URL,
                headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                json={
                    "model": MODEL,
                    "input": "Reply with OK.",
                    "store": False,
                    "max_output_tokens": 32,
                    "reasoning": {"effort": "none"},
                },
            ) as response:
                request_object = response.request
                code = response.status_code
                if code in {401, 403}:
                    return _result(
                        422,
                        "provider_authentication_failed",
                        "OpenAI did not accept this key or its permissions.",
                    )
                if code == 404:
                    return _result(
                        422,
                        "model_unavailable",
                        "GPT6 Luna is unavailable for this OpenAI project.",
                    )
                if code == 429:
                    return _result(
                        429,
                        "provider_limit",
                        "OpenAI reported a quota or rate limit. Check the project before trying again.",
                    )
                if 300 <= code < 400:
                    return _result(
                        502,
                        "provider_redirect_refused",
                        "The official provider returned an unexpected redirect.",
                    )
                if code >= 500:
                    return _result(
                        502,
                        "provider_unavailable",
                        "OpenAI could not complete validation. Try again later if desired.",
                    )
                if code != 200:
                    return _result(
                        422,
                        "provider_rejected",
                        "OpenAI rejected the fixed GPT6 Luna validation request.",
                    )
                async for chunk in response.aiter_bytes():
                    if len(response_bytes) + len(chunk) > MAX_PROVIDER_BYTES:
                        return _result(
                            502,
                            "provider_invalid_response",
                            "OpenAI returned an unexpected validation response.",
                        )
                    response_bytes.extend(chunk)
                try:
                    body = json.loads(response_bytes)
                    completed = isinstance(body, dict) and body.get("status") == "completed"
                    matching_model = (
                        isinstance(body, dict)
                        and isinstance(body.get("model"), str)
                        and (body["model"] == MODEL or body["model"].startswith(MODEL + "-"))
                    )
                    output = body.get("output") if isinstance(body, dict) else None
                    has_text = isinstance(output, list) and any(
                        isinstance(item, dict)
                        and item.get("type") == "message"
                        and isinstance(item.get("content"), list)
                        and any(
                            isinstance(part, dict)
                            and part.get("type") == "output_text"
                            and isinstance(part.get("text"), str)
                            and bool(part["text"].strip())
                            for part in item["content"]
                        )
                        for item in output
                    )
                except (ValueError, UnicodeError, RecursionError):
                    completed = matching_model = has_text = False
                if not (completed and matching_model and has_text):
                    return _result(
                        502,
                        "provider_invalid_response",
                        "OpenAI did not return a completed GPT6 Luna response.",
                    )
        return None
    except httpx.TimeoutException:
        return _result(
            504,
            "provider_timeout",
            "Validation timed out. A provider charge may still have occurred; no automatic retry was made.",
        )
    except Exception:  # noqa: BLE001 - never log/stringify a credential-bearing exception
        return _result(
            502,
            "provider_connection_failed",
            "The connection to OpenAI ended before validation could be confirmed. "
            "A provider charge may still have occurred; no automatic retry was made.",
        )
    finally:
        response_bytes.clear()
        if request_object is not None:
            request_object.headers.pop("authorization", None)
        api_key = ""


async def _validate_provider(api_key):
    # HTTPX phase timeouts alone allow an endless trickle of response bytes.
    # This also bounds the complete exchange, including context-manager cleanup.
    try:
        async with asyncio.timeout(PROVIDER_TIMEOUT_SECONDS):
            return await _provider_exchange(api_key)
    except TimeoutError:
        return _result(
            504,
            "provider_timeout",
            "Validation timed out. A provider charge may still have occurred; no automatic retry was made.",
        )
    finally:
        api_key = ""


@router.post("/verify-saved")
async def verify_saved(request: Request, owner: OwnerIdentity):
    """Human-confirmed use only; never save, enable Runtime, or retry."""
    _require_admin(owner)
    try:
        async with asyncio.timeout(5):
            data, failure = await _read_input(request, saved_key=True)
    except TimeoutError:
        return _result(408, "invalid_request", "The validation form upload timed out. No provider call was made.")
    if failure is not None:
        return failure
    api_key = ""
    try:
        admission = _attempt_gate.begin()
        if admission:
            return _result(409 if admission == "validation_in_progress" else 429, admission,
                           "A validation is running or was attempted recently. Wait before explicitly trying again.")
        try:
            try:
                api_key = _read_saved_key()
            except Exception:
                return _result(409, "storage_unavailable", "The saved private credential is missing, unsafe or invalid. No provider call was made.")
            failure = await _validate_provider(api_key)
            if failure is not None:
                return failure
            return _result(200, None, "GPT6 Luna validation succeeded using the saved key. No model was enabled; no application chat was sent.", validated=True, saved=True)
        finally:
            api_key = ""
            _attempt_gate.finish()
    finally:
        data.clear()


@router.post("/verify")
async def verify(request: Request, owner: OwnerIdentity):
    _require_admin(owner)
    try:
        async with asyncio.timeout(5):
            data, failure = await _read_input(request)
    except TimeoutError:
        return _result(
            408,
            "invalid_request",
            "The validation form upload timed out. No provider call was made.",
        )
    if failure is not None:
        return failure
    try:
        if data.get("save_for_later", False) and not _saved_status()[1]:
            return _result(
                409,
                "storage_unavailable",
                "The private local credential path is unsafe or unavailable. No validation call was made.",
            )
        admission = _attempt_gate.begin()
        if admission:
            return _result(
                409 if admission == "validation_in_progress" else 429,
                admission,
                "A validation is running or was attempted recently. Wait before explicitly trying again.",
            )
        try:
            failure = await _validate_provider(data["api_key"])
            if failure is not None:
                return failure
            save = data.get("save_for_later", False)
            if save:
                try:
                    _save_key(data["api_key"])
                except Exception:  # noqa: BLE001 - filesystem failures must not escape this boundary
                    return _result(
                        500,
                        "storage_unavailable",
                        "GPT6 Luna validation succeeded, but the key could not be saved. No model was enabled.",
                        validated=True,
                    )
            return _result(
                200,
                None,
                "GPT6 Luna validation succeeded. Key saved locally for later explicit use; no model was enabled."
                if save
                else "GPT6 Luna validation succeeded. The submitted key was not saved; no model was enabled.",
                validated=True,
                saved=save,
            )
        finally:
            _attempt_gate.finish()
    finally:
        data.clear()
