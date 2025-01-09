'''
Definitions for elements of the Simulator API

The server side of the API is implemented in mnet/driver.py
The client side is implemented in mnet/client.py
'''

from pydantic import BaseModel

class Link(BaseModel):
    node1_name: str
    node2_name: str
    up: bool

class UpLink(BaseModel):
    sat_node: str
    distance: int

class UpLinks(BaseModel):
    ground_node: str
    uplinks: list[UpLink]

class SatellitePosition(BaseModel):
    name: str
    lat: float
    lon: float
    height: float

class GroundStationPosition(BaseModel):
    name: str
    lat: float
    lon: float

class SatellitePositions(BaseModel):
    satellites: list[SatellitePosition]
    ground_stations: list[GroundStationPosition]
    satellite_links: list[Link]
    ground_uplinks: list[UpLinks]