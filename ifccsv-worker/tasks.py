import logging
import os
import ifcopenshell
import ifcopenshell.util.selector
import ifccsv
from shared.classes import IfcCsvRequest, IfcCsvImportRequest

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_ifc_to_csv_conversion(job_data: dict) -> dict:
    """
    Convert an IFC file to CSV/XLSX/ODS format based on the request.
    
    Args:
        job_data: Dictionary containing job parameters conforming to IfcCsvRequest.
        
    Returns:
        Dictionary containing the conversion results.
    """
    try:
        request = IfcCsvRequest(**job_data)
        logger.info(f"Starting IFC to {request.format.upper()} conversion for {request.filename}")

        models_dir = "/uploads" # Standard mount point
        output_dir = f"/output/{request.format}" # Target dir based on format
        file_path = os.path.join(models_dir, request.filename)
        output_path = os.path.join(output_dir, request.output_filename)

        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Validate input file existence
        if not os.path.exists(file_path):
            logger.error(f"Input IFC file not found: {file_path}")
            raise FileNotFoundError(f"Input IFC file {request.filename} not found")

        # Open IFC model
        model = ifcopenshell.open(file_path)
        
        # Filter elements if a query is provided
        if request.query:
            logger.info(f"Filtering elements with query: {request.query}")
            elements = ifcopenshell.util.selector.filter_elements(model, request.query)
        else:
            logger.info("No query provided, processing all applicable elements")
            elements = model.by_type("IfcProduct") # Default to IfcProduct if no query?
            # Consider if a default is needed or if ifccsv handles None elements.
            # If ifccsv requires elements, this might need adjustment.
            
        logger.info(f"Processing {len(elements)} elements with attributes: {request.attributes}")
        
        # Perform conversion using ifccsv library
        ifc_csv_converter = ifccsv.IfcCsv()
        ifc_csv_converter.export(model, elements, request.attributes)

        # Export to the requested format
        logger.info(f"Exporting data to {output_path} in {request.format.upper()} format")
        if request.format == "csv":
            ifc_csv_converter.export_csv(output_path, delimiter=request.delimiter)
        elif request.format == "ods":
            # Ensure dependencies for ODS are installed (usually handled by ifccsv/pandas extras)
            ifc_csv_converter.export_ods(output_path)
        elif request.format == "xlsx":
            # Ensure dependencies for XLSX are installed (openpyxl)
            ifc_csv_converter.export_xlsx(output_path)
        else:
            raise ValueError(f"Unsupported format specified: {request.format}")

        # Prepare result structure (potentially large, consider implications)
        result_data = {
            "headers": ifc_csv_converter.headers,
            "results": ifc_csv_converter.results # This might be very large!
            # Consider if returning the full results is always necessary or just the path?
        }
        
        logger.info(f"Successfully converted {request.filename} to {output_path}")
        return {
            "success": True, 
            "message": f"Successfully converted to {request.format.upper()}",
            "output_path": output_path,
            # "result_data": result_data # Decide whether to include raw data
            }

    except FileNotFoundError as e:
        logger.error(f"File not found error during IFC to CSV conversion: {str(e)}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Error during IFC to CSV conversion: {str(e)}", exc_info=True)
        raise # Re-raise for RQ failure

def run_csv_to_ifc_import(job_data: dict) -> dict:
    """
    Import changes from a CSV/XLSX/ODS file back into an IFC model.
    
    Args:
        job_data: Dictionary containing job parameters conforming to IfcCsvImportRequest.
        
    Returns:
        Dictionary containing the import results.
    """
    try:
        request = IfcCsvImportRequest(**job_data)
        logger.info(f"Starting import from {request.csv_filename} into {request.ifc_filename}")
        
        models_dir = "/uploads" # Source IFC file
        data_input_dir = "/output" # Assuming CSV/XLSX/ODS comes from a previous output
        ifc_output_dir = "/output/ifc_updated" # Separate dir for modified IFCs

        # Construct full paths
        ifc_path = os.path.join(models_dir, request.ifc_filename)
        # Determine data file path (could be csv, xlsx, ods)
        # We might need the format in the request or infer from filename
        data_path = os.path.join(data_input_dir, request.csv_filename)
        
        # Determine output path
        if request.output_filename:
            output_ifc_path = os.path.join(ifc_output_dir, request.output_filename)
        else:
            # Default to overwriting in the output dir with a modified name
            base, ext = os.path.splitext(request.ifc_filename)
            output_ifc_path = os.path.join(ifc_output_dir, f"{base}_updated{ext}")

        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_ifc_path), exist_ok=True)

        # Validate input files existence
        if not os.path.exists(ifc_path):
            logger.error(f"Input IFC file not found: {ifc_path}")
            raise FileNotFoundError(f"Input IFC file {request.ifc_filename} not found")
        if not os.path.exists(data_path):
             logger.error(f"Input data file not found: {data_path}")
             raise FileNotFoundError(f"Input data file {request.csv_filename} not found")

        # Open the IFC model
        logger.info(f"Opening IFC model: {ifc_path}")
        model = ifcopenshell.open(ifc_path)
        
        # Create IfcCsv instance and import changes
        logger.info(f"Importing data from: {data_path}")
        ifc_csv_importer = ifccsv.IfcCsv()
        
        # The ifccsv library's Import method seems to expect a CSV path specifically.
        # We might need to check the file extension or add format info to request.
        # Assuming it can handle different delimiters based on file extension or pandas.
        ifc_csv_importer.Import(model, data_path) # Verify if this handles XLSX/ODS or needs format hint
        
        # Write the updated model
        logger.info(f"Writing updated IFC model to: {output_ifc_path}")
        model.write(output_ifc_path)
        
        logger.info(f"Successfully imported data from {request.csv_filename} into {output_ifc_path}")
        return {
            "success": True,
            "message": "Data changes successfully imported to IFC model",
            "output_path": output_ifc_path
        }
        
    except FileNotFoundError as e:
        logger.error(f"File not found error during CSV to IFC import: {str(e)}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Error importing data changes to IFC: {str(e)}", exc_info=True)
        raise # Re-raise for RQ failure 