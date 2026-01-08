import subprocess
import os
import logging
from shared.classes import IfcConvertRequest
from shared.db_client import save_conversion_result

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_ifcconvert(job_data: dict) -> dict:
    """
    Process an IFC conversion job using the IfcConvert executable.
    
    This function supports ALL IfcConvert command-line arguments from IfcOpenShell 0.8.x,
    including:
    - Command line options (verbose, quiet, cache, threads, etc.)
    - Geometry options (kernel selection, filtering, mesher settings, boolean operations, etc.)
    - Serialization options (SVG-specific, naming conventions, coordinate systems, etc.)
    
    Based on: https://docs.ifcopenshell.org/ifcconvert/usage.html
    
    Args:
        job_data: Dictionary containing the job parameters conforming to IfcConvertRequest.
        
    Returns:
        Dictionary containing the conversion results including:
        - success: Boolean indicating if conversion succeeded
        - message: Status message
        - log_file: Path to the conversion log file
        - stdout: Standard output from IfcConvert
        - stderr: Standard error from IfcConvert (may contain warnings)
        - db_id: Database ID of the saved conversion record (if available)
    """
    logger.info("=" * 80)
    logger.info("Starting IFC conversion job")
    logger.info(f"Job data keys: {list(job_data.keys())}")
    
    try:
        # Parse the request from the job data
        logger.info("Parsing request from job data...")
        request = IfcConvertRequest(**job_data)
        logger.info(f"Request parsed successfully. Input: {request.input_filename}, Output: {request.output_filename}")
        
        # Define paths (assuming they are absolute within the container context)
        input_path = request.input_filename 
        output_path = request.output_filename
        log_file_path = request.log_file
        
        logger.info(f"Processing paths:")
        logger.info(f"  Input path:  {input_path}")
        logger.info(f"  Output path: {output_path}")
        logger.info(f"  Log path:    {log_file_path if log_file_path else '(auto-generate)'}")

        # Generate default log file path if not provided
        if not log_file_path:
            logger.info("Generating default log file path...")
            # Ensure the default output directory exists before creating log path
            default_output_dir = "/output/converted" # Matching original service convention
            os.makedirs(default_output_dir, exist_ok=True) 
            logger.info(f"Created/verified default output directory: {default_output_dir}")
            
            input_basename = os.path.basename(input_path)
            log_filename = f"{os.path.splitext(input_basename)[0]}_convert.txt"
            log_file_path = os.path.join(default_output_dir, log_filename)
            logger.info(f"Generated log file path: {log_file_path}")
        else:
            # Ensure the specified log directory exists
            log_dir = os.path.dirname(log_file_path)
            if log_dir:  # Only create if there's a directory component
                logger.info(f"Creating log directory: {log_dir}")
                os.makedirs(log_dir, exist_ok=True)
            else:
                logger.warning(f"Log file has no directory component: {log_file_path}")

        # Ensure the output directory for the main conversion exists
        output_dir = os.path.dirname(output_path)
        if output_dir:  # Only create if there's a directory component
            logger.info(f"Creating output directory: {output_dir}")
            os.makedirs(output_dir, exist_ok=True)
        else:
            logger.warning(f"Output file has no directory component: {output_path}")

        # Validate input file existence
        logger.info(f"Validating input file exists: {input_path}")
        if not os.path.exists(input_path):
            logger.error(f"Input file not found: {input_path}")
            logger.error(f"Current working directory: {os.getcwd()}")
            logger.error(f"Directory contents: {os.listdir(os.path.dirname(input_path)) if os.path.dirname(input_path) and os.path.exists(os.path.dirname(input_path)) else 'N/A'}")
            raise FileNotFoundError(f"Input file {input_path} not found")
        
        # Log input file size
        input_size = os.path.getsize(input_path)
        logger.info(f"Input file size: {input_size:,} bytes ({input_size / (1024*1024):.2f} MB)")

        # Construct the IfcConvert command
        logger.info("Constructing IfcConvert command...")
        command = ["/usr/local/bin/IfcConvert"]
        
        # Track which options are being used for logging
        enabled_options = []
        
        # Command line options
        if request.verbose:
            command.append("-v")
            enabled_options.append("verbose")
        if request.quiet:
            command.append("-q")
        if request.cache:
            command.append("--cache")
            if request.cache_file:
                command.extend(["--cache-file", request.cache_file])
        if request.stderr_progress:
            command.append("--stderr-progress")
        if request.yes:
            command.append("-y")
        if request.no_progress:
            command.append("--no-progress")
        
        # Log format and file
        if request.log_format:
            command.extend(["--log-format", request.log_format])
        else:
            command.extend(["--log-format", "plain"])
        command.extend(["--log-file", log_file_path])

        # Geometry options - General
        if request.kernel:
            command.extend(["--kernel", request.kernel])
            enabled_options.append(f"kernel={request.kernel}")
        if request.threads:
            command.extend(["-j", str(request.threads)])
            enabled_options.append(f"threads={request.threads}")
        if request.center_model:
            command.append("--center-model")
            enabled_options.append("center_model")
        if request.center_model_geometry:
            command.append("--center-model-geometry")
            enabled_options.append("center_model_geometry")
        
        # Geometry options - Filtering
        # Handle --include with type specification
        if request.include:
            include_type = request.include_type or "entities"
            command.extend(["--include", include_type])
            command.extend(request.include)
            enabled_options.append(f"include={include_type}({len(request.include)} items)")
        
        # Handle --include+ with type specification
        if request.include_plus:
            include_plus_type = request.include_plus_type or "entities"
            # Note: --include+ requires special syntax per IfcConvert docs
            command.append(f"--include+={include_plus_type}")
            command.extend(request.include_plus)
            enabled_options.append(f"include+={include_plus_type}({len(request.include_plus)} items)")
        
        # Handle --exclude with type specification
        if request.exclude:
            exclude_type = request.exclude_type or "entities"
            command.extend(["--exclude", exclude_type])
            command.extend(request.exclude)
            enabled_options.append(f"exclude={exclude_type}({len(request.exclude)} items)")
        
        # Handle --exclude+ with type specification
        if request.exclude_plus:
            exclude_plus_type = request.exclude_plus_type or "entities"
            # Note: --exclude+ requires special syntax per IfcConvert docs
            command.append(f"--exclude+={exclude_plus_type}")
            command.extend(request.exclude_plus)
            enabled_options.append(f"exclude+={exclude_plus_type}({len(request.exclude_plus)} items)")
        
        if request.filter_file:
            command.extend(["--filter-file", request.filter_file])
        
        # Geometry options - Materials and rendering
        if request.default_material_file:
            command.extend(["--default-material-file", request.default_material_file])
        if request.exterior_only:
            command.extend([f"--exterior-only={request.exterior_only}"])
        if request.apply_default_materials:
            command.append("--apply-default-materials")
        if request.use_material_names:
            command.append("--use-material-names")
        if request.surface_colour:
            command.append("--surface-colour")
        
        # Geometry options - Representation types
        if request.plan:
            command.append("--plan")
        if not request.model:
            command.append("--no-model")
        if request.dimensionality is not None:
            command.extend(["--dimensionality", str(request.dimensionality)])
        
        # Geometry options - Mesher settings
        if request.mesher_linear_deflection is not None:
            command.extend(["--mesher-linear-deflection", str(request.mesher_linear_deflection)])
        if request.mesher_angular_deflection is not None:
            command.extend(["--mesher-angular-deflection", str(request.mesher_angular_deflection)])
        if request.reorient_shells:
            command.append("--reorient-shells")
        
        # Geometry options - Units and precision
        if request.length_unit is not None:
            command.extend(["--length-unit", str(request.length_unit)])
        if request.angle_unit is not None:
            command.extend(["--angle-unit", str(request.angle_unit)])
        if request.precision is not None:
            command.extend(["--precision", str(request.precision)])
        if request.precision_factor is not None:
            command.extend(["--precision-factor", str(request.precision_factor)])
        if request.convert_back_units:
            command.append("--convert-back-units")
        
        # Geometry options - Layer and material processing
        if request.layerset_first:
            command.append("--layerset-first")
        if request.enable_layerset_slicing:
            command.append("--enable-layerset-slicing")
        
        # Geometry options - Boolean operations
        if request.disable_boolean_result:
            command.append("--disable-boolean-result")
        if request.disable_opening_subtractions:
            command.append("--disable-opening-subtractions")
        if request.merge_boolean_operands:
            command.append("--merge-boolean-operands")
        if request.boolean_attempt_2d:
            command.append("--boolean-attempt-2d")
        if request.debug:
            command.append("--debug")
        
        # Geometry options - Wire and edge processing
        if request.no_wire_intersection_check:
            command.append("--no-wire-intersection-check")
        if request.no_wire_intersection_tolerance is not None:
            command.extend(["--no-wire-intersection-tolerance", str(request.no_wire_intersection_tolerance)])
        if request.edge_arrows:
            command.append("--edge-arrows")
        
        # Geometry options - Vertex and shape processing
        if request.weld_vertices:
            command.append("--weld-vertices")
        if request.unify_shapes:
            command.append("--unify-shapes")
        
        # Geometry options - Coordinate systems
        if request.use_world_coords:
            command.append("--use-world-coords")
        if request.building_local_placement:
            command.append("--building-local-placement")
        if request.site_local_placement:
            command.append("--site-local-placement")
        if request.model_offset:
            command.extend(["--model-offset", request.model_offset])
        if request.model_rotation:
            command.extend(["--model-rotation", request.model_rotation])
        
        # Geometry options - Context and output
        if request.context_ids:
            for context_id in request.context_ids:
                command.extend(["--context-ids", context_id])
        if request.iterator_output is not None:
            command.extend(["--iterator-output", str(request.iterator_output)])
        
        # Geometry options - Normals and UVs
        if request.no_normals:
            command.append("--no-normals")
        if request.generate_uvs:
            command.append("--generate-uvs")
        
        # Geometry options - Validation and hierarchy
        if request.validate:
            command.append("--validate")
        if request.element_hierarchy:
            command.append("--element-hierarchy")
        
        # Geometry options - Spaces and bounding boxes
        if request.force_space_transparency is not None:
            command.extend(["--force-space-transparency", str(request.force_space_transparency)])
        if request.keep_bounding_boxes:
            command.append("--keep-bounding-boxes")
        
        # Geometry options - CGAL specific
        if request.circle_segments is not None:
            command.extend(["--circle-segments", str(request.circle_segments)])
        
        # Geometry options - Function curves
        if request.function_step_type is not None:
            command.extend(["--function-step-type", str(request.function_step_type)])
        if request.function_step_param is not None:
            command.extend(["--function-step-param", str(request.function_step_param)])
        
        # Geometry options - Performance
        if request.no_parallel_mapping:
            command.append("--no-parallel-mapping")
        if request.sew_shells:
            command.append("--sew-shells")
        
        # Geometry options - Triangulation
        if request.triangulation_type is not None:
            command.extend(["--triangulation-type", str(request.triangulation_type)])
        
        # Serialization options - SVG specific
        if request.bounds:
            command.extend(["--bounds", request.bounds])
        if request.scale:
            command.extend(["--scale", request.scale])
        if request.center:
            command.extend(["--center", request.center])
        if request.section_ref:
            command.extend(["--section-ref", request.section_ref])
        if request.elevation_ref:
            command.extend(["--elevation-ref", request.elevation_ref])
        if request.elevation_ref_guid:
            for guid in request.elevation_ref_guid:
                command.extend(["--elevation-ref-guid", guid])
        if request.auto_section:
            command.append("--auto-section")
        if request.auto_elevation:
            command.append("--auto-elevation")
        if request.draw_storey_heights:
            command.append(f"--draw-storey-heights={request.draw_storey_heights}")
        if request.storey_height_line_length is not None:
            command.extend(["--storey-height-line-length", str(request.storey_height_line_length)])
        if request.svg_xmlns:
            command.append("--svg-xmlns")
        if request.svg_poly:
            command.append("--svg-poly")
        if request.svg_prefilter:
            command.append("--svg-prefilter")
        if request.svg_segment_projection:
            command.append("--svg-segment-projection")
        if request.svg_write_poly:
            command.append("--svg-write-poly")
        if request.svg_project:
            command.append("--svg-project")
        if request.svg_without_storeys:
            command.append("--svg-without-storeys")
        if request.svg_no_css:
            command.append("--svg-no-css")
        if request.door_arcs:
            command.append("--door-arcs")
        if request.section_height is not None:
            command.extend(["--section-height", str(request.section_height)])
        if request.section_height_from_storeys:
            command.append("--section-height-from-storeys")
        if request.print_space_names:
            command.append("--print-space-names")
        if request.print_space_areas:
            command.append("--print-space-areas")
        if request.space_name_transform:
            command.extend(["--space-name-transform", request.space_name_transform])
        
        # Serialization options - Naming conventions
        if request.use_element_names:
            command.append("--use-element-names")
        if request.use_element_guids:
            command.append("--use-element-guids")
        if request.use_element_step_ids:
            command.append("--use-element-step-ids")
        if request.use_element_types:
            command.append("--use-element-types")
        
        # Serialization options - Coordinate system and format
        if request.y_up:
            command.append("--y-up")
        if request.ecef:
            command.append("--ecef")
        
        # Serialization options - Precision
        if request.digits is not None:
            command.extend(["--digits", str(request.digits)])
        
        # Serialization options - RDF/WKT
        if request.base_uri:
            command.extend(["--base-uri", request.base_uri])
        if request.wkt_use_section:
            command.append("--wkt-use-section")
        
        # Input and output files must be last
        command.extend([input_path, output_path])

        # Log enabled options summary
        if enabled_options:
            logger.info(f"Enabled options: {', '.join(enabled_options[:10])}")
            if len(enabled_options) > 10:
                logger.info(f"  ... and {len(enabled_options) - 10} more options")
        else:
            logger.info("Using default options (no custom options specified)")
        
        logger.info(f"Full command ({len(command)} arguments):")
        logger.info(f"  {' '.join(command)}")
        
        # Check if IfcConvert exists
        if not os.path.exists("/usr/local/bin/IfcConvert"):
            logger.error("IfcConvert executable not found at /usr/local/bin/IfcConvert")
            raise FileNotFoundError("IfcConvert executable not found")
        
        # Run the IfcConvert command
        import time
        start_time = time.time()
        logger.info("Executing IfcConvert...")
        
        try:
            result = subprocess.run(
                command, 
                capture_output=True, 
                text=True, 
                check=False,  # check=False to handle errors manually
                timeout=3600  # 1 hour timeout
            )
        except subprocess.TimeoutExpired:
            logger.error("IfcConvert execution timed out after 3600 seconds")
            raise RuntimeError("IfcConvert execution timed out after 1 hour")
        
        execution_time = time.time() - start_time
        logger.info(f"IfcConvert execution completed in {execution_time:.2f} seconds")
        logger.info(f"Return code: {result.returncode}")
        
        # Log stdout if present
        if result.stdout:
            logger.info(f"IfcConvert stdout ({len(result.stdout)} chars):")
            for line in result.stdout.split('\n')[:20]:  # First 20 lines
                if line.strip():
                    logger.info(f"  STDOUT: {line}")
            if len(result.stdout.split('\n')) > 20:
                logger.info(f"  ... (truncated, see log file for full output)")
        
        # Log stderr if present  
        if result.stderr:
            logger.warning(f"IfcConvert stderr ({len(result.stderr)} chars):")
            for line in result.stderr.split('\n')[:20]:  # First 20 lines
                if line.strip():
                    logger.warning(f"  STDERR: {line}")
            if len(result.stderr.split('\n')) > 20:
                logger.warning(f"  ... (truncated, see log file for full output)")
        
        # Check for errors
        if result.returncode != 0:
            error_message = f"IfcConvert failed with return code {result.returncode}. Stderr: {result.stderr}"
            logger.error(error_message)
            # Try reading the log file for more details if it exists
            try:
                with open(log_file_path, 'r') as log_f:
                    log_content = log_f.read()
                logger.error(f"IfcConvert log ({log_file_path}):{log_content}")
                error_message += f"Log content:{log_content}"
            except Exception as log_e:
                logger.error(f"Could not read log file {log_file_path}: {log_e}")
                
            raise RuntimeError(error_message) # Raise for RQ

        # Verify output file was created
        logger.info(f"Verifying output file creation: {output_path}")
        if not os.path.exists(output_path):
            logger.error(f"Output file was not created: {output_path}")
            logger.error(f"Output directory contents: {os.listdir(output_dir) if output_dir and os.path.exists(output_dir) else 'N/A'}")
            raise RuntimeError(f"IfcConvert completed but output file was not created: {output_path}")
        
        # Log output file size
        output_size = os.path.getsize(output_path)
        logger.info(f"Output file created successfully: {output_size:,} bytes ({output_size / (1024*1024):.2f} MB)")
        size_ratio = (output_size / input_size * 100) if input_size > 0 else 0
        logger.info(f"Output/Input size ratio: {size_ratio:.1f}%")

        # Create a dictionary of conversion options for database storage
        # Only include non-default values to keep the dictionary manageable
        logger.info("Preparing conversion options for database storage...")
        conversion_options = request.dict(exclude={'input_filename', 'output_filename'})
        conversion_options["log_file"] = log_file_path  # Add the actual log file path used
        conversion_options["execution_time"] = execution_time
        conversion_options["input_size_bytes"] = input_size
        conversion_options["output_size_bytes"] = output_size
        
        # Save to database
        logger.info("Saving conversion result to database...")
        try:
            db_id = save_conversion_result(
                input_filename=request.input_filename,
                output_filename=output_path,
                conversion_options=conversion_options
            )
            logger.info(f"Saved to database with ID: {db_id}")
        except Exception as db_error:
            logger.error(f"Failed to save to database: {db_error}", exc_info=True)
            db_id = None
            # Don't fail the job if database save fails

        # Success
        logger.info("=" * 80)
        logger.info(f"✓ IFC conversion completed successfully!")
        logger.info(f"  Input:  {input_path} ({input_size:,} bytes)")
        logger.info(f"  Output: {output_path} ({output_size:,} bytes)")
        logger.info(f"  Time:   {execution_time:.2f} seconds")
        logger.info(f"  Log:    {log_file_path}")
        if db_id:
            logger.info(f"  DB ID:  {db_id}")
        logger.info("=" * 80)
        
        result_dict = {
            "success": True,
            "message": f"File converted successfully to {output_path}",
            "log_file": log_file_path,
            "stdout": result.stdout,
            "stderr": result.stderr,  # Might contain warnings even on success
            "execution_time": execution_time,
            "input_size_bytes": input_size,
            "output_size_bytes": output_size
        }
        
        # Add database ID if available
        if db_id:
            result_dict["db_id"] = db_id
            
        return result_dict

    except FileNotFoundError as e:
        logger.error("=" * 80)
        logger.error(f"✗ FILE NOT FOUND ERROR during IFC conversion")
        logger.error(f"  Error: {str(e)}")
        logger.error("=" * 80)
        logger.error("Stack trace:", exc_info=True)
        # Re-raise specific error for clarity in logs/RQ failure
        raise
    
    except subprocess.TimeoutExpired as e:
        logger.error("=" * 80)
        logger.error(f"✗ TIMEOUT ERROR during IFC conversion")
        logger.error(f"  Command timed out after {e.timeout} seconds")
        logger.error("=" * 80)
        raise
    
    except RuntimeError as e:
        logger.error("=" * 80)
        logger.error(f"✗ RUNTIME ERROR during IFC conversion")
        logger.error(f"  Error: {str(e)}")
        logger.error("=" * 80)
        logger.error("Stack trace:", exc_info=True)
        raise
    
    except ValueError as e:
        logger.error("=" * 80)
        logger.error(f"✗ VALIDATION ERROR during IFC conversion")
        logger.error(f"  Error: {str(e)}")
        logger.error(f"  Job data: {job_data}")
        logger.error("=" * 80)
        logger.error("Stack trace:", exc_info=True)
        raise
    
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"✗ UNEXPECTED ERROR during IFC conversion")
        logger.error(f"  Error type: {type(e).__name__}")
        logger.error(f"  Error message: {str(e)}")
        logger.error(f"  Job data keys: {list(job_data.keys()) if isinstance(job_data, dict) else 'N/A'}")
        logger.error("=" * 80)
        logger.error("Full stack trace:", exc_info=True)
        # Re-raise for RQ to mark as failed
        raise 