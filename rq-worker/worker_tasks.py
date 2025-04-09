import requests
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get service URLs from environment variables
IFCCONVERT_URL = os.getenv("IFCCONVERT_URL", "http://ifcconvert")
IFCCSV_URL = os.getenv("IFCCSV_URL", "http://ifccsv")
IFCCLASH_URL = os.getenv("IFCCLASH_URL", "http://ifcclash")
IFCTESTER_URL = os.getenv("IFCTESTER_URL", "http://ifctester")
IFCDIFF_URL = os.getenv("IFCDIFF_URL", "http://ifcdiff")
IFC2JSON_URL = os.getenv("IFC2JSON_URL", "http://ifc2json")
IFC5D_URL = os.getenv("IFC5D_URL", "http://ifc5d")

def call_service(service_url, endpoint, request_data):
    """
    Generic function to call a microservice endpoint
    
    Args:
        service_url (str): The base URL of the service
        endpoint (str): The endpoint to call
        request_data (dict): The data to send in the request
        
    Returns:
        dict: The response from the service
    """
    url = f"{service_url}/{endpoint}"
    logger.info(f"Calling service at {url}")
    
    try:
        response = requests.post(url, json=request_data, timeout=3600)
        response.raise_for_status()
        logger.info(f"Service call to {url} completed successfully")
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling service at {url}: {str(e)}")
        raise

# Define specific task functions for each service
def call_ifcconvert(request_data):
    """Task function for IFC conversion service"""
    return call_service(IFCCONVERT_URL, "ifcconvert", request_data)

def call_ifccsv(request_data):
    """Task function for IFC to CSV service"""
    return call_service(IFCCSV_URL, "ifccsv", request_data)

def call_ifccsv_import(request_data):
    """Task function for CSV to IFC import service"""
    return call_service(IFCCSV_URL, "ifccsv/import", request_data)

def call_ifcclash(request_data):
    """Task function for IFC clash detection service"""
    return call_service(IFCCLASH_URL, "ifcclash", request_data)

def call_ifctester(request_data):
    """Task function for IFC validation service"""
    return call_service(IFCTESTER_URL, "ifctester", request_data)

def call_ifcdiff(request_data):
    """Task function for IFC diff service"""
    return call_service(IFCDIFF_URL, "ifcdiff", request_data)

def call_ifc2json(request_data):
    """Task function for IFC to JSON service"""
    return call_service(IFC2JSON_URL, "ifc2json", request_data)

def call_ifc5d_qtos(request_data):
    """Task function for IFC quantity takeoff service"""
    return call_service(IFC5D_URL, "calculate-qtos", request_data) 