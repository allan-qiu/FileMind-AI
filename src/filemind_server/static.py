from pathlib import PurePosixPath

from fastapi.staticfiles import StaticFiles


class WebAssets(StaticFiles):
    """Serve web assets consistently even if Windows registry MIME mappings differ."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        content_type = {
            ".js": "text/javascript; charset=utf-8",
            ".mjs": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".html": "text/html; charset=utf-8",
        }.get(PurePosixPath(path).suffix)
        if content_type:
            response.headers["Content-Type"] = content_type
        return response
