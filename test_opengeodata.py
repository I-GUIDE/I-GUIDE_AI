#!/usr/bin/env python3
"""Test script to check OpenGeoData retrieval in the pipeline"""
import requests
import json
import sys
import time

def test_opengeodata_pipeline():
    url = "http://localhost:5002/query"
    
    payload = {
        "user_input": "Illinois landcover",
        "params": {
            "top_k": 100,
            "enable_llm_reranker": True
        },
        "session_context": {}
    }
    
    print("=" * 70)
    print("OpenGeoData Pipeline Test")
    print("=" * 70)
    print(f"Endpoint: {url}")
    print(f"Query: {payload['user_input']}")
    print(f"Top K: {payload['params']['top_k']}")
    print("-" * 70)
    
    try:
        start_time = time.time()
        response = requests.post(url, json=payload, timeout=120)
        elapsed = time.time() - start_time
        
        print(f"\n⏱️  Request completed in {elapsed:.2f} seconds")
        print(f"📊 Status Code: {response.status_code}")
        
        if response.status_code != 200:
            print(f"\n❌ Request failed with status {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False
        
        try:
            data = response.json()
        except json.JSONDecodeError:
            print(f"\n❌ Invalid JSON response")
            print(f"Response: {response.text[:500]}")
            return False
        
        # Check for opengeodata results
        elements = data.get("elements", [])
        opengeodata_elements = [
            e for e in elements 
            if e.get("source") == "opengeodata" or e.get("resource-type") == "opengeodata"
        ]
        
        print(f"\n✅ Request successful")
        print(f"📦 Total elements: {len(elements)}")
        print(f"🌍 OpenGeoData elements: {len(opengeodata_elements)}")
        
        if opengeodata_elements:
            print(f"\n✅ OpenGeoData results found:")
            for i, elem in enumerate(opengeodata_elements[:5], 1):
                title = elem.get('title', 'No title')
                score = elem.get('_score', 0)
                source_type = elem.get('resource-type', 'unknown')
                print(f"  {i}. {title[:60]}")
                print(f"     Score: {score:.4f} | Type: {source_type}")
            if len(opengeodata_elements) > 5:
                print(f"  ... and {len(opengeodata_elements) - 5} more")
        else:
            print(f"\n⚠️  No OpenGeoData results found in response")
            
        # Check retrieval steps
        retrieval_steps = data.get("retrievalSteps", [])
        print(f"\n📋 Retrieval steps: {len(retrieval_steps)}")
        
        opengeodata_step = None
        for step in retrieval_steps:
            reason = step.get("reason", "")
            source = step.get("source", "")
            if "opengeodata" in reason.lower() or source == "opengeodata":
                opengeodata_step = step
                break
        
        if opengeodata_step:
            print(f"✅ OpenGeoData retrieval step found:")
            print(f"   Source: {opengeodata_step.get('source', 'unknown')}")
            print(f"   Reason: {opengeodata_step.get('reason', 'unknown')}")
        else:
            print(f"⚠️  No OpenGeoData retrieval step found in trace")
            print(f"   Available steps: {[s.get('source', 'unknown') for s in retrieval_steps]}")
        
        # Summary
        print("\n" + "=" * 70)
        if len(opengeodata_elements) > 0:
            print("✅ TEST PASSED: OpenGeoData results are being returned")
            return True
        else:
            print("❌ TEST FAILED: No OpenGeoData results found")
            print("\n💡 Check server logs for OpenGeoData retrieval errors")
            return False
            
    except requests.exceptions.ConnectionError:
        print(f"\n❌ Connection failed: Could not connect to {url}")
        print("   Make sure the RAG pipeline server is running on port 5002")
        return False
    except requests.exceptions.Timeout:
        print(f"\n❌ Request timed out after 120 seconds")
        return False
    except Exception as e:
        print(f"\n❌ Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_opengeodata_pipeline()
    sys.exit(0 if success else 1)

