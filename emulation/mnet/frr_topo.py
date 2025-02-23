'''
This module provides a comprehensive framework for simulating a network of routers, satellites, 
and ground stations using Mininet and FRR (Free Range Routing). The simulation supports dynamic 
network topologies, monitoring, and routing configurations.

Key Features:
    - Custom Mininet node types with advanced routing capabilities.
    - Ground station and satellite network emulation.
    - Integration with FRR for dynamic routing protocols like OSPF.
    - Dynamic uplink management and monitoring.

Classes:
    - `RouteNode`: Mininet node with support for loopback interfaces.
    - `MNetNodeWrap`: Wrapper class for managing Mininet nodes.
    - `GroundStation`: Specialized node for simulating ground stations.
    - `FrrRouter`: Node for simulating FRR routers.
    - `NetxTopo`: Topology for building virtual networks using a `networkx.Graph`.
    - `FrrSimRuntime`: Runtime manager for controlling the simulation.

Dependencies:
    - Mininet
    - NetworkX
    - FRR (Free Range Routing)
    - SQLite for monitoring and status tracking.
'''

from typing import Tuple
import os
import grp
import pwd
import ipaddress
import tempfile
import datetime
import shutil
import random
import socket
import typing
from dataclasses import dataclass, field

import networkx
import mininet.topo
import mininet.node
import mininet.net
import mininet.link
import mininet.util

from emulation import torus_topo
from emulation import frr_config_topo
from emulation import simapi
from emulation.mnet import pmonitor




class RouteNode(mininet.node.Node):
    '''
    Mininet node with a loopback.
    Supports FrrRouters and ground sations.

    Includes an optional loopback interface with a /31 subnet mask
    '''

    def __init__(self, name, **params):
        mininet.node.Node.__init__(self, name, **params)

        # Optional loopback interface
        self.loopIntf = None

    def defaultIntf(self):
        # If we have a loopback, that is the default interface.
        # Otherwise use mininet default behavior.
        if self.loopIntf is not None:
            return self.loopIntf
        return super().defaultIntf()

    def config(self, **params):
        '''
        Configure the node and create a loopback interface if needed.

        Args:
            params (dict): Configuration parameters, including `ip` for setting a default IP address.

        Creates:
            - A loopback interface if no matching interface is found for the specified IP.
        '''

        # If we have a default IP and it is not an existing interface, create a
        # loopback.
        if params.get("ip") is not None:
            match_found = False
            ip = format(ipaddress.IPv4Interface(params.get("ip")).ip)
            for intf in self.intfs.values():
                if intf.ip == ip:
                    match_found = True
            if not match_found:
                # Make a default interface
                mininet.util.quietRun("ip link add name loop type dummy")
                self.loopIntf = mininet.link.Intf(name="loop", node=self)

        super().config(**params)

    def setIP(self, ip):
        '''
        Set the IP address for the node.

        Args:
            ip (str): The IP address to set for the node.
        '''

        # What is this for?
        mininet.node.Node.setIP(self, ip)



class MNetNodeWrap:
    '''
    '''

    def __init__(self, name : str, default_ip: str) -> None:
        self.name : str = name
        self.default_ip : str = default_ip
        self.node : mininet.node.Node = None
        fd, self.working_db = tempfile.mkstemp(suffix=".sqlite")
        open(fd, "r").close()
        print(f"{self.name} db file {self.working_db}")
        self.last_five_pings = []
 
    def sendCmd(self, command :str):
        '''
        Send a command to the node for execution.

        Args:
            command (str): The command to execute on the node.
        '''

        if self.node is not None:
            self.node.sendCmd(command)

    def start(self, net: mininet.net.Mininet) -> None:
        '''
        Initialize the Mininet node after the network has started.

        Args:
            net (mininet.net.Mininet): The Mininet network instance.
        '''

        self.node = net.getNodeByName(self.name)

    def waitOutput(self) -> None:
        if self.node is not None:
            self.node.waitOutput()

    def stop(self) -> None:
        '''
        Perform cleanup operations before stopping the node.
        '''

        pass

    def startMonitor(self, db_master_file, db_master):
        '''
        Start monitoring for the node.

        Args:
            db_master_file (str): Path to the master database file.
            db_master (sqlite3.Connection): Open connection to the master database.
        '''

        print(f"start monitor {self.name}:{self.defaultIP()}")
        self.sendCmd(
            f"python3 -m emulation.mnet.pmonitor monitor '{db_master_file}' '{self.working_db}' {self.defaultIP()} >> /dev/null 2>&1  &"
        )
        pmonitor.set_running(db_master, self.defaultIP(), True)

    def stopMonitor(self, db_master):
        '''
        Stop monitoring for the node and clean up associated resources.

        Args:
            db_master (sqlite3.Connection): Open connection to the master database.
        '''

        pmonitor.set_can_run(db_master, self.defaultIP(), False)
        os.unlink(self.working_db)

    def update_monitor_stats(self) -> Tuple[int, int]:
        '''
        Update the monitoring statistics for the node.

        Returns:
            tuple[int, int]: Counts of successful and total samples.
        '''
        # Only get stats if DB is being used
        if os.path.getsize(self.working_db) > 0:
            db = pmonitor.open_db(self.working_db)
            good, total = pmonitor.get_status_count(db, self.stable_node())
            self.last_five_pings = pmonitor.get_last_five(db)
            db.close()
            return good, total

        # Return default values when DB is not in use or empty
        return 0, 0
 
    def defaultIP(self) -> str:
        '''
        Get the default IP address of the node's interface.

        Returns:
            str: The default IP address.
        '''

        if self.node is not None and self.node.defaultIntf() is not None:
            return self.node.defaultIntf().ip
        return self.default_ip

    def stable_node(self) -> bool:
        '''
        Check if the node is expected to always be reachable.

        Returns:
            bool: True if the node is stable, False otherwise.
        '''

        return True


@dataclass
class IPPoolEntry:
    network: ipaddress.IPv4Network
    ip1: ipaddress.IPv4Interface
    ip2: ipaddress.IPv4Interface
    used: bool = False


@dataclass
class Uplink:
    sat_name: str
    distance: int
    ip_pool_entry: IPPoolEntry
    default: bool = False


class GroundStation(MNetNodeWrap):
    '''
    State for a Ground Station

    Tracks established uplinks to satellites.
    Not a mininet node.
    '''

    def __init__(self, name: str, default_ip: str, uplinks: list[dict[str,typing.Any]]) -> None:
        super().__init__(name, default_ip)
        self.uplinks: list[Uplink] = []
        self.ip_pool: list[IPPoolEntry] = []
        for link in uplinks:
            entry = IPPoolEntry(network=link["nw"], ip1=link["ip1"], ip2=link["ip2"])
            self.ip_pool.append(entry)

    def stable_node(self) -> bool:
        '''
        Indicate that the ground station is not expected to be always reachable.

        Overrides:
            `MNetNodeWrap.stable_node`.

        Returns:
            bool: Always returns False.
        '''

        return False

    def has_uplink(self, sat_name: str) -> bool:
        for uplink in self.uplinks:
            if uplink.sat_name == sat_name:
                return True
        return False

    def sat_links(self) -> list[str]:
        '''
        Return a list of satellite names to which we have uplinks
        '''
        return [uplink.sat_name for uplink in self.uplinks]

    def _get_pool_entry(self) -> IPPoolEntry | None:
        for entry in self.ip_pool:
            if not entry.used:
                entry.used = True
                return entry
        return None

    def add_uplink(self, sat_name: str, distance: int) -> Uplink | None:
        '''
        Add an uplink to a satellite.

        Args:
            sat_name (str): Name of the satellite.
            distance (int): Distance to the satellite.

        Returns:
            Uplink | None: The created uplink, or None if no available IP pool entry.
        '''

        pool_entry = self._get_pool_entry()
        if pool_entry is None:
            return None
        uplink = Uplink(sat_name, distance, pool_entry)
        self.uplinks.append(uplink)
        return uplink

    def remove_uplink(self, sat_name: str) -> Uplink|None:
        '''
        Remove an uplink to a satellite.

        Args:
            sat_name (str): Name of the satellite.

        Returns:
            Uplink | None: The removed uplink, or None if the uplink doesn't exist.
        '''

        for entry in self.uplinks:
            if entry.sat_name == sat_name:
                entry.ip_pool_entry.used = False
                self.uplinks.remove(entry)
                return entry
        return None


class FrrRouter(MNetNodeWrap):
    '''
    Support an FRR router under mininet.
    - handles the the FRR config files, starting and stopping FRR.
    Does not cleanup config files.
    '''

    CFG_DIR = "/etc/frr/{node}"
    VTY_DIR = "/var/frr/{node}/{daemon}.vty"
    LOG_DIR = "/var/log/frr/{node}"

    def __init__(self, name: str, default_ip: str):
        super().__init__(name, default_ip)
        self.no_frr = False
        self.vtysh = None
        self.daemons = None
        self.ospf = None

    def configure(self, vtysh: str, daemons: str, ospf: str) -> None:
        '''
        Set the configuration files for the router.

        Args:
            vtysh (str): Contents of the `vtysh.conf` file.
            daemons (str): Contents of the `daemons` file.
            ospf (str): Contents of the `frr.conf` file.
        '''

        self.vtysh = vtysh
        self.daemons = daemons
        self.ospf = ospf

    def write_configs(self) -> None:
        '''
        Write the router's configuration files to the appropriate directories.

        Creates:
            - Configuration files for the router, if not already present.
            - Logging and configuration directories for the router.
        '''

        # Get frr config and save to frr config directory
        cfg_dir = FrrRouter.CFG_DIR.format(node=self.name)
        log_dir = FrrRouter.LOG_DIR.format(node=self.name)

        # Suport this for running without mininet / FRR
        if self.no_frr:
            print("Warning: not running FRR")
            return

        uinfo = pwd.getpwnam("frr")

        if not os.path.exists(cfg_dir):
            # sudo install -m 775 -o frr -g frrvty -d {cfg_dir}
            print(f"create {cfg_dir}")
            os.makedirs(cfg_dir, mode=0o775)
            gid = grp.getgrnam("frrvty").gr_gid
            os.chown(cfg_dir, uinfo.pw_uid, gid)

        # sudo install -m 775 -o frr -g frr -d  {log_dir}
        if not os.path.exists(log_dir):
            print(f"create {log_dir}")
            os.makedirs(log_dir, mode=0o775)
            os.chown(log_dir, uinfo.pw_uid, uinfo.pw_gid)

        self.write_cfg_file(
            f"{cfg_dir}/vtysh.conf", self.vtysh, uinfo.pw_uid, uinfo.pw_gid
        )
        self.write_cfg_file(
            f"{cfg_dir}/daemons", self.daemons, uinfo.pw_uid, uinfo.pw_gid
        )
        self.write_cfg_file(
            f"{cfg_dir}/frr.conf", self.ospf, uinfo.pw_uid, uinfo.pw_gid
        )

    def start(self, net: mininet.net.Mininet) -> None:
        super().start(net)
        if self.node is None:
            self.no_frr = True
        self.write_configs()
        # Start frr daemons
        print(f"start router {self.name}")
        self.sendCmd(f"/usr/lib/frr/frrinit.sh start '{self.name}'")

    def stop(self):
        super().stop()
        # Cleanup and stop frr daemons
        print(f"stop router {self.name}")
        self.sendCmd(f"/usr/lib/frr/frrinit.sh stop '{self.name}'")

    def config_frr(self, daemon: str, commands: list[str]) -> bool:
        '''
        Send configuration commands to an FRR daemon.

        Args:
            daemon (str): Name of the FRR daemon (e.g., "ospfd").
            commands (list[str]): List of configuration commands.

        Returns:
            bool: True if all commands were successfully executed, False otherwise.
        '''

        if self.node is None:
            # Running in stub mode
            return True

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        path = FrrRouter.VTY_DIR.format(node=self.name, daemon=daemon)
        result = True
        try:
            sock.connect(path)
            msg = b'enable\x00'
            result = result and self._send_frr_cmd(sock, msg)
            msg = b'conf term file-lock\x00'
            result = result and self._send_frr_cmd(sock, msg)
            for command in commands:
                print(f"sending command {command} to {self.name}")
                msg = (command + '\x00').encode("ascii")
                result = result and self._send_frr_cmd(sock, msg)
            msg = b'end\x00'
            self._send_frr_cmd(sock, msg)
            msg = b'disable\x00'
            self._send_frr_cmd(sock, msg)
        except TimeoutError:
            print("timout connecting to FRR")
            result = False
        sock.close()
        return result

    def _send_frr_cmd(self, sock, msg: bytes) -> bool:
        sock.sendall(msg)
        data = sock.recv(10000)
        size = len(data)
        if size > 0 and data[size-1] == 0:
            return True
        return False

    def write_cfg_file(self, file_path: str, contents: str, uid: int, gid: int) -> None:
        if self.no_frr:
            return

        print(f"write {file_path}")
        with open(file_path, "w") as f:
            f.write(contents)
            f.close()
        os.chmod(file_path, 0o640)
        os.chown(file_path, uid, gid)


class StubMininet:
    '''
    In order to run and test with out standing up an entire mininet environment (that is run as root),
    we can stub out the mininet calls. This results in the mininet nodes being returned as None and code
    needs to handle this case.
    '''
    def __init__(self):
        pass

    def configLinkStatus(self, node1: str, node2: str, state: str):
        pass

    def linksBetween(self, node1, node2):
        return []

    def getNodeByName(self, name):
        return None
    
    def addLink(self, node1: str, node2: str, params1: dict, params2: dict):
        pass
    
    def delLinkBetween(self, node1, node2):
        pass


class NetxTopo(mininet.topo.Topo):
    '''
    Mininet topology object used to build the virtual network.
    '''
    def __init__(self, graph: networkx.Graph):
        self.graph = graph
        self.routers: list[FrrRouter] = []
        self.ground_stations: list[GroundStation] = []
        self.vessels: list[Vessel] = []  # Add vessels list
        super().__init__()

    def build(self, *args, **params):
        '''
        Construct the Mininet topology based on the networkx.Graph structure.

        Creates:
            - Mininet hosts for routers and ground stations.
            - Links between nodes based on the graph edges.
        '''
        # Create routers
        for name in torus_topo.satellites(self.graph):
            node = self.graph.nodes[name]
            ip = node.get("ip")
            ip_intf = None
            ip_addr = None
            if ip is not None:
                ip_intf = format(ip)
                ip_addr = format(ip.ip)
            self.addHost(
                name,
                cls=RouteNode,
                ip=ip_intf)

            frr_router: FrrRouter = FrrRouter(name, ip_addr) 
            self.routers.append(frr_router)
            frr_router.configure(
                ospf=node["ospf"],
                vtysh=node["vtysh"],
                daemons=node["daemons"]
            )

        # Handle ground stations
        for name in torus_topo.ground_stations(self.graph):
            node = self.graph.nodes[name]
            ip = node.get("ip")
            ip_intf = None
            ip_addr = None
            if ip is not None:
                ip_intf = format(ip)
                ip_addr = format(ip.ip)
            self.addHost(name, cls=RouteNode, ip=ip_intf)
            station = GroundStation(name, ip_addr, node["uplinks"])
            self.ground_stations.append(station)

        # Handle vessels
        for name in torus_topo.vessels(self.graph):
            node = self.graph.nodes[name]
            ip = node.get("ip")
            ip_intf = None
            ip_addr = None
            if ip is not None:
                ip_intf = format(ip)
                ip_addr = format(ip.ip)
            self.addHost(name, cls=RouteNode, ip=ip_intf)
            vessel = Vessel(name, ip_addr, node["uplinks"])
            self.vessels.append(vessel)

        # Create links between routers
        for name, edge in self.graph.edges.items():
            router1 = name[0]
            router2 = name[1]

            # Handle incomplete edges
            if edge.get("ip") is None:
                self.addLink(router1, router2)
                return

            ip1 = edge["ip"][router1]
            intf1 = edge["intf"][router1]

            ip2 = edge["ip"][router2]
            intf2 = edge["intf"][router2]

            self.addLink(
                router1,
                router2,
                intfName1=intf1,
                intfName2=intf2,
                params1={"ip": format(ip1), "delay": "1ms"},
                params2={"ip": format(ip2), "delay": "1ms"},
                cls=mininet.link.TCLink, 
            )


class FrrSimRuntime:
    '''
    Code for the FRR / Mininet / Monitoring functions.
    '''
    def __init__(self, topo: NetxTopo, net: mininet.net.Mininet, stable_monitor: bool =False):
        self.graph = topo.graph
        self.nodes: dict[str, MNetNodeWrap] = {}
        self.routers: dict[str, FrrRouter] = {}
        self.ground_stations: dict[str, GroundStation] = {}
        self.vessels: dict[str, Vessel] = {}
        self.stable_monitor = stable_monitor

        # Create monitoring DB file.
        fd, self.db_file = tempfile.mkstemp(suffix=".sqlite")
        open(fd, "r").close()
        print(f"Master db file {self.db_file}")

        for frr_router in topo.routers:
            self.nodes[frr_router.name] = frr_router
            self.routers[frr_router.name] = frr_router
        for ground_station in topo.ground_stations:
            self.nodes[ground_station.name] = ground_station
            self.ground_stations[ground_station.name] = ground_station
        for vessel in topo.vessels:  # Add vessels to nodes
            self.nodes[vessel.name] = vessel
            self.vessels[vessel.name] = vessel

        self.stat_samples = []
        self.net = net
        self.stub_net = False
        # If net is none, we are running in a stub mode without mininet or FRR.
        if self.net is None:
            self.net = StubMininet()
            self.stub_net = True

    def start_routers(self) -> None:
        '''
        Start all routers and monitoring processes in the simulation.

        Populates the monitoring database with target information and launches
        monitoring threads for dynamic and stable nodes.
        '''
 
        # Populate master db file
        data = []
        # Stable targets - to monitor
        for router in self.routers.values():
            data.append((router.name, router.defaultIP(), router.stable_node()))
        # Not stable targets - don't monitor
        #GroundStations
        for station in self.ground_stations.values():
            data.append((station.name, station.defaultIP(), station.stable_node()))
        pmonitor.init_targets(self.db_file, data)
        #Vessels
        for vessel in self.vessels.values(): 
            data.append((vessel.name, vessel.defaultIP(), vessel.stable_node()))  # <-- Add this
        pmonitor.init_targets(self.db_file, data)

        # Start all nodes
        for node in self.nodes.values():
            node.start(self.net)

        # Wait for start to complete.
        for node in self.nodes.values():
            node.waitOutput()

        # Start monitoring on all nodes
        db_master = pmonitor.open_db(self.db_file)
        for node in self.nodes.values():
            # Start monitor if node is not considered always reachable
            # or we are running monitoring from the stable nodes.
            if self.stable_monitor or not node.stable_node():
                node.startMonitor(self.db_file, db_master)
        db_master.close()

        # Wait for monitoring to start
        for node in self.nodes.values():
            if self.stable_monitor or not node.stable_node():
                node.waitOutput()

    def stop_routers(self):
        '''
        Stop all routers and monitoring processes in the simulation.

        Performs cleanup of monitoring databases and network resources.
        '''

        # Stop monitor on all nodes
        db_master = pmonitor.open_db(self.db_file)
        for node in self.nodes.values():
            node.stopMonitor(db_master)
        db_master.close()

        for node in self.nodes.values():
            node.stop()

        # Wait for commands to complete - important!.
        # Otherwise processes may not shut down.
        for node in self.nodes.values():
            node.waitOutput()
        os.unlink(self.db_file)

    def update_monitor_stats(self):
        '''
        Update monitoring statistics for all nodes in the simulation.

        Updates the `stat_samples` list with the latest counts of successful and total samples.
        '''

        stable_good_count: int = 0
        stable_total_count: int = 0
        dynamic_good_count: int = 0
        dynamic_total_count: int = 0

        if self.stub_net:
            stable_good_count: int = random.randrange(20)
            stable_total_count: int = random.randrange(20) + stable_good_count
            dynamic_good_count: int = random.randrange(20)
            dynamic_total_count: int = random.randrange(20) + dynamic_good_count
        else:
            for node in self.nodes.values():
                good, total = node.update_monitor_stats()
                if node.stable_node():
                    stable_good_count += good
                    stable_total_count += total
                else:
                    dynamic_good_count += good
                    dynamic_total_count += total

        self.stat_samples.append((datetime.datetime.now(), 
                                    stable_good_count, stable_total_count,
                                    dynamic_good_count, dynamic_total_count))
        if len(self.stat_samples) > 200:
            self.stat_samples.pop(0)

    def get_last_five_stats(self) -> dict[str, list[tuple[str,bool]]]:
        '''
        Retrieve the last five monitoring samples for each node.

        Returns:
            dict[str, list[tuple[str, bool]]]: Dictionary mapping node names to their last five samples.
        '''

        result: dict[str, list[tuple[str,bool]]] = {}
        for node in self.nodes.values():
            result[node.name] = node.last_five_pings
        return result

    def sample_stats(self):
        self.update_monitor_stats()

    def get_node_status_list(self, name: str):
        node = self.nodes[name]
        result = []
        if not self.stub_net and os.path.getsize(node.working_db) > 0:
            db_working = pmonitor.open_db(node.working_db)
            result = pmonitor.get_status_list(db_working)
            db_working.close()
        return result

    def get_stat_samples(self):
        return self.stat_samples

    def get_topo_graph(self) -> networkx.Graph:
        return self.graph

    def get_ring_list(self) -> list[list[str]]:
        return self.graph.graph["ring_list"]

    def get_router_list(self) -> list[tuple[str,str]]:
        result = []
        for name in torus_topo.satellites(self.graph):
            node = self.graph.nodes[name]
            ip = ""
            if node.get("ip") is not None:
                ip = format(node.get("ip"))
            else:
                ip = ""
            result.append((name, ip))
        return result

    def get_link_list(self) -> list[tuple[str,str,str]]:
        result = []
        for edge in self.graph.edges:
            node1 = edge[0]
            node2 = edge[1]
            ip_str = []
            for ip in self.graph.edges[node1, node2]["ip"].values():
                ip_str.append(format(ip))
            result.append((node1, node2, "-".join(ip_str)))
        return result

    def get_link(self, node1: str, node2: str):
        if self.graph.nodes.get(node1) is None:
            return f"{node1} does not exist"
        if self.graph.nodes.get(node2) is None:
            return f"{node2} does not exist"
        edge = self.graph.adj[node1].get(node2)
        if edge is None:
            return f"link {node1}-{node2} does not exist"
        return (node1, node2, edge["ip"][node1], edge["ip"][node2])

    def get_router(self, name: str):
        if self.graph.nodes.get(name) is None:
            return f"{name} does not exist"
        result = {"name": name, "ip": self.graph.nodes[name].get("ip"), "neighbors": {}}
        for neighbor in self.graph.adj[name].keys():
            edge = self.graph.adj[name][neighbor]
            result["neighbors"][neighbor] = {
                "ip_local": edge["ip"][name],
                "ip_remote": edge["ip"][neighbor],
                "up": self.get_link_state(name, neighbor),
                "intf_local": edge["intf"][name],
                "intf_remote": edge["intf"][neighbor],
            }
        return result

    def get_ground_stations(self) -> list[GroundStation]:
        return [x for x in self.ground_stations.values()]

    def get_station(self, name):
        return self.ground_stations[name]

    def set_link_state(
        self, node1: str, node2: str, state_up: bool):
        if self.graph.nodes.get(node1) is None:
            return f"{node1} does not exist"
        if self.graph.nodes.get(node2) is None:
            return f"{node2} does not exist"
        adj = self.graph.adj[node1].get(node2)
        if self.graph.adj[node1].get(node2) is None:
            return f"{node1} to {node2} does not exist"
        self._config_link_state(node1, node2, state_up)
        return None

    def _config_link_state(
        self, node1: str, node2: str, state_up: bool 
    ):
        state = "up" if state_up else "down"
        self.net.configLinkStatus(node1, node2, state)

    def get_link_state(self, node1: str, node2: str) -> tuple[bool, bool]:
        n1 = self.net.getNodeByName(node1)
        n2 = self.net.getNodeByName(node2)
        links = self.net.linksBetween(n1, n2)
        if len(links) > 0:
            link = links[0]
            return link.intf1.isUp(), link.intf2.isUp()

        return False, False

    def set_station_uplinks(self, station_name: str, uplinks: list[simapi.UpLink]) -> bool:
        """Handle uplinks for both ground stations and vessels"""
        # Check if it's a ground station or vessel
        station = None
        if station_name in self.ground_stations:
            station = self.ground_stations[station_name]
        elif station_name in self.vessels:
            station = self.vessels[station_name]
        else:
            return False

        # Determine which links should be removed
        next_list = [uplink.sat_node for uplink in uplinks]
        for sat_name in station.sat_links():
            if sat_name not in next_list:
                print(f"Remove uplink {station.name} - {sat_name}")
                uplink = station.remove_uplink(sat_name)
                self._remove_link(
                        station_name, 
                        sat_name, 
                        uplink.ip_pool_entry.network,
                        uplink.ip_pool_entry.ip1)

        # Add any new links
        for link in uplinks:
            if not station.has_uplink(link.sat_node):
                print(f"Add uplink {station.name}- {link.sat_node}")
                uplink = station.add_uplink(link.sat_node, link.distance)
                if uplink is not None:
                    self._create_uplink(
                        station_name,
                        link.sat_node,
                        uplink.ip_pool_entry.network,
                        uplink.ip_pool_entry.ip1,
                        uplink.ip_pool_entry.ip2,
                        )
        self._update_default_route(station)
        return True

    def _update_dns_for_uplink(self, station_name: str, sat_name: str, ip1: ipaddress.IPv4Interface, ip2: ipaddress.IPv4Interface, add: bool = True):
        '''
        Update DNS entries for a dynamic uplink.
        
        Args:
            station_name: Name of the ground station
            sat_name: Name of the satellite
            ip1: Ground station's interface IP
            ip2: Satellite's interface IP
            add: True to add entries, False to remove them
        '''
        # Create DNS entries for both ends of the uplink
        dns_entries = [
            f"{format(ip1.ip)}\t{station_name}-TO-{sat_name} {station_name}-uplink",
            f"{format(ip2.ip)}\t{sat_name}-TO-{station_name} {sat_name}-downlink"
        ]
        
        # Update hosts file in each network namespace
        for node in self.net.hosts:
            if add:
                # Add new entries
                for entry in dns_entries:
                    node.cmd(f'echo "{entry}" >> /etc/netns/{node.name}/hosts')
                    node.cmd(f'echo "{entry}" >> /etc/hosts')
            else:
                # Remove entries
                for entry in dns_entries:
                    node.cmd(f'sed -i "/{entry}/d" /etc/netns/{node.name}/hosts')
                    node.cmd(f'sed -i "/{entry}/d" /etc/hosts')

    def _create_uplink(
        self,
        station_name: str,
        sat_name: str,
        ip_nw: ipaddress.IPv4Network,
        ip1: ipaddress.IPv4Interface,
        ip2: ipaddress.IPv4Interface,
    ):
        # Create the link
        self.net.addLink(
            station_name, sat_name, 
            params1={"ip": format(ip1), "delay": "1ms"}, 
            params2={"ip": format(ip2), "delay": "1ms"},
            cls=mininet.link.TCLink, 
        )

        # Get correct station object
        if station_name in self.ground_stations:
            station = self.ground_stations[station_name]
        elif station_name in self.vessels:
            station = self.vessels[station_name]
        else:
            raise ValueError(f"Unknown station {station_name}")

        frr_router = self.routers[sat_name]

        # Configure static route and OSPF
        frr_router.config_frr("staticd", [f"ip route {station.defaultIP()}/32 {format(ip1.ip)}"])
        ospf_commands = [
            "router ospf",
            f"network {format(ip_nw)} area 0",
            f"network {station.defaultIP()}/32 area 0",
            "exit"
        ]
        frr_router.config_frr("ospfd", ospf_commands)

        # Add DNS entries for the uplink
        self._update_dns_for_uplink(station_name, sat_name, ip1, ip2, add=True)

        # Add default route on station
        station_node = self.net.getNodeByName(station_name)
        if station_node is not None:
            route = f"via {format(ip2.ip)}"
            station_node.cmd(f'ip route add default {route}')


    def _remove_link(self, station_name: str, sat_name: str, ip_nw: ipaddress.IPv4Network, ip: ipaddress.IPv4Interface) -> None:
        station_node = self.net.getNodeByName(station_name)
        sat_node = self.net.getNodeByName(sat_name)
        
        # Check if uplinks are available
        if not self.ground_stations[station_name].uplinks:
            raise ValueError(f"No uplinks exist for station {station_name}. Cannot remove link.")
        
        # Remove DNS entries before removing the link
        uplink = self.ground_stations[station_name].uplinks[0]  # Get the uplink to get IPs
        self._update_dns_for_uplink(
            station_name, 
            sat_name, 
            uplink.ip_pool_entry.ip1, 
            uplink.ip_pool_entry.ip2, 
            add=False
    )

        
        # Remove static route
        station = self.ground_stations[station_name]
        frr_router = self.routers[sat_name]
        frr_router.config_frr("staticd", [f"no ip route {station.defaultIP()}/32 {format(ip.ip)}"])
        self.net.delLinkBetween(station_node, sat_node)

    def _update_default_route(self, station: GroundStation) -> None:
        closest_uplink = None
        # Find closest uplink
        for uplink in station.uplinks:
            if closest_uplink is None:
                closest_uplink = uplink
            elif closest_uplink.distance < uplink.distance:
                closest_uplink = uplink
        
        # If the closest has changed, update the default route
        if closest_uplink is not None and not closest_uplink.default:
            # Clear current default
            for uplink in station.uplinks:
                uplink.default = False
            # Mark new default and set
            closest_uplink.default = True 
            station_node = self.net.getNodeByName(station.name)
            route = "via %s" % format(closest_uplink.ip_pool_entry.ip2.ip)
            print(f"set default route for {station.name} to {route}")
            if station_node is not None:
                station_node.setDefaultRoute(route)

class Vessel(GroundStation):
    """
    Represents a moving vessel that can connect to satellites.
    Inherits from GroundStation for network connectivity.
    """
    def __init__(self, name: str, default_ip: str, uplinks: list[dict[str, typing.Any]]):
        super().__init__(name, default_ip, uplinks)
    
 
