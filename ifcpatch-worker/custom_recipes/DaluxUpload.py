"""
DaluxUpload Custom Recipe

This recipe uploads IFC files to Dalux Build API using chunked upload.
It splits large files into manageable chunks and uploads them sequentially.

Recipe Name: DaluxUpload
Description: Upload IFC file to Dalux Build with automatic chunking
Author: IFC Pipeline Team
Date: 2025-10-06
Version: 1.0.0
"""

import logging
import os
import subprocess
import tempfile
import shutil
import requests
from urllib.parse import quote
from typing import Optional, Dict, Any
import json
import ifcopenshell
from ifcpatch import BasePatcher

logger = logging.getLogger(__name__)


class Patcher(BasePatcher):
    """
    Custom patcher for uploading IFC files to Dalux Build API.
    
    This recipe:
    1. Splits the source file into chunks (default 50MB)
    2. Creates an upload slot in Dalux
    3. Uploads each chunk with proper headers
    4. Checks if file already exists in folder (smart versioning)
    5. Finalizes the upload (new file or new version)
    6. Cleans up temporary chunk files
    
    Smart Versioning:
    - If a file with the same name exists in the target folder, uploads as a new version
    - If no file exists, creates a new file
    - Prevents "file already exists" errors
    
    Parameters:
        file: The IFC model to upload
        logger: Logger instance for output
        project_id: Dalux project ID (required)
        file_area_id: Dalux file area ID (required)
        folder_id: Dalux folder ID for new file (required)
        api_key: Dalux API authentication key (required)
        properties: JSON string of file properties array (optional, commonly used)
        base_url: Dalux API base URL (default: "https://field.dalux.com/service/api")
        file_type: File type (default: "model")
        chunk_size_mb: Chunk size in MB (default: 10)
    
    Example:
        # Basic upload (minimal)
        patcher = Patcher(
            ifc_file, logger,
            "4566500969",   # project_id
            "48753747979",  # file_area_id
            "15904661523",  # folder_id
            "your-api-key"  # api_key
        )
        patcher.patch()
        
        # With properties (no need to specify defaults!)
        properties_json = '[{"key":"168549787771","values":[{"text":"Systemhandling"}]},{"key":"167595880149","values":[{"text":"Preliminär"}]}]'
        patcher = Patcher(
            ifc_file, logger,
            "4566500969",   # project_id
            "48753747979",  # file_area_id
            "15904661523",  # folder_id
            "your-api-key", # api_key
            properties_json # properties (defaults will be used for base_url, file_type, chunk_size_mb)
        )
        patcher.patch()
        
        # Advanced: Override defaults
        patcher = Patcher(
            ifc_file, logger,
            "4566500969", "48753747979", "15904661523", "your-api-key",
            properties_json,  # properties
            "https://custom.dalux.com/api",  # base_url (custom)
            "drawing",  # file_type (custom)
            "10"       # chunk_size_mb (custom)
        )
        patcher.patch()
    """
    
    def __init__(self, file: ifcopenshell.file, logger: logging.Logger,
                 project_id: str = "",
                 file_area_id: str = "",
                 folder_id: str = "",
                 api_key: str = "",
                 properties: str = "",
                 base_url: str = "https://field.dalux.com/service/api",
                 file_type: str = "model",
                 chunk_size_mb: str = "10"):
        """
        Initialize the DaluxUpload patcher.
        
        Args:
            file: IFC file to upload
            logger: Logger instance
            project_id: Dalux project ID (required)
            file_area_id: Dalux file area ID (required)
            folder_id: Dalux folder ID (required)
            api_key: Dalux API key (required)
            properties: JSON string of properties array (default: "")
            base_url: API base URL (default: "https://field.dalux.com/service/api")
            file_type: File type (default: "model")
            chunk_size_mb: Chunk size in MB (default: "50")
        """
        super().__init__(file, logger)
        
        # Required parameters
        if not project_id or not file_area_id or not folder_id or not api_key:
            raise ValueError(
                "Missing required parameters. Need: project_id, file_area_id, folder_id, api_key"
            )
        
        self.project_id = project_id
        self.file_area_id = file_area_id
        self.folder_id = folder_id
        self.api_key = api_key
        
        # Optional parameters
        self.base_url = base_url if base_url else "https://field.dalux.com/service/api"
        self.file_type = file_type if file_type else "model"
        self.chunk_size_mb = int(chunk_size_mb) if chunk_size_mb else 10
        
        # Parse properties JSON if provided
        import json
        self.properties = []
        if properties:
            try:
                self.properties = json.loads(properties)
                # Ensure each property has a "key" field (use "name" as key if missing)
                for prop in self.properties:
                    if 'key' not in prop and 'name' in prop:
                        prop['key'] = prop['name']
                self.logger.info(f"Loaded {len(self.properties)} custom properties")
            except Exception as e:
                self.logger.warning(f"Failed to parse properties JSON: {str(e)}")
        
        # Validate parameters
        if not self.project_id:
            raise ValueError("project_id is required")
        if not self.file_area_id:
            raise ValueError("file_area_id is required")
        if not self.folder_id:
            raise ValueError("folder_id is required")
        if not self.api_key:
            raise ValueError("api_key is required")
        if self.chunk_size_mb <= 0 or self.chunk_size_mb > 100:
            raise ValueError("chunk_size_mb must be between 1 and 100")
        
        # State
        self.temp_dir: Optional[str] = None
        self.upload_guid: Optional[str] = None
        self.file_path: Optional[str] = None
        self.file_name: Optional[str] = None
        
        self.logger.info(f"Initialized DaluxUpload recipe:")
        self.logger.info(f"  Project ID: {self.project_id}")
        self.logger.info(f"  File Area ID: {self.file_area_id}")
        self.logger.info(f"  Folder ID: {self.folder_id}")
        self.logger.info(f"  Base URL: {self.base_url}")
        self.logger.info(f"  File Type: {self.file_type}")
        self.logger.info(f"  Chunk Size: {self.chunk_size_mb}MB")
    
    def patch(self) -> None:
        """
        Execute the upload process.
        """
        self.logger.info("Starting DaluxUpload operation")
        
        try:
            # Step 1: Setup file paths
            self.file_path = self.file._input_file_path
            self.file_name = os.path.basename(self.file_path)
            self.temp_dir = tempfile.mkdtemp(prefix="dalux-upload-chunks-")
            
            file_size = os.path.getsize(self.file_path)
            self.logger.info(f"File: {self.file_name} ({file_size:,} bytes)")
            
            # Step 2: Split file into chunks
            self.logger.info("Step 1: Splitting file into chunks...")
            chunk_files = self._split_file()
            self.logger.info(f"Created {len(chunk_files)} chunks")
            
            # Step 3: Create upload slot
            self.logger.info("Step 2: Creating upload slot in Dalux...")
            self._create_upload_slot()
            self.logger.info(f"Upload slot created with GUID: {self.upload_guid}")
            
            # Step 4: Upload chunks
            self.logger.info("Step 3: Uploading chunks...")
            self._upload_chunks(chunk_files)
            
            # Step 5: Finalize upload
            self.logger.info("Step 4: Finalizing upload...")
            response = self._finalize_upload()
            
            # Log success
            file_id = response.get('data', {}).get('fileId', 'unknown')
            self.logger.info(f"Upload completed successfully!")
            self.logger.info(f"File ID: {file_id}")
            self.logger.info(f"File Name: {self.file_name}")
            
        except Exception as e:
            self.logger.error(f"Error during DaluxUpload: {str(e)}", exc_info=True)
            raise
        finally:
            # Cleanup
            self._cleanup()
    
    def _split_file(self) -> list:
        """Split the source file into chunks using the split command."""
        try:
            # Use temp dir directly for chunks
            chunk_prefix = os.path.join(self.temp_dir, f"{self.file_name}.chunk.")
            chunk_size = f"{self.chunk_size_mb}m"
            
            # Run split command on the original file
            cmd = [
                "split",
                "-b", chunk_size,
                self.file_path,
                chunk_prefix
            ]
            
            self.logger.debug(f"Running command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            # List chunk files
            chunk_files = sorted([
                f for f in os.listdir(self.temp_dir)
                if f.startswith(f"{self.file_name}.chunk.")
            ])
            
            if not chunk_files:
                raise RuntimeError("No chunk files were created")
            
            self.logger.info(f"Split file into {len(chunk_files)} chunks in {self.temp_dir}")
            
            return chunk_files
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to split file: {e.stderr}")
            raise RuntimeError(f"File splitting failed: {e.stderr}")
        except Exception as e:
            self.logger.error(f"Error splitting file: {str(e)}")
            raise
    
    def _create_upload_slot(self) -> None:
        """Create an upload slot in Dalux."""
        url = f"{self.base_url}/1.0/projects/{self.project_id}/file_areas/{self.file_area_id}/upload"
        
        # Dalux uses X-API-KEY header, not Authorization
        headers = {
            "X-API-KEY": self.api_key,
            "Accept": "application/json"
        }
        
        # Debug logging (mask API key for security)
        masked_key = self.api_key[:8] + "..." if len(self.api_key) > 8 else "***"
        self.logger.debug(f"Creating upload slot at: {url}")
        self.logger.debug(f"Using API key: {masked_key}")
        self.logger.debug(f"Headers: {dict(headers)}")
        
        try:
            response = requests.post(url, headers=headers, timeout=30)
            
            # Log response details before raising
            self.logger.debug(f"Response status: {response.status_code}")
            self.logger.debug(f"Response headers: {dict(response.headers)}")
            
            response.raise_for_status()
            
            data = response.json()
            self.upload_guid = data.get('data', {}).get('uploadGuid')
            
            if not self.upload_guid:
                raise RuntimeError("No uploadGuid received from Dalux")
            
        except requests.exceptions.HTTPError as e:
            self.logger.error(f"HTTP Error: {e}")
            if hasattr(e.response, 'text'):
                self.logger.error(f"Response body: {e.response.text}")
            raise RuntimeError(f"Upload slot creation failed: {str(e)}")
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Failed to create upload slot: {str(e)}")
            raise RuntimeError(f"Upload slot creation failed: {str(e)}")
    
    def _upload_chunks(self, chunk_files: list) -> None:
        """Upload each chunk to Dalux."""
        file_size = os.path.getsize(self.file_path)
        chunk_size_bytes = self.chunk_size_mb * 1024 * 1024
        file_name_encoded = quote(self.file_name)
        
        for idx, chunk_file in enumerate(chunk_files):
            chunk_path = os.path.join(self.temp_dir, chunk_file)
            chunk_file_size = os.path.getsize(chunk_path)
            
            # Calculate byte range
            start = idx * chunk_size_bytes
            end = min(start + chunk_size_bytes, file_size) - 1
            
            self.logger.info(f"Uploading chunk {idx + 1}/{len(chunk_files)}: {chunk_file} ({chunk_file_size:,} bytes)")
            
            # Prepare request
            url = f"{self.base_url}/1.0/projects/{self.project_id}/file_areas/{self.file_area_id}/upload/{self.upload_guid}"
            
            # Dalux uses X-API-KEY header
            headers = {
                "X-API-KEY": self.api_key,
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Disposition": f"form-data; filename='{self.file_name}'; filename*=UTF-8''{file_name_encoded}"
            }
            
            try:
                # Upload chunk
                with open(chunk_path, 'rb') as f:
                    response = requests.post(url, headers=headers, data=f, timeout=300)
                    
                    # Expected 202 Accepted
                    if response.status_code != 202:
                        self.logger.warning(f"Unexpected status code: {response.status_code}")
                        if hasattr(response, 'text'):
                            self.logger.warning(f"Response: {response.text}")
                    
                    response.raise_for_status()
                    
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Failed to upload chunk: {str(e)}")
                raise RuntimeError(f"Chunk upload failed: {str(e)}")
            
        self.logger.info(f"All {len(chunk_files)} chunks uploaded successfully")
    
    def _check_existing_file(self) -> Optional[str]:
        """
        Check if a file with the same name already exists in the folder.
        Returns the fileId if it exists, None otherwise.
        """
        url = f"{self.base_url}/1.0/projects/{self.project_id}/file_areas/{self.file_area_id}/files"
        headers = {
            "X-API-KEY": self.api_key,
            "Accept": "application/json"
        }
        
        try:
            self.logger.info(f"Checking if file '{self.file_name}' already exists in folder {self.folder_id}...")
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            # Search for file with matching name and folder
            if "items" in data:
                for item in data.get("items", []):
                    file_data = item.get("data", {})
                    if (file_data.get("fileName") == self.file_name and 
                        file_data.get("folderId") == self.folder_id):
                        file_id = file_data.get("fileId")
                        self.logger.info(f"File already exists with ID: {file_id}. Will upload as new version.")
                        return file_id
            
            self.logger.info("File does not exist yet. Will create new file.")
            return None
            
        except Exception as e:
            self.logger.warning(f"Error checking for existing file: {e}")
            self.logger.warning("Proceeding with new file upload (using folderId)")
            return None
    
    def _finalize_upload(self) -> Dict[str, Any]:
        """Finalize the upload in Dalux."""
        url = f"{self.base_url}/2.0/projects/{self.project_id}/file_areas/{self.file_area_id}/upload/{self.upload_guid}/finalize"
        
        # Dalux uses X-API-KEY header
        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        # Check if file already exists
        existing_file_id = self._check_existing_file()
        
        # Build request body - use fileId if file exists, folderId if new
        body = {
            "fileName": self.file_name,
            "fileType": self.file_type
        }
        
        if existing_file_id:
            # Upload as new version of existing file
            body["fileId"] = existing_file_id
            self.logger.info(f"Uploading as new version (fileId: {existing_file_id})")
        else:
            # Upload as new file in folder
            body["folderId"] = self.folder_id
            self.logger.info(f"Uploading as new file (folderId: {self.folder_id})")
        
        # Add properties if provided
        if self.properties:
            body["properties"] = self.properties
            self.logger.info(f"Including {len(self.properties)} properties in finalize request")
        
        import json
        self.logger.info(f"Finalize body: {json.dumps(body, indent=2)}")
        
        try:
            response = requests.post(url, headers=headers, json=body, timeout=30)
            
            # Log response details before raising
            self.logger.info(f"Finalize response status: {response.status_code}")
            self.logger.info(f"Finalize response body: {response.text}")
            
            # Expected 201 Created
            if response.status_code != 201:
                self.logger.warning(f"Unexpected status code: {response.status_code}")
                self.logger.warning(f"Response body: {response.text}")
            
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            self.logger.error(f"HTTP Error during finalize: {e}")
            if hasattr(e.response, 'text'):
                self.logger.error(f"Response body: {e.response.text}")
            
            # If we got a 500 error and we had properties, try again without them
            if e.response.status_code == 500 and self.properties:
                self.logger.warning("Got 500 error with properties. Retrying without properties...")
                try:
                    retry_body = {
                        "folderId": self.folder_id,
                        "fileName": self.file_name,
                        "fileType": self.file_type
                    }
                    self.logger.info(f"Retry finalize body (without properties): {json.dumps(retry_body, indent=2)}")
                    retry_response = requests.post(url, headers=headers, json=retry_body, timeout=30)
                    
                    self.logger.info(f"Retry response status: {retry_response.status_code}")
                    self.logger.info(f"Retry response body: {retry_response.text}")
                    
                    retry_response.raise_for_status()
                    
                    self.logger.warning("Upload succeeded without properties! Properties may not be configured in Dalux.")
                    return retry_response.json()
                except Exception as retry_error:
                    self.logger.error(f"Retry also failed: {retry_error}")
            
            raise RuntimeError(f"Upload finalization failed: {str(e)}")
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Failed to finalize upload: {str(e)}")
            raise RuntimeError(f"Upload finalization failed: {str(e)}")
    
    def _cleanup(self) -> None:
        """Clean up temporary files."""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
                self.logger.info(f"Cleaned up temporary directory: {self.temp_dir}")
            except Exception as e:
                self.logger.warning(f"Failed to cleanup temp directory: {str(e)}")
    
    def get_output(self) -> ifcopenshell.file:
        """
        Return the original IFC file.
        
        Note: This recipe only uploads the file, it doesn't modify it.
        
        Returns:
            The original IFC file object
        """
        return self.file

