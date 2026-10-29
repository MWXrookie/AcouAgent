"""Loopback-only UI acceptance for production task-preview handlers.

Only read-only page/profile routes and the no-execution contract handlers are
registered. No model/solver endpoints or production data stores are available.
"""
import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview-only', action='store_true', required=True)
    parser.add_argument('--port', type=int, default=18221)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise ValueError('invalid_preview_port')
    for key in list(os.environ):
        if key.startswith(('MODEL_', 'SKILL_', 'DIFY_')) or key in {'DEEPSEEK_API_KEY', 'PORTAL_ADMIN_TOKEN'}:
            os.environ.pop(key)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from starlette.applications import Starlette
    from starlette.routing import Route
    import uvicorn
    from app import portal
    class Registry:
        def __init__(self):
            self.routes = []
        def custom_route(self, path, methods):
            def decorate(fn):
                if path in {'/', '/portal', '/health', '/api/model-profiles', '/api/task-intake', '/api/task-intake/confirm', '/api/task-intake/cancel', '/skills/api'}:
                    self.routes.append(Route(path, fn, methods=methods))
                return fn
            return decorate
    registry = Registry()
    portal.register(registry)
    uvicorn.run(Starlette(routes=registry.routes), host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
