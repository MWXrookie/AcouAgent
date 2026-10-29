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
    modes.add_argument('--execute-workflow', action='store_true')
    parser.add_argument('--ssh-host')
    parser.add_argument('--gateway-container', default='dify-mcp')
    parser.add_argument('--legacy-model', help='Explicit existing product preset if gateway has only the authorized legacy key')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--skills-copy', type=Path, help='Optional local copied library; read-only routes only')
    parser.add_argument('--port', type=int, default=18221)
    parser.add_argument('--general-learning',action='store_true',help='Fresh isolated library, real learning worker and independent pinned references')
    parser.add_argument('--reviewed-references',type=Path)
    args = parser.parse_args()
    if args.general_learning and (not args.execute_workflow or args.skills_copy or not args.reviewed_references):
        parser.error('general learning requires real workflow, reviewed references and an empty isolated store')
    if not 1024 <= args.port <= 65535:
        raise ValueError('invalid_preview_port')
    for key in list(os.environ):
        if key.startswith(('MODEL_', 'SKILL_', 'DIFY_')) or key in {'DEEPSEEK_API_KEY', 'PORTAL_ADMIN_TOKEN','DEFAULT_MODEL_PROFILE'}:
            os.environ.pop(key)
    os.environ['ROOM_MODAL_ENABLED'] = 'false'
    os.environ['TASK_CONFIRMATION_ENABLED'] = 'false'
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
    if args.execute_room or args.execute_workflow:
        if not args.ssh_host or not args.data_dir:
            parser.error('real room mode requires --ssh-host and a fresh --data-dir')
        args.data_dir.mkdir(parents=True, exist_ok=False)
        config.ROOM_MODAL_ENABLED = args.execute_room
        config.ROOM_MODAL_RUNS_PATH = str(args.data_dir/'tasks.db')
        if args.execute_workflow:
            from scripts.validation.gateway_model_session import load
            load(args.ssh_host,args.gateway_container,args.legacy_model)
            config.TASK_CONFIRMATION_ENABLED = True
            config.TASK_CONFIRMATION_RUNS_PATH = str(args.data_dir/'confirmed_tasks.db')
        dashboard.DB_PATH = str(args.data_dir/'history.db')
        dashboard.init_db()
        from scripts.validation.room_mode_probe import remote_execute
        execution._execute_code = lambda code, timeout, **kw:remote_execute(args.ssh_host,args.gateway_container,code,timeout,managed=args.execute_workflow,**kw)
    worker = None
    if args.general_learning:
        from app import skill_library_service
        from app.skill_store import SkillStore
        from scripts.skill_library.prepare_general import runtime_identity
        from app.skill_general_library import get_library
        from app.skill_auto_worker import start_worker
        for name in ('SKILL_LIBRARY_ENABLED','SKILL_RETRIEVAL_ENABLED','SKILL_INJECTION_ENABLED',
                     'SKILL_AUTHORING_ENABLED','SKILL_VALIDATION_ENABLED','SKILL_GENERAL_ADMISSION_ENABLED','SKILL_LEARNING_ENABLED'):
            setattr(config,name,True)
        config.SKILL_GENERAL_REFERENCE_ROOT = str(args.reviewed_references.resolve())
        config.SKILL_GENERAL_ASSET_ROOT = str((args.data_dir/'assets').resolve())
        config.SKILL_GENERAL_RUNTIME_SHA256 = runtime_identity()
        skill_library_service.STORE = store = SkillStore(args.data_dir/'skills.db')
        store.migrate_skill_authoring()
        library = get_library(store)
        for case in ('B01','B02','B03'):
            library.install_profile(case)
        worker = start_worker()
        if worker is None or not worker.is_alive():
            raise RuntimeError('isolated_learning_worker_not_ready')
    class Registry:
        def __init__(self):
            self.routes = []
        def custom_route(self, path, methods):
            def decorate(fn):
                if path in {'/', '/portal', '/health', '/api/model-profiles', '/api/task-intake', '/api/task-intake/confirm', '/api/task-intake/cancel', '/api/task-intake/status', '/skills/api'} or ((args.execute_room or args.execute_workflow) and methods == ['GET'] and path in {'/dashboard', '/dashboard/api/executions', '/dashboard/api/executions/by-run/{run_id}'}) or ((args.skills_copy or args.general_learning) and methods == ['GET'] and (path.startswith('/skills') or path.startswith('/api/skill'))):
                    self.routes.append(Route(path, fn, methods=methods))
                return fn
            return decorate
    registry = Registry()
    portal.register(registry)
    try:
        uvicorn.run(Starlette(routes=registry.routes), host='127.0.0.1', port=args.port, access_log=False)
    finally:
        if worker is not None:
            worker.stop()


if __name__ == '__main__':
    main()
