import logging
import os
from datetime import datetime

def setup_loggers():
    """Set up multiple loggers with different configurations.
    
    Returns:
        tuple: (root_logger, console_logger, file_logger, mqtt_logger)
    """
    # Create logs directory if it doesn't exist
    os.makedirs('logs', exist_ok=True)
    
    # Create a timestamp for the log filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = f"logs/mqtt_app_{timestamp}.log"
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture everything at the root level
    
    # Clear any existing handlers if the logger already has some
    if root_logger.handlers:
        root_logger.handlers.clear()
    
    # Create formatters
    file_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # File handler with full verbosity
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)  # Capture everything in the file
    file_handler.setFormatter(file_format)
    root_logger.addHandler(file_handler)
    
    # Console handler with warnings and above
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)  # Only show WARNING and above in console
    console_handler.setFormatter(console_format)
    root_logger.addHandler(console_handler)
    
    # Create specialized loggers
    console_logger = logging.getLogger('console')
    console_logger.setLevel(logging.INFO)  # This logger will process INFO and above
    
    file_logger = logging.getLogger('file')
    file_logger.setLevel(logging.DEBUG)  # This logger will process DEBUG and above
    
    # MQTT specific logger
    mqtt_logger = logging.getLogger('mqtt')
    mqtt_logger.setLevel(logging.INFO)  # This logger will process INFO and above
    
    # Add a specific error-only handler to the console for the MQTT logger
    mqtt_console_handler = logging.StreamHandler()
    mqtt_console_handler.setLevel(logging.ERROR)  # Only ERROR and CRITICAL go to console
    mqtt_console_handler.setFormatter(logging.Formatter('[MQTT] %(asctime)s - %(levelname)s - %(message)s'))
    mqtt_logger.addHandler(mqtt_console_handler)
    mqtt_logger.propagate = False  # Don't propagate to root logger to avoid duplicate messages
    
    # Log initialization message
    root_logger.warning(f"Logging initialized. Full logs writing to {log_file}")
    return root_logger, console_logger, file_logger, mqtt_logger
