#!/usr/bin/python3
'''
Demonstration script for the unified satellite simulation.
Shows how to add dynamic nodes during simulation runtime.
'''

import time
import sys
import signal
import requests
from emulation.unified_simulation import SimulationManager

def check_api_positions():
    """Check if nodes are visible in the positions API"""
    try:
        print("\nChecking positions API...")
        response = requests.get('http://127.0.0.1:8000/positions')
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

def main():
    if len(sys.argv) < 2:
        print("Usage: sudo -E python3 -m emulation.demo_dynamic_nodes <config_file>")
        sys.exit(1)
        
    config_file = sys.argv[1]
    
    # Create and start simulation
    print("Starting unified satellite simulation...")
    sim_manager = SimulationManager(config_file)
    
    # Set up signal handler for clean shutdown
    def signal_handler(sig, frame):
        print("\nShutting down simulation...")
        sim_manager.stop()
        sys.exit(0)
        
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start the simulation
    sim_manager.start()
    
    # Wait for network to initialize
    print("Waiting for network and API to initialize...")
    time.sleep(45)
    
    # Check initial positions
    print("\nInitial network state:")
    initial_check = check_api_positions()
    if not initial_check:
        print("WARNING: API check failed. Web interface may not be working.")
    
    # Add a new satellite
    print("\n===== Adding a new satellite =====")
    new_sat = sim_manager.add_satellite("R7_7", 7, 7)
    print(f"Added satellite: {new_sat}")
    
    # Wait and check API
    print("Waiting for API to update...")
    time.sleep(15)
    check_api_positions()
    
    # Add a new ground station
    print("\n===== Adding a new ground station =====")
    new_gs = sim_manager.add_ground_station("G_TOKYO", 35.6762, 139.6503)
    print(f"Added ground station: {new_gs}")
    
    # Wait and check API
    print("Waiting for API to update...")
    time.sleep(15)
    check_api_positions()
    
    # Add a new vessel with path
    print("\n===== Adding a new vessel =====")
    waypoints = [
        (25.0, -70.0),  # Atlantic starting point
        (15.0, -60.0),  # Caribbean
        (5.0, -50.0)    # South American coast
    ]
    new_vessel = sim_manager.add_vessel("V_EXPLORER", waypoints)
    print(f"Added vessel: {new_vessel}")
    
    # Wait and check API
    print("Waiting for API to update...")
    time.sleep(15)
    check_api_positions()
    
    # Keep running until interrupted
    print("\nSimulation running with dynamic nodes added.")
    print("Press Ctrl+C to quit")
    
    try:
        while True:
            time.sleep(60)
            print("\nPeriodic API check:")
            check_api_positions()
    except KeyboardInterrupt:
        print("\nShutting down simulation...")
        sim_manager.stop()

if __name__ == "__main__":
    main()