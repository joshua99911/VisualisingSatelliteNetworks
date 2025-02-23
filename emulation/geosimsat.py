'''
Geographic Satellite Simulator
Simulate location changes in a satellite network in real time.

Simulate in real time specific events in a satellite network:
Generate events for:
    - Satellite position - based on TLE data specs
    - horizontal links down above and below a critical latitude
    - new / break  connections to ground stations
    - new / break connections to end hosts
'''

from dataclasses import dataclass, field
import configparser
import sys
import datetime
import time

from emulation import torus_topo
from emulation import simclient
from emulation import simapi

import networkx
from skyfield.api import load, wgs84 # type: ignore
from skyfield.api import EarthSatellite # type: ignore
from skyfield.positionlib import Geocentric # type: ignore
from skyfield.toposlib import GeographicPosition # type: ignore
from skyfield.units import Angle, Distance # type: ignore


@dataclass
class Satellite:
    '''
    Represents an instance of a satellite
    '''

    name: str
    earth_sat: EarthSatellite
    geo: Geocentric = None
    lat: Angle = 0
    lon: Angle = 0
    height: Distance = 0
    inter_plane_status: bool = True
    prev_inter_plane_status: bool = True

@dataclass
class Uplink:
    '''Represents a link between the ground and a satellite'''
    satellite_name: str
    ground_name: str
    distance: int

@dataclass
class GroundStation:
    '''Represents an instance of a ground station'''
    name: str
    position: GeographicPosition
    uplinks: list[Uplink] = field(default_factory=list)
    

@dataclass
class Waypoint:
    """Represents a waypoint for a vessel's journey"""
    lat: float
    lon: float

@dataclass
class MovingStation(GroundStation):
    '''Represents an instance of a moving station (vessel)'''
    waypoints: list[Waypoint] = field(default_factory=list)
    current_waypoint_index: int = 0
    next_waypoint_index: int = 1
    moving_forward: bool = True
    SPEED: float = 1.0 #0.01  # degrees per update

    def update_position(self) -> None:
        """Update the vessel's position based on constant speed movement"""
        if not self.waypoints or len(self.waypoints) < 2:
            return

        current_lat = float(self.position.latitude.degrees)
        current_lon = float(self.position.longitude.degrees)
        current_wp = self.waypoints[self.current_waypoint_index]
        next_wp = self.waypoints[self.next_waypoint_index]

        # Calculate direction vector
        delta_lat = next_wp.lat - current_wp.lat
        delta_lon = next_wp.lon - current_wp.lon
        
        # Normalize direction vector
        distance = (delta_lat ** 2 + delta_lon ** 2) ** 0.5
        if distance > 0:
            move_lat = (delta_lat / distance) * self.SPEED
            move_lon = (delta_lon / distance) * self.SPEED
        else:
            move_lat = move_lon = 0

        # Update position
        new_lat = current_lat + move_lat
        new_lon = current_lon + move_lon

        # Check if we've reached the next waypoint
        new_distance = ((new_lat - next_wp.lat) ** 2 + (new_lon - next_wp.lon) ** 2) ** 0.5
        if new_distance < self.SPEED:
            # We've reached the waypoint, update indices
            if self.moving_forward:
                if self.next_waypoint_index == len(self.waypoints) - 1:
                    # Reached last waypoint, reverse direction
                    self.moving_forward = False
                    self.current_waypoint_index = self.next_waypoint_index
                    self.next_waypoint_index = self.current_waypoint_index - 1
                else:
                    # Move to next waypoint
                    self.current_waypoint_index = self.next_waypoint_index
                    self.next_waypoint_index += 1
            else:
                if self.next_waypoint_index == 0:
                    # Reached first waypoint, reverse direction
                    self.moving_forward = True
                    self.current_waypoint_index = 0
                    self.next_waypoint_index = 1
                else:
                    # Move to previous waypoint
                    self.current_waypoint_index = self.next_waypoint_index
                    self.next_waypoint_index -= 1

        # Update the position using wgs84.latlon
        self.position = wgs84.latlon(new_lat, new_lon)


class SatSimulation:
    '''
    Runs real time to update satellite positions
    '''

    # Time slice for simulation
    TIME_SLICE = 10
    MIN_ELEVATION = 35

    def __init__(self, graph: networkx.Graph):
        self.graph = graph
        self.ts = load.timescale()
        self.satellites: list[Satellite] = []
        self.ground_stations: list[GroundStation] = []
        self.client: simclient.Client = simclient.Client("http://127.0.0.0:8000")
        self.calc_only = False
        self.min_elevation = SatSimulation.MIN_ELEVATION
        self.zero_uplink_count = 0
        self.uplink_updates = 0
        self.moving_stations: list[MovingStation] = []  # Changed from self.vessels

        for name in torus_topo.ground_stations(graph):
            node = graph.nodes[name]
            position = wgs84.latlon(node[torus_topo.LAT], node[torus_topo.LON])
            ground_station = GroundStation(name, position)
            self.ground_stations.append(ground_station)

        for name in torus_topo.satellites(graph):
            orbit = graph.nodes[name]["orbit"]
            ts = load.timescale()
            l1, l2 = orbit.tle_format()
            earth_satellite = EarthSatellite(l1, l2, name, ts)
            satellite = Satellite(name, earth_satellite)
            self.satellites.append(satellite)

        # Initialize vessels
        for name in torus_topo.vessels(graph):
            node = graph.nodes[name]
            position = wgs84.latlon(node[torus_topo.LAT], node[torus_topo.LON])
            # Convert tuple waypoints to Waypoint objects
            waypoints = [Waypoint(lat=wp[0], lon=wp[1]) for wp in node["waypoints"]]
            moving_station = MovingStation(
                name=name,
                position=position,
                waypoints=waypoints  # Now passing a list of Waypoint objects
            )
            self.moving_stations.append(moving_station)   

    def updatePositions(self, future_time: datetime.datetime):
        sfield_time = self.ts.from_datetime(future_time)
        positions = []
        ground_positions = []
        vessel_positions = []

        # Update satellite positions
        for satellite in self.satellites:
            satellite.geo = satellite.earth_sat.at(sfield_time)
            lat, lon = wgs84.latlon_of(satellite.geo)
            satellite.lat = lat
            satellite.lon = lon
            satellite.height = wgs84.height_of(satellite.geo)
            
            # Create position update
            position = simapi.SatellitePosition(
                name=satellite.name,
                lat=float(satellite.lat.degrees),
                lon=float(satellite.lon.degrees),
                height=float(satellite.height.km)
            )
            positions.append(position)

        # Add ground station positions
        for station in self.ground_stations:
            ground_pos = simapi.GroundStationPosition(
                name=station.name,
                lat=float(station.position.latitude.degrees),
                lon=float(station.position.longitude.degrees)
            )
            ground_positions.append(ground_pos)

        # Update moving station positions
        for station in self.moving_stations:
            station.update_position()  # Add this line to update vessel positions
            vessel_pos = simapi.VesselPosition(
                name=station.name,
                lat=float(station.position.latitude.degrees),
                lon=float(station.position.longitude.degrees)
            )
            vessel_positions.append(vessel_pos)

        # Collect satellite-to-satellite links
        satellite_links = []
        for node1, node2 in self.graph.edges():
            if node1.startswith('R') and node2.startswith('R'):  # Satellite nodes start with R
                status = self.graph.edges[node1, node2].get("up", True)
                satellite_links.append(simapi.Link(
                    node1_name=node1,
                    node2_name=node2,
                    up=status
                ))

        # Collect both ground station and vessel uplinks
        ground_uplinks = []
        all_stations = self.ground_stations + self.moving_stations
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
            
        # Send position updates to API
        data = simapi.GraphData(
            satellites=positions,
            ground_stations=ground_positions,
            vessels=vessel_positions,
            satellite_links=satellite_links,
            ground_uplinks=ground_uplinks
        )
        self.client.update_positions(data)
        #print(f"{satellite.name} Lat: {satellite.lat}, Lon: {satellite.lon}, Hieght: {satellite.height.km}km")
        print(f"{station.name} Lat: {station.position.latitude.degrees}, Lon: {station.position.longitude.degrees}")

    @staticmethod
    def nearby(ground_station: GroundStation, satellite: Satellite) -> bool:
        return (satellite.lon.degrees > ground_station.position.longitude.degrees - 20 and
                satellite.lon.degrees < ground_station.position.longitude.degrees + 20 and
                satellite.lat.degrees > ground_station.position.latitude.degrees - 20 and 
                satellite.lat.degrees < ground_station.position.latitude.degrees + 20)
 
    def updateUplinkStatus(self, future_time: datetime.datetime):
        '''
        Update the links between ground stations and satellites
        '''
        self.uplink_updates += 1
        zero_uplinks: bool = False

        sfield_time = self.ts.from_datetime(future_time)
        # Combined list for both types of stations
        all_stations = self.ground_stations + self.moving_stations
        
        for station in all_stations:
            station.uplinks = [] 
            for satellite in self.satellites:
                # Calculate az for close satellites
                if SatSimulation.nearby(station, satellite):
                    difference = satellite.earth_sat - station.position
                    topocentric = difference.at(sfield_time)
                    alt, az, d = topocentric.altaz()
                    if alt.degrees > self.min_elevation:
                        uplink = Uplink(satellite.name, station.name, d.km)
                        station.uplinks.append(uplink)
                        print(f"{satellite.name} Lat: {satellite.lat}, Lon: {satellite.lon}")
                        print(f"{station.name} Lat: {station.position.latitude}, Lon: {station.position.longitude}")
                        print(f"ground/vessel {station.name}, sat {satellite.name}: {alt}, {az}, {d.km}")
            if len(station.uplinks) == 0:
                zero_uplinks = True
            if zero_uplinks:
                self.zero_uplink_count += 1
            

    def updateInterPlaneStatus(self):
        inclination = self.graph.graph["inclination"]
        for satellite in self.satellites:
            # Track if state changed
            satellite.prev_inter_plane_status = satellite.inter_plane_status
            if satellite.lat.degrees > (inclination - 2) or satellite.lat.degrees < (
                -inclination + 2
            ):
                # Above the threashold for inter plane links to connect
                satellite.inter_plane_status = False
            else:
                satellite.inter_plane_status = True

    def send_updates(self):
        for satellite in self.satellites:
            if satellite.prev_inter_plane_status != satellite.inter_plane_status:
                for neighbor in self.graph.adj[satellite.name]: 
                    if self.graph.edges[satellite.name, neighbor]["inter_ring"]:
                        self.client.set_link_state(satellite.name, neighbor, satellite.inter_plane_status)
        
        # for ground_station in self.ground_stations:
        #     links = []
        #     for uplink in ground_station.uplinks:
        #         links.append((uplink.satellite_name, int(uplink.distance)))
        #     self.client.set_uplinks(ground_station.name, links)
        
        all_stations = self.ground_stations + self.moving_stations
        for station in all_stations:
            links = []
            for uplink in station.uplinks:
                links.append((uplink.satellite_name, int(uplink.distance)))
            self.client.set_uplinks(station.name, links)

    def run(self):
        current_time = datetime.datetime.now(tz=datetime.timezone.utc)
        slice_delta = datetime.timedelta(seconds=SatSimulation.TIME_SLICE)

        # Generate positions for current time
        print(f"update positions for {current_time}")
        self.updatePositions(current_time)
        self.updateUplinkStatus(current_time)
        self.updateInterPlaneStatus()
        self.send_updates()

        while True:
            # Generate positions for next time step
            future_time = current_time + slice_delta
            print(f"update positions for {future_time}")
            self.updatePositions(future_time)
            self.updateUplinkStatus(future_time)
            self.updateInterPlaneStatus()
            sleep_delta = future_time - datetime.datetime.now(tz=datetime.timezone.utc)
            print(f"zero uplink % = {self.zero_uplink_count / self.uplink_updates}")
            print("sleep")
            if not self.calc_only:
                # Wait until next time step thenupdate
                time.sleep(sleep_delta.seconds)
                self.send_updates()
            current_time = future_time


def run(num_rings: int, num_routers: int, ground_stations: bool, min_elev: int, calc_only: bool, ground_station_data: dict, vessel_data: dict = None) -> None:
    '''
    Simulate physical positions of satellites.

    num_rings: number of orbital rings
    num_routers: number of satellites on each ring
    ground_stations: True if groundstations are included
    min_alt: Minimum angle (degrees) above horizon needed to connect to the satellite
    calc_only: If True, only loop quicky dumping results to the screen
    '''
    graph = torus_topo.create_network(num_rings, num_routers, ground_stations, ground_station_data, vessel_data, inclination, altitude)
    sim: SatSimulation = SatSimulation(graph)
    sim.min_elevation = min_elev
    sim.calc_only = calc_only
    sim.run()


def usage():
    print("Usage: sim_sat [config-file] [--calc-ony]")

if __name__ == "__main__":
    calc_only = False
    if "--calc-only" in sys.argv:
        # Only run calculations in a loop reporting data to the screen
        calc_only = True
        sys.argv.remove("--calc-only")

    if len(sys.argv) > 2:
        usage()
        sys.exit(-1)
        
    parser = configparser.ConfigParser()
    parser.optionxform = str  # Retain case sensitivity
    parser['network'] = {}
    parser['physical'] = {}
    try:
        if len(sys.argv) == 2:
            parser.read(sys.argv[1])
    except Exception as e:
        print(str(e))
        usage()
        sys.exit(-1)

    ground_station_data = {}
    if 'ground_stations' in parser:
        print("Ground Stations True")
        for name, coords in parser['ground_stations'].items():
            lat, lon = map(float, coords.split(','))
            ground_station_data[name] = (lat, lon)
    else:
        print("Ground Stations False")

    vessel_data = {}
    if 'vessels' in parser:
        print("Vessels True")
        for name, waypoint_str in parser['vessels'].items():
            waypoints = []
            for waypoint in waypoint_str.split(';'):
                lat, lon = map(float, waypoint.split(','))
                waypoints.append((lat, lon))
            vessel_data[name] = waypoints
    else:
        print("Vessels False")

    num_rings = parser['network'].getint('rings', 4)
    num_routers = parser['network'].getint('routers', 4)
    # Should ground stations be included in the network?
    ground_stations = parser['network'].getboolean('ground_stations', False)
    # Minimum angle above horizon needed to connect to satellites
    min_alt = parser['physical'].getint('min_elevation', SatSimulation.MIN_ELEVATION)
    inclination = parser['constellation'].getfloat('inclination', 53.9)
    altitude = parser['constellation'].getfloat('altitude', 550)


    print(f"Running {num_rings} rings with {num_routers} per ring, ground stations {ground_stations}")
    run(num_rings, num_routers, ground_stations, min_alt, calc_only, ground_station_data, vessel_data)

