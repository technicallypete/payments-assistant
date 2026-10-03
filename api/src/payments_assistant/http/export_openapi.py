"""Print the API's OpenAPI schema (`python -m payments_assistant.http.export_openapi`).

The Next app generates its TypeScript types from this (app/lib/api-types.ts).
"""

import json

from payments_assistant.http.app import create_app

if __name__ == "__main__":
    print(json.dumps(create_app().openapi(), indent=2, sort_keys=True))
