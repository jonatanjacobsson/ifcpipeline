from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import os
import time
import logging
from catboost import CatBoostClassifier

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define request/response models
class IfcClassifyRequest(BaseModel):
    category: str
    family: str
    type: str
    manufacturer: str = ""
    description: str = ""

class IfcClassifyBatchRequest(BaseModel):
    elements: List[IfcClassifyRequest]

class IfcClassificationResult(BaseModel):
    ifc_class: str
    predefined_type: Optional[str] = None
    confidence: float
    element_id: Optional[str] = None

class IfcClassifyResponse(BaseModel):
    result: IfcClassificationResult
    processing_time_ms: float

class IfcClassifyBatchResponse(BaseModel):
    results: List[IfcClassificationResult]
    processing_time_ms: float
    total_elements: int

class ModelInfo(BaseModel):
    model_loaded: bool
    model_path: str
    feature_names: List[str]
    classes_count: int
    version: str = "1.0.0"

# Initialize FastAPI app
app = FastAPI(
    title="IFC Classifier Service",
    description="CPU-based IFC element classification using CatBoost",
    version="1.0.0"
)

# Global model instance
model = None
model_info = None

def load_model():
    """Load the CatBoost model on startup"""
    global model, model_info
    
    model_path = "/app/models/ifc_classifier.cbm"
    
    if not os.path.exists(model_path):
        logger.warning(f"Model file not found at {model_path}. Service will return mock responses.")
        model_info = ModelInfo(
            model_loaded=False,
            model_path=model_path,
            feature_names=["category", "family", "type", "manufacturer"],
            classes_count=0
        )
        return
    
    try:
        model = CatBoostClassifier()
        model.load_model(model_path)
        
        # Get model information - use only the features the model was trained with
        feature_names = ["category", "family", "type", "manufacturer"]
        classes_count = len(model.classes_) if hasattr(model, 'classes_') else 0
        
        # Log model details for debugging
        logger.info(f"Model loaded successfully from {model_path}")
        logger.info(f"Model supports {classes_count} classes")
        logger.info(f"Model feature names: {feature_names}")
        
        # Check if model has categorical features info
        if hasattr(model, 'feature_names_'):
            logger.info(f"Model internal feature names: {model.feature_names_}")
        if hasattr(model, 'get_cat_feature_indices'):
            try:
                cat_indices = model.get_cat_feature_indices()
                logger.info(f"Categorical feature indices: {cat_indices}")
            except:
                logger.info("Could not get categorical feature indices")
        
        model_info = ModelInfo(
            model_loaded=True,
            model_path=model_path,
            feature_names=feature_names,
            classes_count=classes_count
        )
        
    except Exception as e:
        logger.error(f"Failed to load model: {str(e)}")
        logger.error(f"Model loading error details: {type(e).__name__}")
        model_info = ModelInfo(
            model_loaded=False,
            model_path=model_path,
            feature_names=["category", "family", "type", "manufacturer"],
            classes_count=0
        )

def classify_element(element: IfcClassifyRequest) -> IfcClassificationResult:
    """Classify a single IFC element"""
    if model is None:
        # Return mock response when model is not loaded
        logger.warning("Model is None, returning mock response")
        return IfcClassificationResult(
            ifc_class="IfcBuildingElement",
            predefined_type="NOTDEFINED",
            confidence=0.0,
            element_id=None
        )
    
    try:
        # Prepare input features - only use the 4 features the model was trained with
        # Model expects: ['Category', 'Family', 'Type', 'Manufacturer']
        features = [
            element.category, 
            element.family, 
            element.type, 
            element.manufacturer
            # Note: description is excluded as the model wasn't trained with it
        ]
        
        logger.debug(f"Input features: {features}")
        
        # Make prediction with categorical features
        # CatBoost can handle categorical features directly if they were defined during training
        prediction = model.predict([features])[0]
        probabilities = model.predict_proba([features])[0]
        confidence = float(max(probabilities))
        
        logger.debug(f"Raw prediction: {prediction} (type: {type(prediction)})")
        logger.debug(f"Max probability: {confidence}")
        
        # Convert prediction to string if it's not already
        # Handle cases where prediction might be an array or list
        if hasattr(prediction, '__iter__') and not isinstance(prediction, str):
            prediction_str = str(prediction[0]) if len(prediction) > 0 else str(prediction)
        else:
            prediction_str = str(prediction)
        
        # Clean up the prediction string (remove brackets, quotes, etc.)
        prediction_str = prediction_str.strip("[]'\"")
        
        # Parse prediction (assuming format "IfcClass|PredefinedType" or just "IfcClass")
        if "|" in prediction_str:
            ifc_class, predefined_type = prediction_str.split("|", 1)
        else:
            ifc_class = prediction_str
            predefined_type = "NOTDEFINED"
        
        logger.debug(f"Final classification: {ifc_class}, {predefined_type}, confidence: {confidence}")
        
        return IfcClassificationResult(
            ifc_class=ifc_class,
            predefined_type=predefined_type,
            confidence=confidence
        )
        
    except Exception as e:
        logger.error(f"Classification error: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Input element: category='{element.category}', family='{element.family}', type='{element.type}', manufacturer='{element.manufacturer}', description='{element.description}'")
        # Return fallback classification
        return IfcClassificationResult(
            ifc_class="IfcBuildingElement",
            predefined_type="NOTDEFINED",
            confidence=0.0
        )

@app.on_event("startup")
async def startup_event():
    """Load model on startup"""
    load_model()

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "model_loaded": model_info.model_loaded if model_info else False,
        "service": "ifc-classifier"
    }

@app.get("/model/info", response_model=ModelInfo)
async def get_model_info():
    """Get model information"""
    if model_info is None:
        raise HTTPException(status_code=500, detail="Model information not available")
    return model_info

@app.post("/classify", response_model=IfcClassifyResponse)
async def classify_single(request: IfcClassifyRequest):
    """Classify a single IFC element"""
    start_time = time.time()
    
    try:
        result = classify_element(request)
        processing_time = (time.time() - start_time) * 1000  # Convert to milliseconds
        
        return IfcClassifyResponse(
            result=result,
            processing_time_ms=processing_time
        )
        
    except Exception as e:
        logger.error(f"Error in single classification: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Classification failed: {str(e)}")

@app.post("/classify/batch", response_model=IfcClassifyBatchResponse)
async def classify_batch(request: IfcClassifyBatchRequest):
    """Classify multiple IFC elements"""
    start_time = time.time()
    
    try:
        results = []
        for element in request.elements:
            result = classify_element(element)
            results.append(result)
        
        processing_time = (time.time() - start_time) * 1000  # Convert to milliseconds
        
        return IfcClassifyBatchResponse(
            results=results,
            processing_time_ms=processing_time,
            total_elements=len(results)
        )
        
    except Exception as e:
        logger.error(f"Error in batch classification: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Batch classification failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 