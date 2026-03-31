import uvicorn

from api.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    print(f"[INFO] {settings.APP_NAME} 시작 중...")
    print(f"[INFO] Host: {settings.APP_HOST}, Port: {settings.APP_PORT}")
    uvicorn.run(
        "api.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.APP_ENV == "local",
    )