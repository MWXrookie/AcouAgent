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
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--preview-only', action='store_true')
    modes.add_argument('--execute-room', action='store_true')
    parser.add_argument('--ssh-host')
    parser.add_argument('--gateway-container', default='dify-mcp')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--skills-copy', type=Path, help='Optional local copied library; read-only routes only')
    parser.add_argument('--port', type=int, default=18221)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise ValueError('invalid_preview_port')
    for key in list(os.environ):
        if key.startswith(('MODEL_', 'SKILL_', 'DIFY_')) or key in {'DEEPSEEK_API_KEY', 'PORTAL_ADMIN_TOKEN'}:
            os.environ.pop(key)
    os.environ['ROOM_MODAL_ENABLED'] = 'false'
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from starlette.applications import Starlette
    from starlette.routing import Route
    import uvicorn
    from app import portal, config, dashboard, execution
    if args.skills_copy:
        if not args.skills_copy.is_file():
            parser.error('skills copy must already exist')
        from app import skill_library_service
        from app.skill_store import SkillStore
        skill_library_service.STORE = SkillStore(args.skills_copy)
        config.SKILL_LIBRARY_ENABLED = True
    if args.execute_room:
        if not args.ssh_host or not args.data_dir:
            parser.error('real room mode requires --ssh-host and a fresh --data-dir')
        args.data_dir.mkdir(parents=True, exist_ok=False)
        config.ROOM_MODAL_ENABLED = True
        config.ROOM_MODAL_RUNS_PATH = str(args.data_dir/'tasks.db')
        dashboard.DB_PATH = str(args.data_dir/'history.db')
        dashboard.init_db()
        from scripts.validation.room_mode_probe import remote_execute
        execution._execute_code = lambda code, timeout, **kw:remote_execute(args.ssh_host,args.gateway_container,code,timeout,**kw)
    class Registry:
        def __init__(self):
            self.routes = []
        def custom_route(self, path, methods):
            def decorate(fn):
                if path in {'/', '/portal', '/health', '/api/model-profiles', '/api/task-intake', '/api/task-intake/confirm', '/api/task-intake/cancel', '/api/task-intake/status', '/skills/api'} or (args.execute_room and methods == ['GET'] and path in {'/dashboard', '/dashboard/api/executions', '/dashboard/api/executions/by-run/{run_id}'}) or (args.skills_copy and methods == ['GET'] and path in {'/skills','/skills/api/{skill_id}','/skills/api/{skill_id}/events','/skills/api/{skill_id}/feedback'}):
                    self.routes.append(Route(path, fn, methods=methods))
                return fn
            return decorate
    registry = Registry()
    portal.register(registry)
    uvicorn.run(Starlette(routes=registry.routes), host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
