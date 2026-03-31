import warnings

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.config import Settings, get_settings

bearer_scheme = HTTPBearer(auto_error=False)


async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> bool:
    if not settings.AI_SERVICE_TOKEN:
        warnings.warn("AI_SERVICE_TOKEN 미설정 — 토큰 검증을 건너뜁니다.", stacklevel=1)
        return True
    if credentials is None or credentials.credentials != settings.AI_SERVICE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing service token",
        )
    return True