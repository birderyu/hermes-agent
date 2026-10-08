"""Read-only Ollo conversation discovery and turn-local conversation conventions."""
from collections import OrderedDict
import json
import logging
import re
import threading

logger = logging.getLogger(__name__)

CONVENTIONS = '你在这条 Ollo App 对话中自称 Ollo。'


def read_config(home):
    directory = home / 'ollo'
    if not directory.exists():
        directory = home / 'hermes-plus'
    try:
        value = json.loads((directory / 'conversation.json').read_text())
        if (not isinstance(value, dict) or not isinstance(value.get('session_id'), str)
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,256}', value['session_id'])):
            raise ValueError('Invalid session ID')
        name = value.get('name', 'Ollo')
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError('Invalid conversation name')
        return {'session_id': value['session_id'], 'name': name}
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning('Ollo conversation configuration unavailable (%s)', type(exc).__name__)
        return None


def register(ctx):
    from hermes_constants import get_hermes_home
    from .mood import Classifier, TASK, TIMEOUT, strip_mood, turn_input, with_mood
    home = get_hermes_home()
    config = read_config(home)
    adapter_ref = []
    pending = OrderedDict()
    pending_lock = threading.Lock()
    classifier = Classifier(ctx)
    ctx.register_auxiliary_task(TASK, display_name='Ollo reply mood',
        description='Classify the final Ollo App reply as concerned, happy or none.',
        defaults={'timeout': TIMEOUT, 'prefer_fast_model': True, 'reasoning_effort': 'none'})

    def wire(app, adapter):
        from aiohttp import web
        adapter_ref[:] = [adapter]

        async def conversation(request):
            if not adapter._expected_api_key():
                return web.json_response({'error': 'Authentication unavailable'}, status=503)
            denied = adapter._check_auth(request)
            if denied is not None:
                return denied
            if config is None or get_hermes_home() != home:
                return web.json_response({'error': 'Ollo conversation not configured'}, status=503)
            # Return the configured root ID. Session synchronization resolves its
            # continuation; discovery never opens or creates a SessionDB/session.
            return web.json_response(config)

        for path in ('/v1/ollo/conversation', '/v1/hermes-plus/conversation'):
            app.router.add_get(path, conversation)

    def bound(session_id, platform):
        if (platform != 'api_server' or config is None or not adapter_ref
                or get_hermes_home() != home or not isinstance(session_id, str)):
            return False
        db = adapter_ref[0]._ensure_session_db()
        if db is None or db.get_session(session_id) is None or db.get_session(config['session_id']) is None:
            return False
        return db.resolve_resume_session_id(session_id) == db.resolve_resume_session_id(config['session_id'])

    def context(session_id=None, platform=None, turn_id=None, user_message=None,
                conversation_history=None, **kwargs):
        allowed = bound(session_id, platform)
        with pending_lock:
            if isinstance(turn_id, str) and turn_id:
                pending.pop(turn_id, None)
                if allowed:
                    pending[turn_id] = turn_input(user_message, conversation_history)
                    # Cancelled turns may never reach output transform or session-end.
                    while len(pending) > 64:
                        pending.popitem(last=False)
        return {'context': CONVENTIONS} if allowed else None

    def transform(response_text, session_id=None, platform=None, turn_id=None, **kwargs):
        if not isinstance(response_text, str) or not bound(session_id, platform):
            return None
        with pending_lock:
            inputs = pending.pop(turn_id, None)
        body = strip_mood(response_text)
        return with_mood(body, classifier.classify(inputs, body))

    def discard(turn_id=None, **kwargs):
        with pending_lock:
            pending.pop(turn_id, None)

    ctx.register_platform_handler('api_server', wire)
    ctx.register_hook('pre_llm_call', context)
    ctx.register_hook('transform_llm_output', transform)
    ctx.register_hook('on_session_end', discard)
