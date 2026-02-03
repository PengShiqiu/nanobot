"""Feishu/Lark channel implementation using lark-oapi SDK.

Dependencies:
    pip install lark-oapi

If you encounter `ModuleNotFoundError: No module named 'lark_oapi'` when using uv tool:

    # Find the uv tool Python path
    cat ~/.local/bin/nanobot | head -1

    # Install lark-oapi to the tool environment
    uv pip install lark-oapi --python ~/.local/share/uv/tools/nanobot-ai/bin/python
"""

import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import lark_oapi as lark
from loguru import logger
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import FeishuConfig


class FeishuChannel(BaseChannel):
    """
    Feishu/Lark channel using WebSocket event streaming.

    Supports both P2P (private chat) and group messages.
    """

    name = "feishu"

    def __init__(self, config: FeishuConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: FeishuConfig = config
        self._client: Any = None  # lark.Client for API calls
        self._ws_client: Any = None  # lark.ws.Client for events
        self._executor: ThreadPoolExecutor | None = None
        self._event_queue: asyncio.Queue | None = None
        self._process_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the Feishu WebSocket connection."""
        # 配置日志输出到终端
        logger.add(
            sys.stderr,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            level="DEBUG",
            colorize=True,
        )
        
        logger.info("=" * 60)
        logger.info("[Feishu] Starting Feishu channel...")
        logger.info(f"[Feishu] app_id: {self.config.app_id}")
        logger.info(f"[Feishu] app_secret: {'*' * len(self.config.app_secret) if self.config.app_secret else 'None'}")
        logger.info(f"[Feishu] base_domain: {self.config.base_domain}")
        logger.info("=" * 60)

        if not self.config.app_id or not self.config.app_secret:
            logger.error("[Feishu] app_id or app_secret not configured")
            return

        self._running = True

        # Import lark SDK here to avoid import errors if not installed
        try:
            from lark_oapi import Client, ws
            logger.info("[Feishu] lark-oapi SDK imported successfully")
        except ImportError as e:
            logger.error(f"[Feishu] lark-oapi not installed: {e}")
            logger.error("[Feishu] Run: pip install lark-oapi")
            return

        # Create event queue and thread pool
        self._event_queue = asyncio.Queue()
        self._executor = ThreadPoolExecutor(max_workers=1)
        logger.info("[Feishu] Event queue and thread pool created")

        # Create API client (for sending messages)
        logger.info("[Feishu] Creating API client...")
        self._client = (
            Client.builder()
            .app_id(self.config.app_id)
            .app_secret(self.config.app_secret)
            .build()
        )
        logger.info("[Feishu] API client created")

        # Register event handler using EventDispatcherHandler
        logger.info("[Feishu] Registering event handler...")
        event_handler = (
            lark.EventDispatcherHandler.builder(
                self.config.app_id,
                self.config.app_secret
            )
            .register_p2_im_message_receive_v1(self._on_message_event)
            .build()
        )
        logger.info("[Feishu] Event handler registered")

        # Create WebSocket client
        logger.info("[Feishu] Creating WebSocket client...")
        self._ws_client = ws.Client(
            self.config.app_id,
            self.config.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )
        logger.info("[Feishu] WebSocket client created")

        # Start event processor
        logger.info("[Feishu] Starting event processor...")
        self._process_task = asyncio.create_task(self._process_events())

        # Start WebSocket in thread pool (blocking call)
        logger.info("[Feishu] Starting WebSocket connection in thread pool...")
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                self._executor,
                self._ws_client.start
            )
            logger.info("[Feishu] WebSocket start() called")
        except Exception as e:
            logger.error(f"[Feishu] Failed to start WebSocket: {e}")
            return

        logger.info("[Feishu] ✓ Feishu channel started successfully")
        logger.info("[Feishu] Waiting for messages...")
        logger.info("=" * 60)

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the Feishu WebSocket connection."""
        self._running = False

        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass

        if self._ws_client:
            logger.info("Stopping Feishu WebSocket...")
            self._ws_client.stop()
            self._ws_client = None

        self._client = None

        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None

        logger.info("Feishu channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Feishu."""
        logger.info("=" * 60)
        logger.info("[Feishu] send() called")
        logger.info(f"[Feishu]   - channel: {msg.channel}")
        logger.info(f"[Feishu]   - chat_id: {msg.chat_id}")
        logger.info(f"[Feishu]   - content: {msg.content[:100]}...")
        logger.info(f"[Feishu]   - metadata: {msg.metadata}")

        if not self._client:
            logger.error("[Feishu] Client not running!")
            return

        try:
            # Prepare message content as JSON
            content_json = json.dumps({"text": msg.content})
            logger.info(f"[Feishu] Content JSON: {content_json}")

            # Get metadata
            chat_type = msg.metadata.get("chat_type", "p2p")
            message_id = msg.metadata.get("message_id")

            logger.info(f"[Feishu] chat_type: {chat_type}, message_id: {message_id}")

            # Use different API based on chat type
            if chat_type == "p2p":
                # P2P: use message.create()
                logger.info("[Feishu] Using P2P message.create() API")
                request = (
                    CreateMessageRequest.builder()
                    .receive_id_type("chat_id")
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(msg.chat_id)
                        .msg_type("text")
                        .content(content_json)
                        .build()
                    )
                    .build()
                )
                logger.info(f"[Feishu] Request built: {request}")

                response = self._client.im.v1.message.create(request)
                logger.info(f"[Feishu] Response received: success={response.success}, code={response.code}, msg={response.msg}")

            else:
                # Group chat: use message.reply()
                logger.info(f"[Feishu] Using group message.reply() API")
                if not message_id:
                    logger.error("[Feishu] message_id required for group chat replies")
                    return

                request = (
                    ReplyMessageRequest.builder()
                    .message_id(message_id)
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .msg_type("text")
                        .content(content_json)
                        .build()
                    )
                    .build()
                )
                logger.info(f"[Feishu] Request built: {request}")

                response = self._client.im.v1.message.reply(request)
                logger.info(f"[Feishu] Response received: success={response.success}, code={response.code}, msg={response.msg}")

            if not response.success():
                logger.error(f"[Feishu] ✗ Failed to send message: {response.code} {response.msg}")
                if response.error:
                    logger.error(f"[Feishu] Error details: {response.error}")
            else:
                logger.info("[Feishu] ✓ Message sent successfully!")

        except Exception as e:
            logger.error(f"[Feishu] Error sending message: {e}", exc_info=True)

        logger.info("=" * 60)

    def _on_message_event(self, data: P2ImMessageReceiveV1) -> None:
        """Sync callback for incoming Feishu events."""
        logger.info("=" * 60)
        logger.info("[Feishu] _on_message_event CALLED!")

        if not self._event_queue:
            logger.error("[Feishu] Event queue not initialized!")
            return

        try:
            # Extract event data from P2ImMessageReceiveV1
            sender = data.event.sender
            message = data.event.message
            sender_id = sender.sender_id.open_id  # Access sender from event, not message
            chat_id = message.chat_id
            message_id = message.message_id
            chat_type = message.chat_type
            msg_type = message.message_type
            create_time = message.create_time

            logger.info(f"[Feishu] Message details:")
            logger.info(f"  - sender_id: {sender_id}")
            logger.info(f"  - chat_id: {chat_id}")
            logger.info(f"  - message_id: {message_id}")
            logger.info(f"  - chat_type: {chat_type}")
            logger.info(f"  - msg_type: {msg_type}")
            logger.info(f"  - create_time: {create_time}")
            logger.info(f"  - raw_content: {message.content}")

            # Parse message content (JSON format)
            content = ""
            if msg_type == "text":
                if message.content:
                    try:
                        content_dict = json.loads(message.content)
                        content = content_dict.get("text", "")
                        logger.info(f"  - parsed_text: {content}")
                    except json.JSONDecodeError as e:
                        logger.warning(f"[Feishu] Failed to parse JSON: {e}")
                        content = message.content
            else:
                logger.warning(f"[Feishu] Unsupported message type: {msg_type}")
                return

            if not content:
                logger.warning("[Feishu] Empty content, skipping")
                return

            logger.info(f"[Feishu] ✓ Putting event in queue...")

            # Put event in queue for async processing
            event_data = {
                "sender_id": sender_id,
                "chat_id": chat_id,
                "content": content,
                "chat_type": chat_type,
                "message_id": message_id,
            }

            future = asyncio.run_coroutine_threadsafe(
                self._event_queue.put(event_data),
                asyncio.get_event_loop()
            )

            # Wait briefly to ensure queue is not full
            try:
                future.result(timeout=1.0)
                logger.info(f"[Feishu] ✓ Event queued successfully")
            except Exception as e:
                logger.error(f"[Feishu] Failed to queue event: {e}")

        except Exception as e:
            logger.error(f"[Feishu] Error handling event: {e}", exc_info=True)

        logger.info("=" * 60)

    async def _process_events(self) -> None:
        """Process events from the queue."""
        logger.info("[Feishu] Event processor started")

        if not self._event_queue:
            logger.error("[Feishu] Event queue not initialized!")
            return

        while self._running:
            try:
                logger.debug("[Feishu] Waiting for event from queue...")
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=1.0
                )

                logger.info("=" * 60)
                logger.info("[Feishu] Processing event from queue:")
                logger.info(f"  - sender_id: {event['sender_id']}")
                logger.info(f"  - chat_id: {event['chat_id']}")
                logger.info(f"  - content: {event['content'][:50]}...")
                logger.info(f"  - chat_type: {event['chat_type']}")
                logger.info(f"  - message_id: {event['message_id']}")

                # Check if sender is allowed
                is_allowed = self.is_allowed(event["sender_id"])
                logger.info(f"[Feishu] Sender allowed: {is_allowed}")
                if not is_allowed:
                    logger.warning(f"[Feishu] Sender {event['sender_id']} not in allow list")
                    continue

                # Publish to message bus
                logger.info("[Feishu] Publishing to message bus...")
                await self._handle_message(
                    sender_id=event["sender_id"],
                    chat_id=event["chat_id"],
                    content=event["content"],
                    metadata={
                        "chat_type": event["chat_type"],
                        "message_id": event["message_id"],
                    }
                )
                logger.info("[Feishu] ✓ Published to message bus")
                logger.info("=" * 60)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                logger.info("[Feishu] Event processor cancelled")
                break
            except Exception as e:
                logger.error(f"[Feishu] Error processing event: {e}", exc_info=True)

        logger.info("[Feishu] Event processor stopped")