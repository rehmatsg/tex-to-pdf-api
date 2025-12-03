from fastapi import FastAPI
from app.core.config import settings
from app.api import routes_compile

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