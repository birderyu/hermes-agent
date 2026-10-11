"""Thin Ollo transport: one plugin, no report/location/receipt database."""
import logging

from .apple import AppleBackend
from .config import read_config
from .http_client import GatewayClient
from .identity import Binding
from .protocol import BridgeError, require, validate
from .integration import Integration
from .reports import delivery
from .schedule import Schedule

logger = logging.getLogger(__name__)


def register(ctx):
    from hermes_constants import get_hermes_home
    from gateway.config import Platform
    from gateway.platforms.base import BasePlatformAdapter, SendResult

    home = get_hermes_home()
    try:
        config = read_config(home)
    except (BridgeError, OSError, ValueError, TypeError, KeyError) as exc:
        logger.warning('Ollo bridge configuration unavailable (%s)', type(exc).__name__)
        config = None
    client = GatewayClient(config)
    binding = Binding(home, config)
    schedule = Schedule(config, client, AppleBackend(config.helper_path if config else '', config.apple if config else None), ctx.llm)
    ctx.register_auxiliary_task('ollo_interpret', display_name='Ollo schedule interpretation',
        description='Isolated structured suggestion; no tools, memory or conversation writes.',
        defaults={'timeout': 15, 'prefer_fast_model': True, 'reasoning_effort': 'none'})

    class BridgeAdapter(BasePlatformAdapter):
        splits_long_messages = True
        supports_async_delivery = True
        # The gateway durably deduplicates by execution_id, including suppressed
        # agenda attempts. This is a transport property, never model input.
        idempotent_delivery = True

        def __init__(self, platform_config, platform='ollo'):
            super().__init__(platform_config, Platform(platform))

        async def connect(self, *, is_reconnect=False):
            self._running = config is not None
            return self._running

        async def disconnect(self):
            self._running = False

        async def get_chat_info(self, chat_id):
            return {'name': 'Ollo GTD', 'type': 'dm'}

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            try:
                require(get_hermes_home() == home, 'forbidden', 403)
                payload = delivery(config, chat_id, content, metadata or {})
                result = await client.request('POST', '/internal/v1/reports', body=payload, timeout=2)
                validate('B_BridgeReportReceipt', result)
                return SendResult(success=True, message_id=result['id'])
            except (BridgeError, ValueError, TypeError) as exc:
                return SendResult(success=False, error='Ollo report delivery failed',
                                  raw_response={'retryable': isinstance(exc, BridgeError) and exc.retryable})

    async def standalone(*args, **kwargs):
        return {'error': 'Ollo requires the live adapter with trusted cron execution metadata'}

    for name in ('ollo', 'hermes_plus'):
        ctx.register_platform(name, 'Ollo', lambda pc, name=name: BridgeAdapter(pc, name), lambda: True,
            validate_config=lambda _: config is not None,
            cron_deliver_env_var='OLLO_HOME_CHANNEL' if name == 'ollo' else 'HERMES_PLUS_HOME_CHANNEL',
            parse_target_ref_fn=lambda target: ('main', None) if target == 'main' else None,
            validate_target_ref_fn=lambda target: target == 'main', standalone_sender_fn=standalone,
            allow_update_command=False, pii_safe=True)

    Integration(home, config, client, binding, schedule).register(ctx)
