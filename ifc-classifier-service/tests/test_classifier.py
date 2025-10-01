#!/usr/bin/env python3
"""
Basic tests for the IFC Classifier Service
"""

import requests
import json

def test_health_endpoint():
    """Test the health endpoint"""
    response = requests.get("http://localhost:8002/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "model_loaded" in data
    print("✓ Health endpoint test passed")

def test_classify_endpoint():
    """Test single classification endpoint"""
    test_data = {
        "category": "Walls",
        "family": "Basic Wall",
        "type": "Generic - 200mm",
        "manufacturer": "Autodesk",
        "description": "Interior partition wall"
    }
    
    response = requests.post(
        "http://localhost:8002/classify",
        json=test_data,
        headers={"Content-Type": "application/json"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "result" in data
    assert "processing_time_ms" in data
    assert "ifc_class" in data["result"]
    assert "confidence" in data["result"]
    print("✓ Single classification test passed")
    print(f"  Result: {data['result']['ifc_class']} (confidence: {data['result']['confidence']:.2f})")

def test_batch_classify_endpoint():
    """Test batch classification endpoint"""
    test_data = {
        "elements": [
            {
                "category": "Walls",
                "family": "Basic Wall",
                "type": "Generic - 200mm",
                "manufacturer": "Autodesk",
                "description": "Interior partition wall"
            },
            {
                "category": "Doors",
                "family": "Single-Flush",
                "type": "900 x 2100mm",
                "manufacturer": "Autodesk",
                "description": "Standard door"
            },
            {
                "category": "Windows",
                "family": "Fixed",
                "type": "1200 x 1500mm",
                "manufacturer": "Autodesk",
                "description": "Fixed window"
            }
        ]
    }
    
    response = requests.post(
        "http://localhost:8002/classify/batch",
        json=test_data,
        headers={"Content-Type": "application/json"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert "total_elements" in data
    assert data["total_elements"] == 3
    assert len(data["results"]) == 3
    print("✓ Batch classification test passed")
    print(f"  Processed {data['total_elements']} elements in {data['processing_time_ms']:.2f}ms")

def test_model_info_endpoint():
    """Test model info endpoint"""
    response = requests.get("http://localhost:8002/model/info")
    assert response.status_code == 200
    data = response.json()
    assert "model_loaded" in data
    assert "feature_names" in data
    assert "classes_count" in data
    print("✓ Model info test passed")
    print(f"  Model loaded: {data['model_loaded']}")
    print(f"  Features: {data['feature_names']}")

def main():
    """Run all tests"""
    print("Testing IFC Classifier Service...")
    print("=" * 50)
    
    try:
        test_health_endpoint()
        test_model_info_endpoint()
        test_classify_endpoint()
        test_batch_classify_endpoint()
        
        print("=" * 50)
        print("✓ All tests passed!")
        
    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to service. Is it running on http://localhost:8002?")
        print("Start the service with: docker-compose up ifc-classifier")
    except AssertionError as e:
        print(f"❌ Test failed: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    main() 