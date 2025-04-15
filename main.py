import asyncio
import signal
import sys
import logging
import os
from datetime import datetime
from tqdm import tqdm
from mqtt_handler import MQTTHandler

# Configure logging to file and console
def setup_logger():
    """Set up and configure the global logger."""
    # Create logs directory if it doesn't exist
    os.makedirs('logs', exist_ok=True)
    
    # Create a timestamp for the log filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = f"logs/mqtt_app_{timestamp}.log"
    
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(logging.WARNING)
    
    # File handler with timestamp
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_format)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_format)
    
    # Add handlers to the logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    logger.warning(f"Logging initialized. Writing to {log_file}")
    return logger

# Initialize the global logger
logger = setup_logger()

async def timed_status_routine(duration: float, shutdown_event) -> None:
    """Prints the test duration to the console output with a progress bar"""
    end = int(duration)
    
    # Create a progress bar using tqdm
    progress_bar = tqdm(iterable=range(0, end), bar_format="Progress: {percentage:.2f}%  Time Remaining: {remaining} |{bar}|")
    
    try:
        for _ in progress_bar:
            # Check if shutdown is requested before each iteration
            if shutdown_event.is_set():
                progress_bar.write("Progress bar task stopping due to shutdown request")
                break
                
            # Use wait_for with timeout to make the sleep interruptible
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=1)
                progress_bar.write("Progress bar task interrupted during sleep")
                break
            except asyncio.TimeoutError:
                # Timeout just means we continue with the loop
                pass
                
        progress_bar.write("Progress bar task completed")
        
    except asyncio.CancelledError:
        progress_bar.write("Progress bar task was cancelled")
    finally:
        progress_bar.close()

class ApplicationRunner:
    def __init__(self):
        self.mqtt_handler = MQTTHandler()
        self.shutdown_requested = False
        self.mqtt_tasks = {}
        self.additional_tasks = {}
        self.mqtt_stack = None
        
    def setup_signal_handlers(self, loop):
        """Set up signal handlers for graceful shutdown."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.request_shutdown)
            
    def request_shutdown(self):
        """Handle shutdown request from signal."""
        if not self.shutdown_requested:
            self.shutdown_requested = True
            logger.warning("\n\nShutdown initiated. Confirm exit? (y/n)")
            asyncio.create_task(self.get_user_confirmation())
            
    async def get_user_confirmation(self):
        """Get user confirmation for exit."""
        try:
            '''
            The challenge is that sys.stdin.readline() is a blocking operation. 
            In a normal synchronous program, when you call this function, it blocks the entire 
            program until the user enters input and presses Enter. However, in an asyncio-based application:

            Blocking calls would freeze the entire event loop
            This would prevent other asynchronous tasks from running
            It could cause the entire application to become unresponsive

            The solution is to use the run_in_executor function to run the blocking operation
            in it's own thread and collect the response once it's finished. Using await makes the 
            current coroutine pause until the executor completes, but importantly, the event loop 
            continues running other tasks
            '''
            user_choice = await asyncio.get_event_loop().run_in_executor(
                None, sys.stdin.readline
            )
            user_choice = user_choice.strip().lower()
            
            if user_choice.startswith('y'):
                self.mqtt_handler.exit_confirmed = True
                self.mqtt_handler.shutdown_event.set()  # Signal all tasks to shut down
                logger.warning("Exit confirmed. Shutting down...")
            else:
                logger.warning("Shutdown cancelled. Continuing execution.")
                self.shutdown_requested = False
        except Exception as e:
            logger.error(f"Error getting user input: {e}")
            self.mqtt_handler.exit_confirmed = True
            self.mqtt_handler.shutdown_event.set()  # Signal shutdown on error
            
    async def perform_cleanup(self):
        """Dedicated method for cleanup during shutdown."""
        logger.warning("Performing cleanup...")
        
        # Cancel additional tasks first
        for task_name, task in self.additional_tasks.items():
            logger.warning(f"Cancelling additional task: {task_name}")
            task.cancel()
            
        # Wait for additional tasks to complete their cancellation
        if self.additional_tasks:
            await asyncio.gather(*self.additional_tasks.values(), return_exceptions=True)
        
        # Cancel MQTT tasks individually to maintain better control
        for task_name, task in self.mqtt_tasks.items():
            logger.warning(f"Cancelling {task_name} task")
            task.cancel()
        
        # Wait for all MQTT tasks to complete their cancellation
        if self.mqtt_tasks:
            await asyncio.gather(*self.mqtt_tasks.values(), return_exceptions=True)
        
        # Publish goodbye message while client is still available
        if self.mqtt_handler.client:
            try:
                await self.mqtt_handler.publish_message("test/goodbye", "goodbye")
                logger.warning("Goodbye message sent")
            except Exception as e:
                logger.error(f"Error sending goodbye message: {e}")
                
        # Now close the MQTT client by exiting the AsyncExitStack context
        if self.mqtt_stack:
            await self.mqtt_stack.aclose()
            logger.warning("MQTT client closed")
            
        logger.warning("Cleanup completed")
            
    async def run(self):
        """Main execution method."""
        try:
            # Setup the MQTT client
            self.mqtt_stack = await self.mqtt_handler.setup_client()
            
            # Start all MQTT-related tasks
            self.mqtt_tasks = await self.mqtt_handler.start_tasks()
            
            # Start additional tasks - progress bar for 30 seconds
            self.additional_tasks['progress_bar'] = asyncio.create_task(
                timed_status_routine(30, self.mqtt_handler.shutdown_event)
            )
            
            # Wait for shutdown event
            await self.mqtt_handler.shutdown_event.wait()
            
            # If we get here, a shutdown has been triggered
            if self.mqtt_handler.exit_confirmed:
                logger.warning("Shutdown confirmed. Starting cleanup...")
                await self.perform_cleanup()
            else:
                logger.warning("Unexpected exit without confirmation.")
                
        except Exception as e:
            logger.error(f"Error in main execution: {e}")
            await self.perform_cleanup()

def main():
    """Entry point of the application."""
    loop = asyncio.get_event_loop()
    
    logger.warning("Application starting")
    
    runner = ApplicationRunner()
    runner.setup_signal_handlers(loop)
    
    try:
        logger.warning("Entering main event loop")
        loop.run_until_complete(runner.run())
    except KeyboardInterrupt:
        logger.warning("\033[0;33mProgram interrupted\033[0m")
    except Exception as e:
        logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
    finally:
        logger.warning("Closing event loop")
        loop.close()
        logger.warning("Application shutdown complete")

if __name__ == "__main__":
    main()