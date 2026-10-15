"""AstrBot 4.25.5 adapter: lifecycle only, deliberately no user-facing handlers."""
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import MessageChain
from astrbot.api.star import Context, Star, StarTools, register

from .engine import Consumer, HttpFetcher, Settings, valid_target


@register('astrbot_plugin_cdu_radar', 'CDU Radar contributors',
          '固定来源、固定会话、默认关闭的人工批准 RSS', '0.1.1')
class CDURadarPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.consumer = None
        self.fetcher = None
        self.settings = None
        self._active = False

    def _authorized(self):
        if not self._active or self.consumer is None or self.consumer.closed or self.consumer.stopping:
            return False
        try:
            live = Settings.parse(self.config)
            return live.enabled and live == self.settings and valid_target(live.target_umo)
        except (ValueError, TypeError, AttributeError):
            return False

    async def _send(self, target, text):
        if not self._authorized() or target != self.settings.target_umo or not valid_target(target):
            raise PermissionError('Exact target authorization is required')
        # Plain component only. No event, command, model, provider or other plugin.
        return await self.context.send_message(target, MessageChain().message(text))

    async def initialize(self):
        if self.consumer is not None:
            raise RuntimeError('Already initialized')
        try:
            self.settings = Settings.parse(self.config)
            if not self.settings.enabled:
                logger.info('CDU Radar RSS disabled; no polling or sends')
                return
            self._active = True
            self.fetcher = HttpFetcher()
            self.consumer = Consumer(self.settings, StarTools.get_data_dir('astrbot_plugin_cdu_radar'),
                                     self.fetcher, self._send, self._authorized)
            self.consumer.start()
        except Exception:
            await self.terminate()
            # Avoid exception strings containing private targets, URLs or paths.
            logger.error('CDU Radar RSS initialization refused; review private configuration/state')
            raise RuntimeError('CDU Radar initialization refused') from None

    async def terminate(self):
        # Wait for cancellation to finish; no detached send/retry tasks.
        self._active = False
        if self.consumer is not None:
            await self.consumer.close()
            self.consumer = None
        if self.fetcher is not None:
            await self.fetcher.close()
            self.fetcher = None
