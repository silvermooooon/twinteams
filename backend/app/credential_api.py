"""Dedicated authenticated value-entry endpoints. Never expose submitted input in errors."""
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from . import credentials
from .api import OwnerIdentity
from .notifications import notifications
from .service import service


class CredentialRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def safe_handler(request):
            try:
                response = await original(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except RequestValidationError:
                return JSONResponse(status_code=422, content={"detail": "Credential form is invalid"})
            except HTTPException:
                raise
            except Exception:  # noqa: BLE001 - this boundary deliberately discards sensitive exception inputs
                # In particular do not pass exception text, request bodies or Pydantic
                # inputs to the application's general traceback logger.
                return JSONResponse(status_code=500, content={"detail": "Credential operation failed"})
        return safe_handler


router = APIRouter(prefix="/api/me/twin", route_class=CredentialRoute)


class CredentialInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    description: str = Field(default="", max_length=2000)
    values: dict[Annotated[str, Field(min_length=1, max_length=128)], SecretStr] | None = None
    value: SecretStr | None = None

    def plaintext(self):
        return {key: value.get_secret_value() for key, value in self.values.items()} if self.values is not None else None


class CompletionInput(CredentialInput):
    existing_name: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=128)


@router.get("/credentials")
async def list_credentials(owner: OwnerIdentity):
    return await credentials.list_credentials(owner)


@router.put("/credentials/{name}")
async def put_credential(name: str, data: CredentialInput, owner: OwnerIdentity):
    result = await service.db_call(credentials.manage, service, owner, name,
        values=data.plaintext(), value=data.value.get_secret_value() if data.value else None,
        description=data.description)
    notifications.emit_personal(owner, "credentials")
    return result


@router.delete("/credentials/{name}")
async def delete_credential(name: str, owner: OwnerIdentity):
    result = await service.db_call(credentials.manage, service, owner, name, delete=True)
    notifications.emit_personal(owner, "credentials")
    return result


@router.get("/credential-requests")
async def list_requests(owner: OwnerIdentity):
    return await service.db_call(credentials.list_requests, service, owner)


@router.post("/credential-requests/{request_id}/complete")
async def complete_request(request_id: str, data: CompletionInput, owner: OwnerIdentity):
    result = await service.db_call(credentials.complete, service, owner, request_id,
        existing_name=data.existing_name, name=data.name, values=data.plaintext(),
        value=data.value.get_secret_value() if data.value else None,
        description=data.description if "description" in data.model_fields_set else None)
    notifications.emit_personal(owner, "credentials")
    return result


@router.post("/credential-requests/{request_id}/dismiss")
async def dismiss_request(request_id: str, owner: OwnerIdentity):
    result = await service.db_call(credentials.dismiss, service, owner, request_id)
    notifications.emit_personal(owner, "credentials")
    return result
