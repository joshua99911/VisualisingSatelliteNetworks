#!/usr/bin/python3

'''
Run a mininet instance of FRR routers in a torus topology with namespace-aware traffic capture.

Command-Line Options:
    - `--cli`:
        Enable CLI (Command Line Interface) mode.
    - `--no-mnet`:
        Disable Mininet simulation.
    - `--monitor`:
        Enable monitoring functionality.

Configuration File:
    An optional INI-style file defining the following sections:

    [network]
        - `rings` (int): Number of network rings (1-30). Default is 4.
        - `routers` (int): Number of routers per ring (1-30). Default is 4.
        - `ground_stations` (bool): Enable or disable ground stations. Default is False.

    [monitor]
        - `stable_monitors` (bool): Enable or disable stable monitors. Default is False.

'''

import configparser
import signal
import sys
import os
from pathlib import Path
import subprocess
import time
import threading

from mininet.net import Mininet
from mininet.log import setLogLevel, info
from mininet.cli import CLI
from emulation.mnet import driver
from mininet.term import makeTerm

from emulation import torus_topo
from emulation import frr_config_topo
from emulation.mnet import frr_topo


def configure_dns(net, graph):
    '''
    Configure DNS for all nodes in the network by updating /etc/hosts
    in each node's namespace. Include interface IPs with descriptive names.
    '''
    # First, collect all IP addresses and hostnames
    hosts_entries = []
    
    # Add satellite nodes loopback addresses
    for name in torus_topo.satellites(graph):
        node = graph.nodes[name]
        if "ip" in node:
            hosts_entries.append(f"{format(node['ip'].ip)}\t{name}")
            
        # Add interface IPs with descriptive names
        for neighbor in graph.adj[name]:
            edge = graph.adj[name][neighbor]
            local_ip = edge["ip"][name]
            remote_ip = edge["ip"][neighbor]
            local_intf = edge["intf"][name]
            remote_intf = edge["intf"][neighbor]
            
            # Add entries for both local and remote interfaces
            # Format: IP    devicename-intf devicename-TO-neighborname
            hosts_entries.append(f"{format(local_ip.ip)}\t{local_intf} {name}-TO-{neighbor}")
            hosts_entries.append(f"{format(remote_ip.ip)}\t{remote_intf} {neighbor}-TO-{name}")
    
    # Add ground stations
    for name in torus_topo.ground_stations(graph):
        node = graph.nodes[name]
        if "ip" in node:
            hosts_entries.append(f"{format(node['ip'].ip)}\t{name}")
            
    # Create hosts file content
    hosts_content = "\n".join([
        "127.0.0.1\tlocalhost",
        "::1\tlocalhost ip6-localhost ip6-loopback",
        "fe00::0\tip6-localnet",
        "ff00::0\tip6-mcastprefix",
        "ff02::1\tip6-allnodes",
        "ff02::2\tip6-allrouters",
        "\n# Network hosts",
        *hosts_entries
    ])
    
    # Update /etc/hosts in each node's namespace
    for node in net.hosts:
        # Create a temporary hosts file
        with open('/tmp/hosts.temp', 'w') as f:
            f.write(hosts_content)
        
        # Copy the file to the node's namespace
        node.cmd(f'mkdir -p /etc/netns/{node.name}')
        node.cmd(f'cp /tmp/hosts.temp /etc/netns/{node.name}/hosts')
        
        # Also update the current namespace's hosts file
        node.cmd('cp /tmp/hosts.temp /etc/hosts')
        
        # Clean up
        node.cmd('rm /tmp/hosts.temp')
        
        # Configure resolv.conf to use the hosts file
        resolv_content = "nameserver 127.0.0.1\nsearch mininet"
        node.cmd(f'echo "{resolv_content}" > /etc/netns/{node.name}/resolv.conf')
        node.cmd(f'echo "{resolv_content}" > /etc/resolv.conf')

def cleanup_dns(net):
    """
    Clean up DNS configuration when the network is stopped.
    """
    for node in net.hosts:
        # Remove the network namespace config directory
        node.cmd(f'rm -rf /etc/netns/{node.name}')
        # Restore original /etc/hosts
        node.cmd('cp /etc/hosts.original /etc/hosts')

def signal_handler(sig, frame):
    """
    Make a ^C start a clean shutdown. Needed to stop all of the FRR processes.
    """
    print("Ctrl-C received, shutting down....")
    # Ensure tcpdump is stopped
    os.system('pkill -f tcpdump')
    # Restore original DNS files if they exist
    if os.path.exists('/etc/hosts.mininet.bak'):
        os.system('cp /etc/hosts.mininet.bak /etc/hosts')
        os.system('rm /etc/hosts.mininet.bak')
    if os.path.exists('/etc/resolv.conf.mininet.bak'):
        os.system('cp /etc/resolv.conf.mininet.bak /etc/resolv.conf')
        os.system('rm /etc/resolv.conf.mininet.bak')
    driver.invoke_shutdown()


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
    '''
    Merge all individual capture files into one.
    '''
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


def run_web_api(frrt):
    '''
    Launch the web API in a separate thread.
    '''
    print("Launching web API in a separate thread. Use /shutdown to halt.")
    driver.run(frrt)


def run(num_rings, num_routers, use_cli, use_mnet, stable_monitors: bool, ground_stations: bool, enable_monitoring: bool = False):
    '''
    Execute the simulation of an FRR router network in a torus topology using Mininet.

    Args:
        num_rings (int): Number of network rings to create (1-30).
        num_routers (int): Number of routers per ring (1-30).
        use_cli (bool): If True, enable the Mininet Command Line Interface (CLI).
        use_mnet (bool): If True, enable the Mininet simulation.
        stable_monitors (bool): Whether to enable stable monitoring configurations.
        ground_stations (bool): If True, include ground stations in the topology.
        enable_monitoring (bool, optional): Whether to enable traffic monitoring. Defaults to False.

    This function creates a torus topology based on the specified parameters, configures DNS and
    monitoring (if enabled), and starts the simulation. If the CLI is enabled, it allows interactive
    control of the network. After the simulation, it cleans up the DNS configuration and stops the
    network.
    '''

    # Create a networkx graph annotated with FRR configs
    graph = torus_topo.create_network(num_rings, num_routers, ground_stations)
    frr_config_topo.annotate_graph(graph)
    frr_config_topo.dump_graph(graph)

    # Use the networkx graph to build a mininet topology
    topo = frr_topo.NetxTopo(graph)
    print("generated topo")

    net = None
    if use_mnet:
        # Backup original DNS files
        if os.path.exists('/etc/hosts'):
            os.system('cp /etc/hosts /etc/hosts.mininet.bak')
        if os.path.exists('/etc/resolv.conf'):
            os.system('cp /etc/resolv.conf /etc/resolv.conf.mininet.bak')
        
        net = Mininet(topo=topo)
        net.start()
        
        # Configure DNS after network starts but before monitoring
        configure_dns(net, graph)
        print("configured DNS")
        
        # Set up packet capture if monitoring is enabled
        if enable_monitoring:
            # Wait a moment for interfaces to be ready
            time.sleep(2)
            setup_packet_capture(net, graph)

    frrt = frr_topo.FrrSimRuntime(topo, net, stable_monitors)
    print("created runtime")

    frrt.start_routers()

    print(f"\n****Running {num_rings} rings with {num_routers} per ring, stable monitors {stable_monitors}, "
          f"ground_stations {ground_stations}, monitoring {'enabled' if enable_monitoring else 'disabled'}")
    
    # Open xterm for specific nodes
    # node_list = [net.get('G_PAO'), net.get('G_SYD'), net.get('R0_0')]  # Replace with your node names
    # for node in node_list:
    #     makeTerm(node, title=f'Terminal for {node.name}')
    #     print("Made Terminal for ", {node.name})

    if use_cli and net is not None:
        # Launch the web interface in a separate thread
        web_thread = threading.Thread(target=run_web_api, args=(frrt,))
        web_thread.daemon = True
        web_thread.start()

        # Enter the CLI
        CLI(net)

        # Ensure the web thread continues running after exiting the CLI
        web_thread.join()
    else:
        run_web_api(frrt)

    # Cleanup before stopping
    if net is not None and enable_monitoring:
        print("Stopping packet capture...")
        os.system('pkill -f tcpdump')
        merge_captures()
    
    if net is not None:
        cleanup_dns(net)
        net.stop()

    # Restore original DNS files
    if os.path.exists('/etc/hosts.mininet.bak'):
        os.system('cp /etc/hosts.mininet.bak /etc/hosts')
        os.system('rm /etc/hosts.mininet.bak')
    if os.path.exists('/etc/resolv.conf.mininet.bak'):
        os.system('cp /etc/resolv.conf.mininet.bak /etc/resolv.conf')
        os.system('rm /etc/resolv.conf.mininet.bak')

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
