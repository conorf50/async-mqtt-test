import asyncio
import logging

import aiomqtt

logger = logging.getLogger("mqtt")

# A stalled consumer must fail the run rather than buffer without limit.
MAX_QUEUE_SIZE = 100


class MQTTHandler:
    """Route one client's messages onto one queue per consumer."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[aiomqtt.Message]] = {}
        self._topic_queues: dict[str, list[str]] = {}

    def setup_subscriptions(
        self,
        subscriptions: list[tuple[str, list[str]]],
    ) -> dict[str, asyncio.Queue[aiomqtt.Message]]:
        """Create queues for each (topic filter, [queue names]) pair.

        Each consumer needs its own queue. Queue.get() removes the message,
        so two consumers cannot share one.
        """
        for topic, queue_names in subscriptions:
            if isinstance(queue_names, str) or not queue_names:
                raise TypeError(f"{topic}: queue names must be a non-empty list")
            if topic in self._topic_queues:
                raise ValueError(f"topic filter already registered: {topic}")

            for queue_name in queue_names:
                if queue_name in self._queues:
                    raise ValueError(
                        f"queue {queue_name} already exists; each consumer needs its own"
                    )
                self._queues[queue_name] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

            self._topic_queues[topic] = list(queue_names)

        return dict(self._queues)

    def remove_queue(self, queue_name: str) -> None:
        """Stop routing to a queue whose consumer has finished, so it cannot fill up."""
        del self._queues[queue_name]
        for queue_names in self._topic_queues.values():
            if queue_name in queue_names:
                queue_names.remove(queue_name)

    async def subscribe(self, client: aiomqtt.Client) -> None:
        """Subscribe before the distributor starts, so early messages are queued."""
        for topic in self._topic_queues:
            await client.subscribe(topic)
            logger.info("subscribed to %s", topic)

    async def distribute(self, client: aiomqtt.Client) -> None:
        """Copy each incoming message onto every queue whose filter matches.

        A message can match more than one filter (frequency/# and +/demo both
        match frequency/demo). Every match is delivered.
        """
        async for message in client.messages:
            delivered: list[str] = []
            for pattern, queue_names in self._topic_queues.items():
                if not message.topic.matches(pattern):
                    continue
                for queue_name in queue_names:
                    self._put(queue_name, message)
                    delivered.append(queue_name)
            if delivered:
                logger.debug("routed %s to %s", message.topic, ", ".join(delivered))
            else:
                logger.warning("no queue for topic %s", message.topic)

    def _put(self, queue_name: str, message: aiomqtt.Message) -> None:
        try:
            self._queues[queue_name].put_nowait(message)
        except asyncio.QueueFull as exc:
            raise RuntimeError(
                f"{queue_name} is full ({MAX_QUEUE_SIZE} messages); "
                "its consumer is not keeping up"
            ) from exc


async def next_message(
    queue: asyncio.Queue[aiomqtt.Message],
    timeout: float,
    label: str,
) -> aiomqtt.Message:
    """Wait for one message. Raise TimeoutError if the topic stays quiet.

    The timeout covers only the wait, so slow handling is not a missed publish.
    """
    try:
        async with asyncio.timeout(timeout):
            return await queue.get()
    except TimeoutError:
        raise TimeoutError(f"{label}: no message for {timeout}s") from None
