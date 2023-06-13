import os
from schematic_api.api import app
import asyncio


def main(): 
    # Get app configuration
    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = os.environ.get("APP_PORT", "3001")
    port = int(port)

    # Launch app
    app.run(host=host, port=port, debug=True)

if __name__ == "__main__":
    asyncio.run(main())