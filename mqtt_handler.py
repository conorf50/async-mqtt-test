import asyncio
from contextlib import AsyncExitStack
from aiomqtt import Client
import time
import logging

# Get the root logger
logger = logging.getLogger()

# Configuration
MQTT_CONFIG = {
    'host': "192.168.63.25",
    'port': 1883,
    'max_retries': 3,
    'timeout': 3  # seconds
}

class MQTTHandler:
    def __init__(self):
        self.shutdown_requested = False
        self.exit_confirmed = False
        self.client = None
        self.shutdown_event = asyncio.Event()
        
        # Topic configurations
        self.topics = [
            {"name": "frequency./#", "max_retries": MQTT_CONFIG['max_retries']},
            {"name": "simulator/status", "max_retries": MQTT_CONFIG['max_retries']}
        ]
        
        # Initialize tracking dictionaries for topics
        self.topic_last_message = {}
        for topic_config in self.topics:
            self.topic_last_message[topic_config["name"]] = time.time()

    async def timed_publish(self):
        """Publish a value to the test topic."""
        try:
            logger.warning("\033[0;34mStarting 15-second timed task\033[0m.....")
            for i in range(1, 15):
                # Check if shutdown is requested before each iteration
                if self.shutdown_event.is_set():
                    logger.warning("Timed task stopping due to shutdown request")
                    break
                    
                msg = f"Counter : {i}"
                await self.publish_message("test/hello", msg)
                
                # Use wait_for with timeout to make the sleep interruptible
                try:
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=1)
                    logger.warning("Timed task interrupted during sleep")
                    break
                except asyncio.TimeoutError:
                    # Timeout just means we continue with the loop
                    pass
                    
            logger.warning("Timed task completed")
            self.exit_confirmed = True
            self.shutdown_event.set()  # Signal all tasks to shut down
            
        except asyncio.CancelledError:
            logger.warning("Timed task was cancelled")
        except Exception as e:
            logger.error(f"\033[0;31mError in timed task: {e}\033[0m")            

    async def setup_mqtt_client(self):
        """Create and configure MQTT client."""
        try:
            self.client = Client(
                hostname=MQTT_CONFIG['host'], 
                port=MQTT_CONFIG['port']
            )
            return self.client
        except Exception as e:
            logger.error(f"Failed to setup MQTT client: {e}")
            raise

    async def publish_message(self, topic, message):
        """Publish a message to a specific topic."""
        try:
            await self.client.publish(topic, message)
            logger.warning(f"Published '{message}' to {topic}")
        except Exception as e:
            logger.error(f"Failed to publish to {topic}: {e}")

    async def message_processor(self):
        """Process incoming messages and update last message times."""
        # Subscribe to all topics
        for topic_config in self.topics:
            topic = topic_config["name"]
            await self.client.subscribe(topic)
            logger.warning(f"Subscribed to {topic}")
        
        # Process incoming messages
        try:
            async for message in self.client.messages:
                if self.shutdown_event.is_set():
                    break
                    
                # Update last message time for matching topics
                for topic in self.topic_last_message.keys():
                    if message.topic.matches(topic):
                        self.topic_last_message[topic] = time.time()
                        logger.warning(f"Received message on {topic}: {message.payload}")
                        break
        except asyncio.CancelledError:
            logger.warning("Message processor cancelled")

    async def monitor_missing_messages(self):
        """Monitor for missing messages on all topics."""
        topic_retries = {topic: 0 for topic in self.topic_last_message.keys()}
        last_logged_retry = {topic: 0 for topic in self.topic_last_message.keys()}
        
        try:
            while not self.shutdown_event.is_set():
                current_time = time.time()
                
                # Check each topic for missing messages
                for topic_config in self.topics:
                    topic = topic_config["name"]
                    max_retries = topic_config["max_retries"]
                    
                    # Calculate time since last message
                    time_since_last = current_time - self.topic_last_message[topic]
                    
                    # Check if we've exceeded the timeout
                    if time_since_last > MQTT_CONFIG['timeout']:
                        topic_retries[topic] += 1
                        
                        # Log warning if this is a new retry count
                        if topic_retries[topic] > last_logged_retry[topic]:
                            logger.warning(
                                f"No message on '{topic}' for {time_since_last:.1f} seconds. "
                                f"Retry {topic_retries[topic]}/{max_retries}"
                            )
                            last_logged_retry[topic] = topic_retries[topic]
                            await asyncio.sleep(1)
                        
                        # Initiate shutdown if max retries reached
                        if topic_retries[topic] >= max_retries:
                            logger.error(f"Max retries reached for {topic}. Initiating shutdown.")
                            self.exit_confirmed = True
                            self.shutdown_event.set()
                            return
                    else:
                        # Reset retry counter if we're within timeout
                        if topic_retries[topic] > 0:
                            topic_retries[topic] = 0
                            last_logged_retry[topic] = 0
                
                # Short sleep to avoid CPU spinning
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.warning("Message monitor cancelled")

    async def setup_client(self):
        """Setup the MQTT client and return the AsyncExitStack context manager."""
        stack = AsyncExitStack()
        client = await self.setup_mqtt_client()
        await stack.enter_async_context(client)
        return stack
            
    async def start_tasks(self):
        """Start all MQTT-related tasks and return a dictionary of task references."""
        tasks = {}
        
        # Start the message processor
        tasks['message_processor'] = asyncio.create_task(self.message_processor())
        
        # Start the missing message monitor
        tasks['monitor'] = asyncio.create_task(self.monitor_missing_messages())
        
        # Start timed task
        tasks['timed_publisher'] = asyncio.create_task(self.timed_publish())
        
        # Send initial hello message
        await self.publish_message("test/hello", "hello world")
        
        return tasks