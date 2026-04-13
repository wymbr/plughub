"""Entry point for the CLI script."""

import uvicorn


def main() -> None:
    uvicorn.run(
        "plughub_dashboard_api.app:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
    )


if __name__ == "__main__":
    main()
