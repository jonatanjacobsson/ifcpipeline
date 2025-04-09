
from shared.classes import IfcClashRequest, ClashSet, ClashFile, ClashMode
import logging
import json
import os
import time
import ifcopenshell
import ifcopenshell.util.selector
import ifcopenshell.geom
import multiprocessing

# Monkey patch the problematic method in ifcclash before importing the module
import sys

# Define a replacement for the add_collision_objects method
def patched_add_collision_objects(self, name, ifc_file, mode, selector):
    start = time.time()
    self.settings.logger.info("Creating iterator")
    if not mode or mode == "a" or not selector:
        elements = set(ifc_file.by_type("IfcElement"))
        elements -= set(ifc_file.by_type("IfcFeatureElement"))
    elif mode == "e":
        elements = set(ifc_file.by_type("IfcElement"))
        elements -= set(ifc_file.by_type("IfcFeatureElement"))
        elements -= set(ifcopenshell.util.selector.filter_elements(ifc_file, selector))
    elif mode == "i":
        elements = set(ifcopenshell.util.selector.filter_elements(ifc_file, selector))
        
    # Convert elements to a list of element ID strings for the iterator
    element_ids = [str(e.id()) for e in elements]
    
    iterator = ifcopenshell.geom.iterator(
        self.geom_settings, ifc_file, multiprocessing.cpu_count(), include=element_ids
    )
    self.settings.logger.info(f"Iterator creation finished {time.time() - start}")

    start = time.time()
    self.logger.info(f"Adding objects {name} ({len(elements)} elements)")
    assert iterator.initialize()
    while True:
        self.tree.add_element(iterator.get())
        shape = iterator.get()
        if not iterator.next():
            break
    self.logger.info(f"Tree finished {time.time() - start}")
    start = time.time()
    self.groups[name]["elements"].update({e.GlobalId: e for e in elements})
    self.logger.info(f"Element metadata finished {time.time() - start}")
    start = time.time()

# Import and apply monkey patch
from ifcclash.ifcclash import Clasher, ClashSettings
# Apply the patch
Clasher.add_collision_objects = patched_add_collision_objects

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
    models_dir = "/uploads"
    output_dir = "/output/clash"
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

        logger.info(f"Smart grouping? {request.smart_grouping}")

        if request.smart_grouping:
            logger.info("Starting Smart Clashes....")
            preprocessed_clash_sets = preprocess_clash_data(clasher.clash_sets)
            smart_groups = clasher.smart_group_clashes(preprocessed_clash_sets, request.max_cluster_distance)
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