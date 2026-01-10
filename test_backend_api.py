#!/usr/bin/env python3
"""
Quick test script to verify backend API endpoints are accessible
and returning data correctly.
"""

import requests
import json
import sys

BACKEND_URL = "http://localhost:3000"

def test_endpoint(endpoint_name):
    """Test a single endpoint and print results"""
    url = f"{BACKEND_URL}/{endpoint_name}"
    print(f"\n{'='*70}")
    print(f"Testing: {url}")
    print(f"{'='*70}")

    try:
        response = requests.get(url, timeout=5)
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print(f"✓ SUCCESS - Received data")
            print(f"Data structure: {type(data)}")

            if isinstance(data, list) and len(data) > 0:
                print(f"Number of items: {len(data)}")
                print(f"First item keys: {list(data[0].keys()) if isinstance(data[0], dict) else 'N/A'}")

                # Print first item in pretty format
                print(f"\nFirst item:")
                print(json.dumps(data[0], indent=2))
            elif isinstance(data, dict):
                print(f"Keys: {list(data.keys())}")
                print(f"\nData:")
                print(json.dumps(data, indent=2))
            else:
                print(f"Data: {data}")

            return True
        else:
            print(f"✗ FAILED - Status {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False

    except requests.exceptions.ConnectionError:
        print(f"✗ CONNECTION ERROR - Backend not running at {BACKEND_URL}")
        return False
    except requests.exceptions.Timeout:
        print(f"✗ TIMEOUT - Backend took too long to respond")
        return False
    except Exception as e:
        print(f"✗ ERROR: {str(e)}")
        return False

def main():
    """Test all important endpoints"""
    print("\n" + "="*70)
    print("POLARI BACKEND API TEST")
    print("="*70)

    # List of endpoints to test
    endpoints = [
        "polyTypedObject",
        "polyTypedVariable",
        "polariServer",
        "polariAPI",
        "polariCRUDE",
        "managerObject",  # This is the one we're particularly interested in
    ]

    results = {}
    for endpoint in endpoints:
        results[endpoint] = test_endpoint(endpoint)

    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    for endpoint, success in results.items():
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{status}: /{endpoint}")

    successful = sum(1 for s in results.values() if s)
    total = len(results)
    print(f"\nTotal: {successful}/{total} endpoints accessible")

    if successful < total:
        print("\n⚠️  Some endpoints are not accessible!")
        print("Make sure the backend is running: cd polari-framework && python initLocalhostPolariServer.py")
        sys.exit(1)
    else:
        print("\n✓ All endpoints are accessible!")
        sys.exit(0)

if __name__ == "__main__":
    main()
