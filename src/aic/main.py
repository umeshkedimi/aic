"""Application entrypoint.

Creates the FastAPI application and provides the uvicorn entry point.
"""

from aic.api.app import create_app

# Create the application instance
app = create_app()


def main() -> None:
    """Run the application with uvicorn."""
    import uvicorn

    from aic.config import get_settings

    settings = get_settings()

    uvicorn.run(
        "aic.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.is_development,
        workers=settings.workers if not settings.is_development else 1,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
