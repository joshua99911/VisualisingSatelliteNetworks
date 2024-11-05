#!/usr/bin/python3

"""
Run a mininet instance of FRR routers in a torus topology with namespace-aware traffic capture.
"""
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
import mnet.driver

import torus_topo
import frr_config_topo
import mnet.frr_topo


def signal_handler(sig, frame):
    """
    Make a ^C start a clean shutdown. Needed to stop all of the FRR processes.
    """
    print("Ctrl-C received, shutting down....")
    # Ensure tcpdump is stopped
    os.system('pkill -f tcpdump')
    mnet.driver.invoke_shutdown()


def setup_packet_capture(net, graph):
    """
    Set up packet capture within each router's network namespace.
    """
    capture_dir = Path.cwd() / "mininet_captures"
    capture_dir.mkdir(exist_ok=True, parents=True)
    print(f"\nSetting up packet capture in: {capture_dir}")
    
    # Ensure we have permission to write to the directory
    os.system(f'chmod -R 777 {capture_dir}')
    
    # Kill any existing tcpdump processes
    os.system('pkill -f tcpdump')

    # Create the tcpdump command template
    # Using -B 4096 to increase buffer size and prevent packet drops
    tcpdump_cmd = (
        'tcpdump -i any -s 0 -n -B 4096 -w /tmp/capture_{}.pcap '
        '"ip proto ospf or icmp or tcp or udp" '
        '2>/dev/null &'
    )
    
    # Start capture for each router
    for node_name in torus_topo.satellites(graph):
        if node_name in net:
            node = net.get(node_name)
            node.cmd(tcpdump_cmd.format(node_name))
            print(f"Started capture for {node_name}")

    # Start capture for ground stations
    for node_name in torus_topo.ground_stations(graph):
        if node_name in net:
            node = net.get(node_name)
            node.cmd(tcpdump_cmd.format(node_name))
            print(f"Started capture for {node_name}")

    # Give tcpdump a moment to start
    time.sleep(2)

    # Verify captures are running
    running = False
    for node in net.hosts:
        result = node.cmd('ps aux | grep tcpdump')
        if 'tcpdump -i any' in result:
            running = True
            print(f"Confirmed capture running on {node.name}")
    
    if running:
        print("\nPacket capture successfully started on network namespaces")
    else:
        print("\nWarning: Packet captures may not have started properly")


def merge_captures():
    """
    Merge all individual capture files into one.
    """
    capture_dir = Path.cwd() / "mininet_captures"
    temp_dir = Path('/tmp')
    output_file = capture_dir / "torus_network.pcap"
    
    # Find all temporary capture files
    capture_files = list(temp_dir.glob('capture_*.pcap'))
    
    if capture_files:
        # Use mergecap if available, otherwise use cat
        if os.system('which mergecap >/dev/null 2>&1') == 0:
            cmd = f'mergecap -w {output_file} /tmp/capture_*.pcap'
        else:
            cmd = f'cat /tmp/capture_*.pcap > {output_file}'
        
        os.system(cmd)
        print(f"\nMerged captures into {output_file}")
        
        # Cleanup temporary files
        for file in capture_files:
            os.unlink(file)


def run(num_rings, num_routers, use_cli, use_mnet, stable_monitors: bool, ground_stations: bool, enable_monitoring: bool = False):
    # Create a networkx graph annotated with FRR configs
    graph = torus_topo.create_network(num_rings, num_routers, ground_stations)
    frr_config_topo.annotate_graph(graph)
    frr_config_topo.dump_graph(graph)

    # Use the networkx graph to build a mininet topology
    topo = mnet.frr_topo.NetxTopo(graph)
    print("generated topo")

    net = None
    if use_mnet:
        net = Mininet(topo=topo)
        net.start()
        
        # Set up packet capture if monitoring is enabled
        if enable_monitoring:
            # Wait a moment for interfaces to be ready
            time.sleep(2)
            setup_packet_capture(net, graph)

    frrt = mnet.frr_topo.FrrSimRuntime(topo, net, stable_monitors)
    print("created runtime")

    frrt.start_routers()

    print(f"\n****Running {num_rings} rings with {num_routers} per ring, stable monitors {stable_monitors}, "
          f"ground_stations {ground_stations}, monitoring {'enabled' if enable_monitoring else 'disabled'}")
    

    if use_cli and net is not None:
        CLI(net)
    else:
        print("Launching web API. Use /shutdown to halt")
        signal.signal(signal.SIGINT, signal_handler)
        mnet.driver.run(frrt)
    
    # Cleanup before stopping
    if net is not None and enable_monitoring:
        print("Stopping packet capture...")
        os.system('pkill -f tcpdump')
        merge_captures()
    
    frrt.stop_routers()

    if net is not None:
        net.stop()


def usage():
    print("Usage: python3 -m mnet.run_mn [--cli] [--no-mnet] [--monitor] <config_file>")
    print("Options:")
    print("  --cli        Enable Mininet CLI")
    print("  --no-mnet    Disable Mininet")
    print("  --monitor    Enable traffic monitoring")
    print("<config_file>  Configuration file with network settings")


if __name__ == "__main__":
    use_cli = False
    use_mnet = True
    enable_monitoring = False

    if "--cli" in sys.argv:
        use_cli = True
        sys.argv.remove("--cli")

    if "--no-mnet" in sys.argv:
        use_mnet = False
        sys.argv.remove("--no-mnet")

    if "--monitor" in sys.argv:
        enable_monitoring = True
        sys.argv.remove("--monitor")

    if len(sys.argv) > 2:
        usage()
        sys.exit(-1)

    parser = configparser.ConfigParser()
    parser['network'] = {}
    parser['monitor'] = {}
    try:
        if len(sys.argv) == 2:
            parser.read(sys.argv[1])
    except Exception as e:
        print(str(e))
        usage()
        sys.exit(-1)

    num_rings = parser['network'].getint('rings', 4)
    num_routers = parser['network'].getint('routers', 4)
    ground_stations = parser['network'].getboolean('ground_stations', False)
    stable_monitors = parser['monitor'].getboolean('stable_monitors', False)

    if num_rings < 1 or num_rings > 30 or num_routers < 1 or num_routers > 30:
        print("Rings or nodes count out of range")
        sys.exit(-1)

    setLogLevel("info")
    run(num_rings, num_routers, use_cli, use_mnet, stable_monitors, ground_stations, enable_monitoring)