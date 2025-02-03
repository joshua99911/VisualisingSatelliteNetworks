#!/usr/bin/python3
'''
Run a Mininet instance of FRR routers in a torus topology with namespace‐aware traffic capture.
Includes improved cleanup and process management.
'''

import configparser
import signal
import sys
import os
from pathlib import Path
import subprocess
import time

from mininet.net import Mininet
from mininet.log import setLogLevel, info
from mininet.cli import CLI
from emulation.mnet import driver
from mininet.term import makeTerm

from emulation import torus_topo
from emulation import frr_config_topo
from emulation.mnet import frr_topo

# Global variables for the simulation and webpack process
net = None
frrt = None
webpack_process = None  # Will hold the process running "npm run watch"

def ensure_clean_state():
    """
    Ensure clean state before starting a new instance.
    """
    print("Ensuring clean state before starting...")
    os.system('pkill -f "watchfrr|zebra|ospfd|staticd"')
    os.system('ip -all netns delete')
    os.system('ip link show | grep veth | cut -d"@" -f1 | while read veth; do ip link delete $veth 2>/dev/null; done')
    os.system('rm -rf /tmp/frr.* /tmp/zebra.* /tmp/ospfd.*')
    time.sleep(2)

def configure_dns(net, graph):
    '''
    Configure DNS for all nodes in the network by updating /etc/hosts
    in each node's namespace.
    '''
    hosts_entries = set()  # Use a set to avoid duplicates

    for name in torus_topo.satellites(graph):
        node = graph.nodes[name]
        if "ip" in node:
            hosts_entries.add(f"{format(node['ip'].ip)}\t{name}")

        for neighbor in graph.adj[name]:
            edge = graph.adj[name][neighbor]
            local_ip = edge["ip"][name]
            remote_ip = edge["ip"][neighbor]
            local_intf = edge["intf"][name]
            remote_intf = edge["intf"][neighbor]

            hosts_entries.add(f"{format(local_ip.ip)}\t{local_intf} {name}-TO-{neighbor}")
            hosts_entries.add(f"{format(remote_ip.ip)}\t{remote_intf} {neighbor}-TO-{name}")

    for node_type in [torus_topo.ground_stations, torus_topo.vessels]:
        for name in node_type(graph):
            node = graph.nodes[name]
            if "ip" in node:
                hosts_entries.add(f"{format(node['ip'].ip)}\t{name}")

    hosts_content = "\n".join([
        "127.0.0.1\tlocalhost",
        "::1\tlocalhost ip6-localhost ip6-loopback",
        "fe00::0\tip6-localnet",
        "ff00::0\tip6-mcastprefix",
        "ff02::1\tip6-allnodes",
        "ff02::2\tip6-allrouters",
        "\n# Network hosts",
        *sorted(hosts_entries)
    ])

    for node in net.hosts:
        temp_file = f'/tmp/hosts_{node.name}.temp'
        with open(temp_file, 'w') as f:
            f.write(hosts_content)

        node.cmd(f'mkdir -p /etc/netns/{node.name}')
        node.cmd(f'cp {temp_file} /etc/netns/{node.name}/hosts')
        node.cmd(f'cp {temp_file} /etc/hosts')
        node.cmd(f'rm {temp_file}')

        resolv_content = "nameserver 127.0.0.1\nsearch mininet"
        node.cmd(f'echo "{resolv_content}" > /etc/netns/{node.name}/resolv.conf')
        node.cmd(f'echo "{resolv_content}" > /etc/resolv.conf')

def cleanup_dns(net):
    '''
    Clean up DNS configuration when the network is stopped.
    '''
    try:
        for node in net.hosts:
            node.cmd(f'rm -rf /etc/netns/{node.name}')
            print(f"Removed /etc/netns/{node.name}.")

        if os.path.exists('/etc/hosts.mininet.bak'):
            os.system('cp /etc/hosts.mininet.bak /etc/hosts')
            os.system('rm /etc/hosts.mininet.bak')
            print("Restored /etc/hosts.")

        if os.path.exists('/etc/resolv.conf.mininet.bak'):
            os.system('cp /etc/resolv.conf.mininet.bak /etc/resolv.conf')
            os.system('rm /etc/resolv.conf.mininet.bak')
            print("Restored /etc/resolv.conf.")
    except Exception as e:
        print(f"Error during DNS cleanup: {e}")

def stop_packet_capture():
    '''
    Stop all running tcpdump processes.
    '''
    os.system('pkill -f tcpdump')
    time.sleep(1)
    print("Stopped all tcpdump processes.")

def cleanup_network():
    '''
    Cleanup network resources and processes.
    '''
    global net, frrt
    try:
        if frrt is not None:
            print("Stopping FRR routers...")
            frrt.stop_routers()
            time.sleep(1)
        
        stop_packet_capture()
        
        if net is not None:
            print("Cleaning up DNS configuration...")
            cleanup_dns(net)
            
            print("Cleaning up network namespaces...")
            for node in net.hosts:
                node.cmd('ip netns del %s 2>/dev/null' % node.name)
            
            print("Stopping Mininet...")
            net.stop()
            
        print("Performing final cleanup...")
        os.system('pkill -f "watchfrr|zebra|ospfd|staticd"')
        os.system('ip -all netns delete')
        os.system('rm -rf /tmp/frr.* /tmp/zebra.* /tmp/ospfd.*')
        
    except Exception as e:
        print(f"Error during cleanup: {e}")

def cleanup_webpack():
    """
    Terminate the webpack watch process if it is running.
    """
    global webpack_process
    if webpack_process is not None:
        try:
            print("Terminating webpack watch process...")
            webpack_process.terminate()
            webpack_process.wait(timeout=10)
        except Exception as e:
            print(f"Error terminating webpack process: {e}")
        webpack_process = None

def signal_handler(sig, frame):
    '''
    Handle Ctrl+C for clean shutdown.
    '''
    print("\nCtrl-C received, shutting down...")
    cleanup_network()
    cleanup_webpack()
    sys.exit(0)

def run(num_rings, num_routers, use_cli, use_mnet, stable_monitors, ground_stations, enable_monitoring, ground_station_data, vessel_data):
    '''
    Execute the simulation of an FRR router network.
    '''
    global net, frrt
    try:
        ensure_clean_state()
        graph = torus_topo.create_network(num_rings, num_routers, ground_stations, ground_station_data, vessel_data)
        frr_config_topo.annotate_graph(graph)
        topo = frr_topo.NetxTopo(graph)

        if use_mnet:
            if os.path.exists('/etc/hosts'):
                os.system('cp /etc/hosts /etc/hosts.mininet.bak')
            if os.path.exists('/etc/resolv.conf'):
                os.system('cp /etc/resolv.conf /etc/resolv.conf.mininet.bak')

            net = Mininet(topo=topo)
            net.start()
            configure_dns(net, graph)

            if enable_monitoring:
                time.sleep(2)

        frrt = frr_topo.FrrSimRuntime(topo, net, stable_monitors)
        print("Starting FRR routers...")
        frrt.start_routers()
        time.sleep(2)

        if net is not None:
            nodes_to_open = ['G_LON', 'R0_0']
            for node_name in nodes_to_open:
                node = net.get(node_name)
                if node:
                    makeTerm(node, title=f'Terminal for {node.name}')
                    print(f"Opened terminal for {node.name}")

        if use_cli and net is not None:
            CLI(net)
        else:
            signal.signal(signal.SIGINT, signal_handler)
            driver.run(frrt)

    except Exception as e:
        print(f"Error during execution: {e}")
        cleanup_network()
        sys.exit(1)
    finally:
        cleanup_network()
        cleanup_webpack()

def usage():
    print("Usage: python3 -m emulation.mnet.run_mn [--cli] [--no-mnet] [--monitor] <config_file>")

if __name__ == "__main__":
    use_cli = "--cli" in sys.argv
    use_mnet = "--no-mnet" not in sys.argv
    enable_monitoring = "--monitor" in sys.argv

    if len(sys.argv) > 2:
        usage()
        sys.exit(-1)

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser['network'] = {}
    parser['monitor'] = {}

    if len(sys.argv) == 2:
        parser.read(sys.argv[1])

    ground_station_data = {}
    if 'ground_stations' in parser:
        for name, coords in parser['ground_stations'].items():
            lat, lon = map(float, coords.split(','))
            ground_station_data[name] = (lat, lon)

    vessel_data = {}
    if 'vessels' in parser:
        for name, waypoint_str in parser['vessels'].items():
            waypoints = []
            for waypoint in waypoint_str.split(';'):
                lat, lon = map(float, waypoint.split(','))
                waypoints.append((lat, lon))
            vessel_data[name] = waypoints

    num_rings = parser['network'].getint('rings', 4)
    num_routers = parser['network'].getint('routers', 4)
    ground_stations = parser['network'].getboolean('ground_stations', False)
    stable_monitors = parser['monitor'].getboolean('stable_monitors', False)

    if num_rings < 1 or num_rings > 30 or num_routers < 1 or num_routers > 30:
        print("Rings or nodes count out of range")
        sys.exit(-1)

    setLogLevel("info")

    # Start webpack in watch mode so that frontend changes are automatically built.
    webpack_dir = os.path.join("emulation", "mnet", "static", "js")
    try:
        webpack_process = subprocess.Popen(["npm", "run", "watch"], cwd=webpack_dir)
        print("Started webpack watch process in", webpack_dir)
    except Exception as e:
        print("Error starting webpack watch process:", e)
        webpack_process = None

    run(num_rings, num_routers, use_cli, use_mnet, stable_monitors, ground_stations, enable_monitoring, ground_station_data, vessel_data)
