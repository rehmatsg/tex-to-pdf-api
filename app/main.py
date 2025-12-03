from fastapi import FastAPI
from app.core.config import settings
from app.api import routes_compile

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
    )

    app.include_router(routes_compile.router)

    @app.get("/health")
    async def health_check():
        return {
            "status": "ok",
            "version": settings.VERSION,
            "tex_available": True, # TODO: Check actual availability
        }

    return app

app = create_app()
