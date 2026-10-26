import asyncio
import json
import logging
import signal
import sys

import aiomqtt

from log_handler import setup_loggers
from mqtt_handler import MQTTHandler, next_message

logger = logging.getLogger("app")
console_logger = logging.getLogger("console")
file_logger = logging.getLogger("file")

BROKER_HOST = "127.0.0.1"
BROKER_PORT = 1883
CLIENT_ID = "async-mqtt-test"

HELLO_TOPIC = "test/hello"
GOODBYE_TOPIC = "test/goodbye"

# topic filter -> one queue name per consumer of that topic.
# frequency has three consumers, so it has three queues.
SUBSCRIPTIONS: list[tuple[str, list[str]]] = [
    ("frequency/#", ["frequency_log", "frequency_watch", "frequency_once"]),
    ("snapshot/#", ["snapshot"]),
]

# Seconds of silence before that consumer ends the run.
CONSUMER_TIMEOUTS = {
    "frequency_log": 5,
    "frequency_watch": 3,
    "snapshot": 10,
}
FREQUENCY_ONCE_TIMEOUT = 5

# The publisher stands in for the broker's real data and the test sequence.
# Drop it when a live broker is already publishing these topics.
TEST_DURATION_SECONDS = 15
PUBLISH_INTERVAL_SECONDS = 1
# Set to e.g. 3 to stop publishing snapshot early and watch its consumer end the run.
STOP_SNAPSHOT_AFTER_SECONDS: float | None = None


class ShutdownRequested(Exception):
    """Raised inside the TaskGroup so it cancels every other task."""


class ExitPrompt:
    """Ctrl+C asks for y/n before shutting down. SIGTERM shuts down straight away."""

    def __init__(self, shutdown_event: asyncio.Event) -> None:
        self._shutdown_event = shutdown_event
        self._loop = asyncio.get_running_loop()
        self._waiting_for_answer = False

    def install(self) -> None:
        self._loop.add_signal_handler(signal.SIGINT, self._on_sigint)
        self._loop.add_signal_handler(signal.SIGTERM, self._shut_down, "SIGTERM")

    def uninstall(self) -> None:
        self._loop.remove_signal_handler(signal.SIGINT)
        self._loop.remove_signal_handler(signal.SIGTERM)
        if self._waiting_for_answer:
            self._loop.remove_reader(sys.stdin)

    def _on_sigint(self) -> None:
        if self._waiting_for_answer:
            return
        if not sys.stdin.isatty():
            self._shut_down("SIGINT, no terminal to confirm on")
            return
        self._waiting_for_answer = True
        logger.warning("Shutdown requested. Confirm exit? (y/n)")
        # The loop calls _on_answer once a line is typed. Nothing blocks while waiting.
        self._loop.add_reader(sys.stdin, self._on_answer)

    def _on_answer(self) -> None:
        self._loop.remove_reader(sys.stdin)
        self._waiting_for_answer = False
        answer = sys.stdin.readline().strip().lower()
        if answer.startswith("y"):
            self._shut_down("exit confirmed")
        else:
            logger.warning("Shutdown cancelled. Continuing.")

    def _shut_down(self, reason: str) -> None:
        logger.warning("Shutting down: %s", reason)
        self._shutdown_event.set()


def payload_text(payload: object) -> str:
    if isinstance(payload, (bytes, bytearray)):
        return payload.decode("utf-8", errors="replace")
    return str(payload)


async def consume(
    label: str,
    queue: asyncio.Queue[aiomqtt.Message],
    timeout: float,
) -> None:
    """Read until the topic goes quiet, then raise TimeoutError."""
    while True:
        message = await next_message(queue, timeout, label)
        file_logger.info("%s %s %s", label, message.topic, payload_text(message.payload))


async def consume_one(
    label: str,
    handler: MQTTHandler,
    queue: asyncio.Queue[aiomqtt.Message],
    timeout: float,
) -> None:
    """Read a single message, then return. Same timeout as a long-running consumer."""
    message = await next_message(queue, timeout, label)
    handler.remove_queue(label)
    console_logger.info("%s first message %s %s", label, message.topic, payload_text(message.payload))


async def publish_demo(client: aiomqtt.Client, shutdown_event: asyncio.Event) -> None:
    """Publish to the subscribed topics for the test duration, then end the run."""
    loop = asyncio.get_running_loop()
    start = loop.time()
    count = 0
    while loop.time() - start < TEST_DURATION_SECONDS:
        count += 1
        await client.publish("frequency/demo", json.dumps({"v": 50.0}))
        elapsed = loop.time() - start
        if STOP_SNAPSHOT_AFTER_SECONDS is None or elapsed < STOP_SNAPSHOT_AFTER_SECONDS:
            await client.publish("snapshot/demo", json.dumps({"n": count}))
        console_logger.info("published sample %s", count)
        await asyncio.sleep(PUBLISH_INTERVAL_SECONDS)
    logger.warning("Test duration of %ss reached", TEST_DURATION_SECONDS)
    shutdown_event.set()


async def wait_for_shutdown(shutdown_event: asyncio.Event) -> None:
    await shutdown_event.wait()
    raise ShutdownRequested


async def run_session(shutdown_event: asyncio.Event) -> None:
    # Client.__init__ reads the running loop, so it has to be built in here.
    async with aiomqtt.Client(
        hostname=BROKER_HOST,
        port=BROKER_PORT,
        identifier=CLIENT_ID,
    ) as client:
        handler = MQTTHandler()
        queues = handler.setup_subscriptions(SUBSCRIPTIONS)
        await handler.subscribe(client)
        await client.publish(HELLO_TOPIC, "hello world")

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(handler.distribute(client), name="distributor")
                tg.create_task(publish_demo(client, shutdown_event), name="publisher")
                tg.create_task(wait_for_shutdown(shutdown_event), name="shutdown")
                tg.create_task(
                    consume_one(
                        "frequency_once",
                        handler,
                        queues["frequency_once"],
                        FREQUENCY_ONCE_TIMEOUT,
                    ),
                    name="frequency_once",
                )
                for name, timeout in CONSUMER_TIMEOUTS.items():
                    tg.create_task(consume(name, queues[name], timeout), name=name)
        finally:
            # Still inside `async with client`, so the connection is open unless it was lost.
            try:
                await client.publish(GOODBYE_TOPIC, "goodbye")
                logger.warning("Goodbye message sent")
            except aiomqtt.MqttError as error:
                logger.error("Could not send goodbye message: %s", error)


async def run() -> int:
    """Return the process exit code: 0 for a requested shutdown, 1 for a failure."""
    shutdown_event = asyncio.Event()
    exit_prompt = ExitPrompt(shutdown_event)
    exit_prompt.install()
    exit_code = 0
    try:
        await run_session(shutdown_event)
    # TaskGroup raises an ExceptionGroup. Each except* handles its own type from it.
    except* ShutdownRequested:
        logger.warning("Shutdown complete")
    except* TimeoutError as group:
        for error in group.exceptions:
            logger.error("%s", error)
        exit_code = 1
    except* aiomqtt.MqttError as group:
        for error in group.exceptions:
            logger.error("MQTT error: %s", error)
        exit_code = 1
    finally:
        exit_prompt.uninstall()
    return exit_code


def main() -> None:
    setup_loggers()
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
