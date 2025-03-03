#!/usr/bin/python3
'''
Client demonstration script for adding dynamic nodes to a running simulation.
This script does NOT start its own simulation but instead connects to a running one.
'''

import time
import sys
import requests
import json

# API endpoint
API_URL = "http://127.0.0.1:8000"

def check_api_positions():
    """Check if nodes are visible in the positions API"""
    try:
        print("\nChecking positions API...")
        response = requests.get(f'{API_URL}/positions')
        if response.status_code == 200:
            data = response.json()
            print(f"  Satellites: {len(data['satellites'])}")
            for sat in data['satellites']:
                print(f"    {sat['name']} at lat: {sat['lat']:.2f}, lon: {sat['lon']:.2f}")
            
            print(f"  Ground stations: {len(data['ground_stations'])}")
            for gs in data['ground_stations']:
                print(f"    {gs['name']} at lat: {gs['lat']:.2f}, lon: {gs['lon']:.2f}")
            
            print(f"  Vessels: {len(data['vessels'])}")
            for vessel in data['vessels']:
                print(f"    {vessel['name']} at lat: {vessel['lat']:.2f}, lon: {vessel['lon']:.2f}")
            
            return True
        else:
            print(f"  API error: {response.status_code}")
            return False
    except Exception as e:
        print(f"  Error checking API: {str(e)}")
        return False

def add_satellite(name, ring_num, node_num, ip=None, mac=None):
    """Add a satellite to the running simulation via API"""
    print(f"\n===== Adding satellite {name} to ring {ring_num}, position {node_num} =====")
    data = {
        "name": name,
        "ring_num": ring_num,
        "node_num": node_num
    }
    
    if ip:
        data["ip"] = ip
    if mac:
        data["mac"] = mac
    
    try:
        response = requests.post(f'{API_URL}/add_satellite', json=data)
        if response.status_code == 200:
            result = response.json()
            print(f"Successfully added satellite: {result.get('name', name)}")
            return True
        else:
            print(f"Failed to add satellite: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Error adding satellite: {str(e)}")
        return False

def add_ground_station(name, lat, lon, ip=None):
    """Add a ground station to the running simulation via API"""
    print(f"\n===== Adding ground station {name} at lat: {lat}, lon: {lon} =====")
    data = {
        "name": name,
        "lat": lat,
        "lon": lon
    }
    
    if ip:
        data["ip"] = ip
    
    try:
        response = requests.post(f'{API_URL}/add_ground_station', json=data)
        if response.status_code == 200:
            result = response.json()
            print(f"Successfully added ground station: {result.get('name', name)}")
            return True
        else:
            print(f"Failed to add ground station: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Error adding ground station: {str(e)}")
        return False

def add_vessel(name, waypoints, ip=None):
    """Add a vessel to the running simulation via API"""
    print(f"\n===== Adding vessel {name} with {len(waypoints)} waypoints =====")
    data = {
        "name": name,
        "waypoints": waypoints
    }
    
    if ip:
        data["ip"] = ip
    
    try:
        response = requests.post(f'{API_URL}/add_vessel', json=data)
        if response.status_code == 200:
            result = response.json()
            print(f"Successfully added vessel: {result.get('name', name)}")
            return True
        else:
            print(f"Failed to add vessel: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Error adding vessel: {str(e)}")
        return False

def main():
    # Check if simulation is running
    print("Checking if simulation is running...")
    try:
        response = requests.get(f'{API_URL}/')
        if response.status_code != 200:
            print("Error: Simulation API is not available. Make sure the simulation is running.")
            sys.exit(1)
    except Exception as e:
        print(f"Error connecting to simulation API: {str(e)}")
        print("Make sure the simulation is running with: sudo -E python3 -m emulation.unified_simulation <config_file>")
        sys.exit(1)
    
    print("Simulation is running. Starting dynamic node demo...")
    
    # Wait for network to initialize
    print("Waiting for API to stabilize...")
    time.sleep(5)
    
    # Check initial positions
    print("\nInitial network state:")
    initial_check = check_api_positions()
    if not initial_check:
        print("WARNING: API check failed. Web interface may not be working.")
    
    # Add a new satellite
    add_satellite("R7_7", 7, 7)
    
    # Wait and check API
    print("Waiting for API to update...")
    time.sleep(15)
    check_api_positions()
    
    # Add a new ground station
    add_ground_station("G_TOKYO", 35.6762, 139.6503)
    
    # Wait and check API
    print("Waiting for API to update...")
    time.sleep(15)
    check_api_positions()
    
    # Add a new vessel with path
    waypoints = [
        (25.0, -70.0),  # Atlantic starting point
        (15.0, -60.0),  # Caribbean
        (5.0, -50.0)    # South American coast
    ]
    add_vessel("V_EXPLORER", waypoints)
    
    # Wait and check API
    print("Waiting for API to update...")
    time.sleep(15)
    check_api_positions()
    
    # Periodic monitoring
    print("\nStarting periodic API checks. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(30)
            print("\nPeriodic API check:")
            check_api_positions()
    except KeyboardInterrupt:
        print("\nExiting client demo.")

if __name__ == "__main__":
    main()