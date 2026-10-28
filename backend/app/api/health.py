from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["health"])
async def health_check() -> dict:
    """Report that the HTTP application is running."""
    return {"status": "ok"}
