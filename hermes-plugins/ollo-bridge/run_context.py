"""Bind authenticated request UUIDs to admitted runs across executor threads."""
import json
import threading

from aiohttp import web
from hermes_constants import get_hermes_home

from .protocol import BridgeError, dumps, validate

class RunRequests:
    def __init__(self):
        self._runs = {}
        self._lock = threading.Lock()

    def remember(self, adapter, owner, key, response):
        if key is None or response.status != 202 or not isinstance(response, web.Response):
            return
        admitted = json.loads(response.body)
        run_id = admitted.get('run_id')
        task = adapter._active_run_tasks.get(run_id)
        if admitted.get('replayed') or task is None or task.done() or adapter._run_owners.get(run_id) != owner:
            return
        with self._lock:
            self._runs[run_id] = (adapter, owner, key)
        task.add_done_callback(lambda _: self.forget(run_id))

    def forget(self, run_id):
        with self._lock:
            self._runs.pop(run_id, None)

    def get(self, adapter, run_id):
        with self._lock:
            request = self._runs.get(run_id)
        if request and request[0] is adapter and adapter._run_owners.get(run_id) == request[1]:
            return request[2]
        return None


def run_middleware(binding):
    @web.middleware
    async def middleware(request, handler):
        adapter = binding.adapter
        if (get_hermes_home() != binding.home or not binding.config
                or binding.config.key == adapter._expected_api_key()):
            return await handler(request)
        route = request.match_info.route.resource
        path = route.canonical if route is not None else ''
        if request.method == 'POST' and path in ('/v1/runs', '/p/{profile}/v1/runs'):
            if (not adapter._expected_api_key() or adapter._room_grant_token(request)
                    or adapter._check_auth(request) is not None):
                return await handler(request)
            # Host authentication, admission and idempotency still run unchanged.
            key = None
            keys = request.headers.getall('Idempotency-Key', [])
            if len(keys) == 1:
                try:
                    key = validate('B_UUID', keys[0].strip())
                except BridgeError:
                    pass  # Non-UUID keys remain valid for ordinary Hermes runs.
            owner = adapter._run_idempotency_scope(request)
            response = await handler(request)
            # v0.21.6 returns immediately after create_task, with no further await:
            # bind before the background run can enter its executor. That executor
            # rebinds host identity, but does NOT copy arbitrary ContextVars.
            binding.requests.remember(adapter, owner, key, response)
            return response
        response = await handler(request)
        if (request.method == 'GET' and path in ('/v1/runs/{run_id}', '/p/{profile}/v1/runs/{run_id}')
                and response.status == 200 and isinstance(response, web.Response)):
            # The host has already checked API credentials AND run ownership.
            status = json.loads(response.body)
            run_id = status.get('run_id')
            agent = adapter._active_run_agents.get(run_id)
            session = getattr(agent, 'session_id', None) or status.get('session_id')
            if run_id == request.match_info.get('run_id') and binding.bound(session):
                # Export the same run-scoped task identity used by the tool;
                # don't change the core's session-scoped terminal task alias.
                status.update(task_id=run_id, session_id=session)
                response.body = dumps(status).encode('utf-8')
        return response
    return middleware
