#!/usr/bin/env python3
"""
This is a simple entrypoint script for RQ that makes the perform_ifc_diff function
directly available in the global namespace.
"""
import os
import sys
import logging

# Configure logging
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

# Import the actual worker function
from worker import perform_ifc_diff

# Log information to verify imports
logger.info("Worker entrypoint script loaded")
logger.info(f"perform_ifc_diff function is available: {perform_ifc_diff.__name__}")

# The function now exists in the global namespace where RQ can find it
if __name__ == "__main__":
    logger.info("Worker entrypoint script executed directly - this should not happen") 