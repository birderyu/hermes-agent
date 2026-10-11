"""Turn capabilities are host-minted, never inferred from a model's arguments."""
from collections import OrderedDict
from contextvars import ContextVar
import threading
import sqlite3

from .protocol import BridgeError, dumps, require, validate
from .run_context import RunRequests

CALL = ContextVar('ollo_bridge_tool_call', default=None)
CONFIRM_MARKER = 'hermes-plus-gtd-confirm'


class Binding:
    def __init__(self, home, config):
        self.home, self.config, self.adapter = home, config, None
        self.requests = RunRequests()
        self.turns = OrderedDict()
        self.lock = threading.Lock()

    def bound(self, session, platform='api_server'):
        """True for the Ollo lineage, False outside it, None if its store is unavailable."""
        from hermes_constants import get_hermes_home
        from agent.delegation_context import is_delegated_child_context
        if (not self.config or not self.adapter or platform != 'api_server' or not isinstance(session, str)
                or get_hermes_home() != self.home or is_delegated_child_context()):
            return False
        try:
            db = self.adapter._ensure_session_db()
            if db is None:
                return None
            current, root = db.get_session(session), db.get_session(self.config.session_id)
            if not current or not root or current.get('source') != 'api_server' or root.get('source') != 'api_server':
                return False
            return db.resolve_resume_session_id(session) == db.resolve_resume_session_id(self.config.session_id)
        except (sqlite3.Error, OSError):
            return None

    def start(self, session_id=None, task_id='', turn_id='', platform='', user_message='', parent_session_id='', **kwargs):
        text = user_message
        if isinstance(text, list):
            text = '\n'.join(v.get('text', '') for v in text if isinstance(v, dict) and v.get('type') == 'text')
        restricted = isinstance(text, str) and CONFIRM_MARKER in text
        allowed = not parent_session_id and self.bound(session_id, platform)
        with self.lock:
            # Host IDs may be reused by another platform; revoke on every start.
            self.turns.pop((task_id, turn_id), None)
            if allowed and task_id and turn_id:
                self.turns[(task_id, turn_id)] = {'session': session_id, 'restricted': restricted,
                    'confirmation_id': self.request_id(session_id, task_id, turn_id) if restricted else None}
                while len(self.turns) > 128:
                    self.turns.popitem(last=False)
        return allowed, restricted

    def state(self, session_id, task_id, turn_id):
        with self.lock:
            state = self.turns.get((task_id, turn_id))
        if not state or not self.bound(session_id) or not self.bound(state['session']):
            return None
        return state

    def current_run_id(self, session_id, task_id, turn_id):
        from hermes_constants import get_hermes_home
        from agent.delegation_context import is_delegated_child_context
        from tools.approval_context import _approval_session_key
        if (not self.config or not self.adapter or get_hermes_home() != self.home
                or is_delegated_child_context() or not task_id or not turn_id):
            return None
        # API workers already bind run_id here. Bypass the public getter's
        # environment fallback, then require this exact live agent/turn.
        run_id = _approval_session_key.get()
        agent = self.adapter._active_run_agents.get(run_id)
        if (agent is not None and agent.session_id == session_id
                and getattr(agent, '_current_task_id', None) == task_id
                and getattr(agent, '_current_turn_id', None) == turn_id):
            return run_id
        return None

    def guard(self, tool_name='', session_id=None, task_id='', turn_id='', **kwargs):
        state = self.state(session_id, task_id, turn_id)
        # If the hook/store failed, we cannot know whether this is a confirmation
        # turn. Fail closed for the active bound API run, including generic tools.
        if (not state and self.current_run_id(session_id, task_id, turn_id)
                and self.bound(session_id) is not False):
            return {'action': 'block', 'message': '缺少可信的 Ollo 回合上下文，不能执行工具。'}
        if state and state['restricted'] and tool_name != 'execute_gtd_confirmation':
            return {'action': 'block', 'message': '此确认回合只能调用 execute_gtd_confirmation；不能绕过执行许可。'}
        if tool_name in {'get_user_location', 'get_gtd_reports', 'execute_gtd_confirmation'} and not state:
            return {'action': 'block', 'message': '没有可信的 Ollo 当前回合。'}
        return None

    def request_id(self, session_id, task_id, turn_id):
        run_id = self.current_run_id(session_id, task_id, turn_id)
        return self.requests.get(self.adapter, run_id) if run_id else None

    def confirmation_id(self):
        call = CALL.get()
        require(call is not None, 'forbidden', 403)
        state = self.state(call['session_id'], call['task_id'], call['turn_id'])
        identifier = self.request_id(call['session_id'], call['task_id'], call['turn_id'])
        require(state and state['restricted'] and identifier and state['confirmation_id'] == identifier, 'forbidden', 403)
        return identifier

    def middleware(self, next_call, tool_name='', session_id=None, task_id='', turn_id='', tool_call_id='', **kwargs):
        denied = self.guard(tool_name, session_id, task_id, turn_id)
        if denied:
            return dumps({'error': denied['message']})
        token = CALL.set({'session_id': session_id, 'task_id': task_id, 'turn_id': turn_id, 'tool_call_id': tool_call_id})
        try:
            return next_call()
        finally:
            CALL.reset(token)

    def runtime(self, *, confirmation=False):
        call = CALL.get()
        require(call is not None, 'forbidden', 403)
        state = self.state(call['session_id'], call['task_id'], call['turn_id'])
        require(state is not None, 'forbidden', 403)
        runtime = {key: call[key] for key in ('session_id', 'task_id', 'tool_call_id')}
        if confirmation:
            run_id = self.current_run_id(call['session_id'], call['task_id'], call['turn_id'])
            require(state['restricted'] and run_id is not None, 'forbidden', 403)
            # The gateway knows the ID returned by POST /v1/runs, not Hermes'
            # conversation-scoped terminal task alias. Send the host's unique run ID.
            runtime['task_id'] = run_id
        runtime['source'] = 'tool'
        validate('B_BridgeRuntime', runtime)
        return runtime

    def end(self, task_id='', turn_id='', session_id=None, **kwargs):
        with self.lock:
            for key in list(self.turns):
                if key == (task_id, turn_id) or self.turns[key]['session'] == session_id:
                    self.turns.pop(key)
