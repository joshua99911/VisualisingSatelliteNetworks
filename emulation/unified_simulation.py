#!/usr/bin/python3
'''
Unified satellite network simulation combining both Mininet network
and satellite dynamics simulations sharing a single NetworkX graph.
'''

import configparser
import signal
import sys
import os
import threading
import time
import ipaddress
import networkx
import datetime

from mininet.net import Mininet
from mininet.term import makeTerm
from mininet.log import setLogLevel, info
from mininet.link import TCLink
from fastapi import FastAPI, Request, BackgroundTasks
from pydantic import BaseModel

from emulation.mnet import driver
from emulation.mnet import frr_topo
from emulation.mnet.run_mn import (
    ensure_clean_state, configure_dns, setup_packet_capture, 
    cleanup_network, stop_packet_capture, merge_captures
)
from emulation import torus_topo
from emulation import frr_config_topo
from emulation import simapi
from emulation.geosimsat import SatSimulation, Satellite, GroundStation, MovingStation, Waypoint
from skyfield.api import load, wgs84, EarthSatellite

class SatelliteModel(BaseModel):
    name: str
    ring_num: int
    node_num: int
    ip: str = None
    mac: str = None

class GroundStationModel(BaseModel):
    name: str
    lat: float
    lon: float
    ip: str = None

class WaypointModel(BaseModel):
    lat: float
    lon: float

class VesselModel(BaseModel):
    name: str
    waypoints: list[tuple[float, float]]
    ip: str = None


def enhanced_cleanup():
    '''
    Thorough cleanup of all simulation resources.
    Handles host files, FRR folders, network namespaces, and other resources.
    '''
    print("Performing thorough cleanup...")
    
    # 1. Kill all relevant processes
    print("Stopping all related processes...")
    os.system('pkill -f "watchfrr|zebra|ospfd|staticd|bgpd|isisd|pimd|ripd|ripngd|ldpd|nhrpd"')
    os.system('pkill -f "dnsmasq"')  # Kill any running dnsmasq instances
    os.system('pkill -f "tcpdump"')
    
    # 2. Restore host files if backups exist
    print("Restoring host files...")
    if os.path.exists('/etc/hosts.mininet.bak'):
        os.system('cp /etc/hosts.mininet.bak /etc/hosts')
        os.system('rm /etc/hosts.mininet.bak')
        print("- Restored /etc/hosts")
    
    if os.path.exists('/etc/resolv.conf.mininet.bak'):
        os.system('cp /etc/resolv.conf.mininet.bak /etc/resolv.conf')
        os.system('rm /etc/resolv.conf.mininet.bak')
        print("- Restored /etc/resolv.conf")
    
    # 3. Remove FRR folders
    print("Removing FRR folders...")
    os.system('rm -rf /etc/frr/R*')
    os.system('rm -rf /etc/frr/G_*')
    os.system('rm -rf /etc/frr/V_*')
    os.system('rm -rf /var/log/frr/R*')
    os.system('rm -rf /var/log/frr/G_*')
    os.system('rm -rf /var/log/frr/V_*')
    os.system('rm -rf /var/frr/R*')
    os.system('rm -rf /var/frr/G_*')
    os.system('rm -rf /var/frr/V_*')
    os.system('rm -rf /tmp/frr.* /tmp/zebra.* /tmp/ospfd.*')
    
    # 4. Delete all network namespaces
    print("Removing network namespaces...")
    os.system('ip -all netns delete')
    
    # 5. Clean up any remaining veth interfaces
    print("Removing veth interfaces...")
    os.system('ip link show | grep veth | cut -d"@" -f1 | while read veth; do ip link delete $veth 2>/dev/null; done')
    
    # 6. Clean up OVS bridges if OVS is being used
    print("Cleaning up OVS bridges...")
    os.system('ovs-vsctl list-br | xargs -r -l ovs-vsctl del-br')
    
    # 7. Clean up any dnsmasq configuration
    print("Cleaning up dnsmasq configuration...")
    if os.path.exists('/tmp/dnsmasq.mininet.conf'):
        os.system('rm /tmp/dnsmasq.mininet.conf')
    if os.path.exists('/tmp/dnsmasq.mininet.hosts'):
        os.system('rm /tmp/dnsmasq.mininet.hosts')
    os.system('killall -9 dnsmasq 2>/dev/null')
    
    # 8. Restore iptables rules (if modified)
    # print("Restoring iptables rules...")
    # os.system('iptables -F')  # Uncomment if your simulation modified iptables
    
    # 9. Give a moment for everything to settle
    time.sleep(2)
    
    print("Cleanup completed")

class SimulationManager:
    '''
    Manages the unified satellite simulation with shared NetworkX graph.
    Coordinates between Mininet network and satellite dynamics.
    '''
    
    def __init__(self, config_file):
        # Parse config
        self.config_file = config_file
        self.config = self._parse_config(config_file)
        
        # Set up simulation parameters
        self.num_rings = self.config['network'].getint('rings', 4)
        self.num_routers = self.config['network'].getint('routers', 4)
        self.ground_stations = self.config['network'].getboolean('ground_stations', False)
        self.stable_monitors = self.config['monitor'].getboolean('stable_monitors', False)
        self.enable_monitoring = '--monitor' in sys.argv
        
        # Parse ground station data
        self.ground_station_data = {}
        if 'ground_stations' in self.config:
            for name, coords in self.config['ground_stations'].items():
                lat, lon = map(float, coords.split(','))
                self.ground_station_data[name] = (lat, lon)
                
        # Parse vessel data
        self.vessel_data = {}
        if 'vessels' in self.config:
            for name, waypoint_str in self.config['vessels'].items():
                waypoints = []
                for waypoint in waypoint_str.split(';'):
                    lat, lon = map(float, waypoint.split(','))
                    waypoints.append((lat, lon))
                self.vessel_data[name] = waypoints
        
        # Get constellation parameters
        self.inclination = 53.9  # Default
        self.altitude = 550.0    # Default
        if 'constellation' in self.config:
            self.inclination = self.config['constellation'].getfloat('inclination', 53.9)
            self.altitude = self.config['constellation'].getfloat('altitude', 550.0)
        
        # Create the shared NetworkX graph
        self.graph = None
        self.init_graph()
        
        # Simulation objects
        self.net = None
        self.frrt = None
        self.sat_simulation = None
        self.dynamics_thread = None
        self.running = False
        self.lock = threading.Lock()
        
    def _parse_config(self, config_file):
        parser = configparser.ConfigParser()
        parser.optionxform = str  # Preserve case
        parser['network'] = {}
        parser['monitor'] = {}
        parser.read(config_file)
        return parser
        
    def init_graph(self):
        '''Initialize the shared NetworkX graph'''
        print("Initializing network graph...")
            
        # Create the network graph
        self.graph = torus_topo.create_network(
            self.num_rings, 
            self.num_routers,
            self.ground_stations, 
            self.ground_station_data,
            self.vessel_data,
            self.inclination,
            self.altitude
        )
        
        # Annotate the graph with network configuration
        frr_config_topo.annotate_graph(self.graph)

    def open_xterm_for_node(self, node_name):
        '''
        Open an xterm window for a specific node.
        
        Args:
            node_name: Name of the node
        
        Returns:
            True if successful, False otherwise
        '''
        try:
            node = self.net.getNodeByName(node_name)
            if not node:
                print(f"Error: Node {node_name} not found")
                return False
                
            # Open xterm
            makeTerm(node, title=f'Terminal for {node_name}')
            print(f"Opened terminal for {node_name}")
            return True
        except Exception as e:
            print(f"Error opening terminal for {node_name}: {str(e)}")
            return False

    def ping_between_nodes(self, source_node, target_node, count=3):
        '''
        Ping from one node to another using the correct namespace.
        
        Args:
            source_node: Name of the source node
            target_node: Name of the target node
            count: Number of pings to send
            
        Returns:
            Ping output
        '''
        try:
            node = self.net.getNodeByName(source_node)
            if not node:
                return f"Error: Source node {source_node} not found"
                
            return node.cmd(f'ping -c {count} {target_node}')
        except Exception as e:
            return f"Error pinging: {str(e)}"
        
    def start_network_simulation(self):
        '''Start the Mininet network simulation and API server'''
        print("Starting network simulation...")
        
        # Create the network topology
        topo = frr_topo.NetxTopo(self.graph)
        
        # Create FastAPI app for dynamic node addition
        app = driver.app  # Use the existing FastAPI app from driver
        sim_manager = self  # Reference to self for API endpoints
        
        # Define API endpoints for dynamic node addition
        @app.post("/add_satellite")
        def add_satellite_endpoint(satellite: SatelliteModel, background_tasks: BackgroundTasks):
            """API endpoint to add a satellite to the running simulation"""
            try:
                # Run the actual addition in a background task to avoid blocking the API
                def add_sat_task():
                    sim_manager.add_satellite(
                        satellite.name, 
                        satellite.ring_num, 
                        satellite.node_num,
                        ip=satellite.ip, 
                        mac=satellite.mac
                    )
                
                background_tasks.add_task(add_sat_task)
                return {"status": "success", "name": satellite.name, "message": "Satellite addition started"}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        
        @app.post("/add_ground_station")
        def add_ground_station_endpoint(station: GroundStationModel, background_tasks: BackgroundTasks):
            """API endpoint to add a ground station to the running simulation"""
            try:
                # Run the actual addition in a background task
                def add_gs_task():
                    sim_manager.add_ground_station(
                        station.name,
                        station.lat,
                        station.lon,
                        ip=station.ip
                    )
                
                background_tasks.add_task(add_gs_task)
                return {"status": "success", "name": station.name, "message": "Ground station addition started"}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        
        @app.post("/add_vessel")
        def add_vessel_endpoint(vessel: VesselModel, background_tasks: BackgroundTasks):
            """API endpoint to add a vessel to the running simulation"""
            try:
                # Run the actual addition in a background task
                def add_vessel_task():
                    sim_manager.add_vessel(
                        vessel.name,
                        vessel.waypoints,
                        ip=vessel.ip
                    )
                
                background_tasks.add_task(add_vessel_task)
                return {"status": "success", "name": vessel.name, "message": "Vessel addition started"}
            except Exception as e:
                return {"status": "error", "message": str(e)}
            
        @app.post("/open_terminal")
        async def open_terminal_endpoint(request: Request):
            """API endpoint to open a terminal for a node"""
            try:
                data = await request.json()
                node_name = data.get("node_name")
                if not node_name:
                    return {"status": "error", "message": "node_name is required"}
                    
                success = sim_manager.open_xterm_for_node(node_name)
                if success:
                    return {"status": "success", "message": f"Terminal opened for {node_name}"}
                else:
                    return {"status": "error", "message": f"Failed to open terminal for {node_name}"}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        
        # Start Mininet
        try:
            # Check if we need to create our own controller
            # This is to avoid port conflicts when running multiple instances
            import socket
            controller_running = False
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(('127.0.0.1', 6653))  # Default OpenFlow port
                sock.close()
            except:
                controller_running = True
                print("Controller already running, using existing controller")
            
            if controller_running:
                # Start Mininet without a controller
                from mininet.node import RemoteController
                self.net = Mininet(topo=topo, controller=RemoteController)
            else:
                # Start with default controller
                self.net = Mininet(topo=topo)
            
            self.net.start()
            
            # Configure DNS
            configure_dns(self.net, self.graph)
            
            # Start packet capture if monitoring is enabled
            if self.enable_monitoring:
                # Let the network stabilize for a moment
                time.sleep(2)
                setup_packet_capture(self.net, self.graph, "/tmp")
                
            # Start FRR
            self.frrt = frr_topo.FrrSimRuntime(topo, self.net, self.stable_monitors)
            print("Starting FRR routers...")
            self.frrt.start_routers()
            time.sleep(2)
            
            # Start the control API - use separate thread to avoid blocking
            self.driver_thread = threading.Thread(target=driver.run, args=(self.frrt,))
            self.driver_thread.daemon = True
            self.driver_thread.start()
            
        except Exception as e:
            print(f"Error starting network simulation: {str(e)}")
            raise
        
    def start_dynamics_simulation(self):
        '''Start the satellite dynamics simulation'''
        print("Starting dynamics simulation...")
        
        # Configure minimum elevation if specified
        min_elev = 15  # Default
        if 'physical' in self.config:
            min_elev = self.config['physical'].getint('min_elevation', 15)
        
        # Create the satellite simulation with the shared graph
        self.sat_simulation = SatSimulation(self.graph)
        self.sat_simulation.min_elevation = min_elev
        
        # Ensure the simulation's API client is properly connected
        # We'll use a specific URL to avoid any potential issues
        from emulation import simclient
        api_url = "http://127.0.0.1:8000"
        print(f"Setting up API client to {api_url}...")
        self.sat_simulation.client = simclient.Client(api_url)
        
        # Force an initial position update to ensure all nodes are visible
        print("Performing initial position update to API...")
        self._update_api_positions()
        
        # Start the simulation in a separate thread
        self.dynamics_thread = threading.Thread(target=self.sat_simulation.run)
        self.dynamics_thread.daemon = True
        self.dynamics_thread.start()
        
    def start(self):
        '''Start the unified simulation'''
        self.running = True
        
        ensure_clean_state()
        
        # Start network simulation
        self.start_network_simulation()
        
        # Start dynamics simulation
        self.start_dynamics_simulation()
        
        print("Simulation started successfully")
        
        # Wait for Ctrl+C
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Shutting down simulation...")
            self.stop()
            
    def stop(self):
        '''Stop the simulation with thorough cleanup'''
        print("Stopping simulation and cleaning up resources...")
        self.running = False
        
        # Stop the network and dynamics simulation
        if hasattr(self, 'frrt') and self.frrt:
            try:
                print("Stopping FRR routers...")
                self.frrt.stop_routers()
            except Exception as e:
                print(f"Error stopping FRR routers: {str(e)}")
        
        if hasattr(self, 'net') and self.net:
            try:
                print("Stopping Mininet network...")
                self.net.stop()
            except Exception as e:
                print(f"Error stopping Mininet network: {str(e)}")
        
        # Perform thorough cleanup
        try:
            enhanced_cleanup()
        except Exception as e:
            print(f"Error during cleanup: {str(e)}")


        
    def add_satellite(self, name, ring_num, node_num, ip=None, mac=None):
        '''
        Dynamically add a satellite to the running simulation.
        
        Args:
            name: Name of the satellite
            ring_num: Ring number (orbital plane)
            node_num: Node number within the ring
            ip: IP address (optional)
            mac: MAC address (optional)
        '''
        with self.lock:
            print(f"Adding satellite {name} to ring {ring_num}, position {node_num}")
            # ... [Existing code remains the same until after creating the satellite] ...
            
            # [After creating the satellite and configuring Mininet...]
            
            # 5. Update satellite simulation with debug output
            if self.sat_simulation:
                print(f"Adding satellite {name} to dynamics simulation...")
                
                # Get orbital parameters
                num_rings = self.graph.graph["rings"]
                num_ring_nodes = self.graph.graph["ring_nodes"]
                right_ascension = 360 / num_rings * ring_num
                inclination = self.graph.graph["inclination"]
                altitude = self.graph.graph["altitude"]
                
                # Create the orbit
                mean_anomaly = 360 / num_ring_nodes * node_num
                # Offset 1/2 spacing for odd rings
                if ring_num % 2 == 1:
                    mean_anomaly += 360 / num_ring_nodes / 2
                    
                orbit = torus_topo.OrbitData(right_ascension, inclination, mean_anomaly, altitude)
                orbit.assign_cat_num()
                
                # Create Earth satellite object
                ts = load.timescale()
                l1, l2 = orbit.tle_format()
                print(f"Satellite TLE: {l1} / {l2}")
                
                from skyfield.api import EarthSatellite
                earth_satellite = EarthSatellite(l1, l2, name, ts)
                
                # Add to satellite simulation
                new_satellite = Satellite(name, earth_satellite)
                self.sat_simulation.satellites.append(new_satellite)
                
                # Force immediate position update
                current_time = datetime.datetime.now(tz=datetime.timezone.utc)
                sfield_time = self.sat_simulation.ts.from_datetime(current_time)
                new_satellite.geo = new_satellite.earth_sat.at(sfield_time)
                lat, lon = wgs84.latlon_of(new_satellite.geo)
                new_satellite.lat = lat
                new_satellite.lon = lon
                new_satellite.height = wgs84.height_of(new_satellite.geo)
                
                # Force API update
                self._update_api_positions()
                
                print(f"Satellite {name} added to dynamics simulation at lat: {lat.degrees}, lon: {lon.degrees}, height: {new_satellite.height.km} km")
            
            print(f"Satellite {name} added successfully")
            return name
    
    def add_ground_station(self, name, lat, lon, ip=None):
        '''
        Dynamically add a ground station to the running simulation.
        
        Args:
            name: Name of the ground station
            lat: Latitude
            lon: Longitude
            ip: IP address (optional)
        '''
        with self.lock:
            print(f"Adding ground station {name} at lat: {lat}, lon: {lon}")
            # ... [Existing code remains the same until after creating the ground station] ...
            
            # [After creating the ground station in Mininet...]
            
            # 4. Update satellite simulation with debug output
            if self.sat_simulation:
                print(f"Adding ground station {name} to dynamics simulation...")
                position = wgs84.latlon(lat, lon)
                new_ground_station = GroundStation(name, position)
                self.sat_simulation.ground_stations.append(new_ground_station)
                
                # Force API update
                self._update_api_positions()
                
                print(f"Ground station {name} added to dynamics simulation at lat: {lat}, lon: {lon}")
                
            print(f"Ground station {name} added successfully")
            return name
            
    def add_vessel(self, name, waypoints, ip=None):
        '''
        Dynamically add a vessel to the running simulation.
        
        Args:
            name: Name of the vessel
            waypoints: List of (lat, lon) tuples defining the vessel's path
            ip: IP address (optional)
        '''
        with self.lock:
            print(f"Adding vessel {name} with {len(waypoints)} waypoints")
            # ... [Existing code remains the same until after creating the vessel] ...
            
            # [After creating the vessel in Mininet...]
            
            # 4. Update satellite simulation with debug output
            if self.sat_simulation:
                print(f"Adding vessel {name} to dynamics simulation...")
                # Convert tuple waypoints to Waypoint objects
                waypoint_objects = [Waypoint(lat=wp[0], lon=wp[1]) for wp in waypoints]
                
                # Create position at first waypoint
                position = wgs84.latlon(waypoints[0][0], waypoints[0][1])
                
                # Create the vessel
                moving_station = MovingStation(
                    name=name,
                    position=position,
                    waypoints=waypoint_objects
                )
                self.sat_simulation.moving_stations.append(moving_station)
                
                # Force API update
                self._update_api_positions()
                
                print(f"Vessel {name} added to dynamics simulation at initial position lat: {waypoints[0][0]}, lon: {waypoints[0][1]}")
                    
            print(f"Vessel {name} added successfully")
            return name

    # New helper method to force position updates to the API
    def _update_api_positions(self):
        """Force an immediate update of positions to the API"""
        if self.sat_simulation:
            # Get current time
            current_time = datetime.datetime.now(tz=datetime.timezone.utc)
            
            # Update satellite positions
            for satellite in self.sat_simulation.satellites:
                sfield_time = self.sat_simulation.ts.from_datetime(current_time)
                satellite.geo = satellite.earth_sat.at(sfield_time)
                lat, lon = wgs84.latlon_of(satellite.geo)
                satellite.lat = lat
                satellite.lon = lon
                satellite.height = wgs84.height_of(satellite.geo)
            
            # Collect all positions for the API
            # Satellites
            satellite_positions = []
            for satellite in self.sat_simulation.satellites:
                satellite_positions.append(simapi.SatellitePosition(
                    name=satellite.name,
                    lat=float(satellite.lat.degrees),
                    lon=float(satellite.lon.degrees),
                    height=float(satellite.height.km)
                ))
            
            # Ground stations
            ground_station_positions = []
            for station in self.sat_simulation.ground_stations:
                ground_station_positions.append(simapi.GroundStationPosition(
                    name=station.name,
                    lat=float(station.position.latitude.degrees),
                    lon=float(station.position.longitude.degrees)
                ))
            
            # Vessels
            vessel_positions = []
            for vessel in self.sat_simulation.moving_stations:
                vessel_positions.append(simapi.VesselPosition(
                    name=vessel.name,
                    lat=float(vessel.position.latitude.degrees),
                    lon=float(vessel.position.longitude.degrees)
                ))
            
            # Satellite links
            satellite_links = []
            for node1, node2 in self.graph.edges():
                if node1.startswith('R') and node2.startswith('R'):
                    status = self.graph.edges[node1, node2].get("up", True)
                    satellite_links.append(simapi.Link(
                        node1_name=node1,
                        node2_name=node2,
                        up=status
                    ))
            
            # Ground uplinks
            ground_uplinks = []
            all_stations = self.sat_simulation.ground_stations + self.sat_simulation.moving_stations
            for station in all_stations:
                uplinks_list = []
                for uplink in station.uplinks:
                    uplinks_list.append(simapi.UpLink(
                        sat_node=uplink.satellite_name,
                        distance=int(uplink.distance)
                    ))
                if uplinks_list:
                    ground_uplinks.append(simapi.UpLinks(
                        ground_node=station.name,
                        uplinks=uplinks_list
                    ))
            
            # Send to API
            print("Sending updated positions to API...")
            data = simapi.GraphData(
                satellites=satellite_positions,
                ground_stations=ground_station_positions,
                vessels=vessel_positions,
                satellite_links=satellite_links,
                ground_uplinks=ground_uplinks
            )
            
            # Use self.sat_simulation.client to send the update
            if hasattr(self.sat_simulation, 'client'):
                self.sat_simulation.client.update_positions(data)
                print(f"Updated API with {len(satellite_positions)} satellites, {len(ground_station_positions)} ground stations, and {len(vessel_positions)} vessels")

def main():
    '''Main entry point for the unified simulation'''
    if len(sys.argv) < 2:
        print("Usage: python3 -m emulation.unified_simulation <config_file> [--monitor]")
        sys.exit(1)
        
    config_file = sys.argv[1]
    
    # Set logging level
    setLogLevel("info")
    
    # Create and start the simulation manager
    sim_manager = SimulationManager(config_file)
    
    # Set up signal handler for clean shutdown
    def signal_handler(sig, frame):
        print("\nShutting down simulation...")
        sim_manager.stop()
        sys.exit(0)
        
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start the simulation
    sim_manager.start()

if __name__ == "__main__":
    main()