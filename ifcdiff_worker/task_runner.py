#!/usr/bin/env python3
import os
import sys
import json
import logging
import time
import redis
from rq import Queue, Worker, Connection
from worker import perform_ifc_diff

# Configure logging
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("task_runner")

# Connect to Redis
redis_host = os.getenv('REDIS_HOST', 'redis')
redis_port = 6379
redis_conn = redis.Redis(host=redis_host, port=redis_port)
queue_name = "ifcdiff-tasks"

def poll_for_tasks():
    """Poll Redis for tasks and execute them directly"""
    logger.info(f"Starting task polling on queue '{queue_name}'")
    
    while True:
        try:
            # Check if there are any jobs in the queue
            queue_key = f"rq:queue:{queue_name}"
            job_ids = redis_conn.lrange(queue_key, 0, 0)  # Get the first job
            
            if job_ids:
                job_id = job_ids[0].decode('utf-8')
                logger.info(f"Found job {job_id} in queue")
                
                # Get job data
                job_data_key = f"rq:job:{job_id}"
                job_data = redis_conn.hgetall(job_data_key)
                
                if not job_data:
                    logger.warning(f"No data found for job {job_id}, skipping")
                    redis_conn.lrem(queue_key, 0, job_ids[0])  # Remove from queue
                    continue
                
                # Mark job as started
                redis_conn.hset(job_data_key, "status", "started")
                redis_conn.lrem(queue_key, 0, job_ids[0])  # Remove from queue
                
                # Get arguments
                args_data = job_data.get(b'args', b'[]')
                kwargs_data = job_data.get(b'kwargs', b'{}')
                
                args = json.loads(args_data)
                kwargs = json.loads(kwargs_data)
                
                logger.info(f"Executing job {job_id} with args: {args}")
                
                try:
                    # Actually call the function
                    result = perform_ifc_diff(*args, **kwargs)
                    
                    # Save result
                    redis_conn.hset(job_data_key, "result", json.dumps(result))
                    redis_conn.hset(job_data_key, "status", "finished")
                    logger.info(f"Job {job_id} completed with result: {result}")
                    
                except Exception as e:
                    logger.error(f"Error executing job {job_id}: {str(e)}", exc_info=True)
                    redis_conn.hset(job_data_key, "status", "failed")
                    redis_conn.hset(job_data_key, "exc_info", str(e))
            
            # Short sleep to avoid hammering Redis
            time.sleep(1)
            
        except Exception as e:
            logger.error(f"Error in polling loop: {str(e)}", exc_info=True)
            time.sleep(5)  # Longer sleep on error

if __name__ == "__main__":
    logger.info("Task runner starting")
    poll_for_tasks() 