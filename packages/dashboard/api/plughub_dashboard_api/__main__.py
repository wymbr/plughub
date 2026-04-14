"""Entry point for the CLI script."""

import os
import uvicorn


def main() -> None:
    port = int(os.environ.get("PLUGHUB_DASHBOARD_API_PORT", "8082"))
    uvicorn.run(
        "plughub_dashboard_api.app:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
