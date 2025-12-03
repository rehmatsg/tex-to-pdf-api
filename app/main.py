from fastapi import FastAPI
from app.core.config import settings
from app.api import routes_compile
import shutil

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
)

app.include_router(routes_compile.router)

@app.get("/")
async def root():
    return {"message": "Welcome to the LaTeX API!"}

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "version": settings.VERSION,
        "tex_available": shutil.which("pdflatex") is not None,
    }