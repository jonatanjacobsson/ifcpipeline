from fastapi import FastAPI, HTTPException, Depends
from shared.classes import IfcClashRequest, ClashSet, ClashFile, ClashMode
from ifcclash.ifcclash import Clasher, ClashSettings
import logging
import json
import os
import time

app = FastAPI()

# Add this at the beginning of your file
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CustomClashSettings(ClashSettings):
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)

class CustomClasher(Clasher):
    def __init__(self, settings):
        super().__init__(settings)
        self.logger = logging.getLogger(__name__)
        if not hasattr(self.settings, 'logger') or self.settings.logger is None:
            self.settings.logger = self.logger

@app.post("/ifcclash", summary="Perform Clash Detection", tags=["Analysis"])
async def api_ifcclash(request: IfcClashRequest):
    """
    Perform clash detection on IFC models.
    
    Args:
        request (IfcClashRequest): The request body containing clash detection parameters.
    
    Returns:
        dict: A dictionary containing the clash detection results and success status.
    """
    models_dir = "/app/uploads"
    output_dir = "/app/output/clash"
    output_path = os.path.join(output_dir, request.output_filename)

    logger.info(f"Starting clash detection for {len(request.clash_sets)} clash sets")

    # Validate that all specified files exist
    for clash_set in request.clash_sets:
        for file in clash_set.a + clash_set.b:
            file_path = os.path.join(models_dir, file.file)
            if not os.path.exists(file_path):
                logger.error(f"File not found: {file_path}")
                raise HTTPException(status_code=404, detail=f"File {file.file} not found")

    try:
        settings = CustomClashSettings()  # Use CustomClashSettings instead of ClashSettings
        settings.output = output_path

        logger.info(f"Clash output will be saved to: {output_path}")

        clasher = CustomClasher(settings)  # Use CustomClasher instead of Clasher

        for clash_set in request.clash_sets:
            clasher_set = {
                "name": clash_set.name,
                "a": [],
                "b": [],
                "tolerance": request.tolerance,
                "mode": request.mode.value,
                "check_all": request.check_all,
                "allow_touching": request.allow_touching,
                "clearance": request.clearance
            }

            logger.info(f"Setting up clash set '{clash_set.name}' with mode: {request.mode.value}")

            # Validate mode-specific parameters
            if request.mode == ClashMode.CLEARANCE and request.clearance <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Clearance value must be greater than 0 when using clearance mode"
                )

            for side in ['a', 'b']:
                for file in getattr(clash_set, side):
                    file_path = os.path.join(models_dir, file.file)
                    logger.info(f"Adding file to clash set: {file_path}")
                    clasher_set[side].append({
                        "file": file_path,
                        "mode": file.mode,
                        "selector": file.selector
                    })

            clasher.clash_sets.append(clasher_set)

        start_time = time.time()

        logger.info("Starting clash detection")
        clasher.clash()

        if request.smart_grouping:
            logger.info("Starting Smart Clashes....")
            preprocessed_clash_sets = preprocess_clash_data(clasher.clash_sets)
            smart_groups = clasher.smart_group_clashes(preprocessed_clash_sets, 10)
        else:
            logger.info("Skipping Smart Clashes (disabled)")

        logger.info("Exporting clash results")
        clasher.export()

        end_time = time.time()
        execution_time = end_time - start_time

        logger.info(f"Clash detection and export completed in {execution_time:.2f} seconds")

        # Read the JSON result from the output file
        with open(output_path, 'r') as json_file:
            clash_results = json.load(json_file)

        return {
            "success": True,
            "result": clash_results
        }
    except Exception as e:
        logger.error(f"Error during clash detection: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    
def preprocess_clash_data(clash_sets):
    for clash_set in clash_sets:
        clashes = clash_set["clashes"]
        for clash in clashes.values():
            p1 = clash["p1"]
            p2 = clash["p2"]
            # Calculate the midpoint and add it as the "position" key
            clash["position"] = [(p1[i] + p2[i]) / 2 for i in range(3)]
    return clash_sets

@app.get("/health")
async def health_check():
    return {"status": "healthy"}