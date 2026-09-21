from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from accounts.service import AccountService, VaultNotConfiguredError
from broker.broker import SqliteTaskBroker

from ..deps import get_accounts, get_broker
from ..errors import InvalidRequestError, NotFoundError, VaultLockedError
from ..schemas.accounts import (
    AccountStatusResponse,
    JobOut,
    LoginRequest,
    StoreCookieRequest,
    StoreCredentialsRequest,
)

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


@router.get("/status", response_model=AccountStatusResponse)
async def get_default_account_status(
    accounts: AccountService = Depends(get_accounts),
) -> AccountStatusResponse:
    info = accounts.status("kktix")
    return AccountStatusResponse(
        platform=info.platform,
        source=info.source,
        configured=info.configured,
        masked_account=info.masked_account,
        credential_kind=info.credential_kind.value if info.credential_kind else None,
    )


@router.get("/{platform}/status", response_model=AccountStatusResponse)
async def get_platform_account_status(
    platform: str,
    accounts: AccountService = Depends(get_accounts),
) -> AccountStatusResponse:
    info = accounts.status(platform)
    return AccountStatusResponse(
        platform=info.platform,
        source=info.source,
        configured=info.configured,
        masked_account=info.masked_account,
        credential_kind=info.credential_kind.value if info.credential_kind else None,
    )


@router.put("/{platform}/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def store_platform_credentials(
    platform: str,
    req: StoreCredentialsRequest,
    accounts: AccountService = Depends(get_accounts),
) -> Response:
    try:
        accounts.store(platform, req.account, req.access_key)
    except VaultNotConfiguredError as exc:
        raise VaultLockedError(str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{platform}/cookie", status_code=status.HTTP_204_NO_CONTENT)
@router.put("/{platform}/cookie", status_code=status.HTTP_204_NO_CONTENT)
async def store_platform_cookies(
    platform: str,
    req: StoreCookieRequest,
    accounts: AccountService = Depends(get_accounts),
) -> Response:
    try:
        accounts.store_cookie(platform, req.cookies)
    except VaultNotConfiguredError as exc:
        raise VaultLockedError(str(exc)) from exc
    except ValueError as exc:
        raise InvalidRequestError(str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{platform}/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def erase_platform_credentials(
    platform: str,
    accounts: AccountService = Depends(get_accounts),
) -> Response:
    accounts.erase(platform)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{platform}/session/check",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_session_check(
    platform: str,
    profile: str = "live",
    accounts: AccountService = Depends(get_accounts),
) -> JobOut:
    job_id = await accounts.request_session_check(platform, profile=profile)
    return JobOut(
        job_id=job_id,
        kind="SESSION_CHECK",
        state="PENDING",
    )


@router.post(
    "/{platform}/login",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_login(
    platform: str,
    req: LoginRequest,
    accounts: AccountService = Depends(get_accounts),
) -> JobOut:
    job_id = await accounts.request_login(platform, profile=req.profile, mode=req.mode)
    kind_str = "AUTO_LOGIN" if req.mode == "auto" else "MANUAL_LOGIN"
    return JobOut(
        job_id=job_id,
        kind=kind_str,
        state="PENDING",
    )


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_account_job(
    job_id: str,
    broker: SqliteTaskBroker = Depends(get_broker),
) -> JobOut:
    job = await broker.get(job_id)
    if job is None:
        raise NotFoundError(f"Job {job_id} not found")
    return JobOut(
        job_id=job.id,
        kind=job.kind.value,
        state=job.state.value,
        result=job.result,
        error=job.error,
    )
