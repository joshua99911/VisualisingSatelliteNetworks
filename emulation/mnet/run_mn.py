#!/usr/bin/python3
'''
Run a mininet instance of FRR routers in a torus topology with namespace-aware traffic capture.
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
from mininet.term import makeTerm

from emulation.mnet import driver
from emulation.mnet import frr_topo
from emulation.mnet.pmonitor import consolidate_databases

from emulation import torus_topo
from emulation import frr_config_topo

# Global variables
net = None
frrt = None
webpack_process = None
enable_monitoring = False
cleanup_in_progress = False


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

    # Satellites
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

    # Ground stations
    for name in torus_topo.ground_stations(graph):
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


def setup_packet_capture(net, graph, capture_dir):
    """
    Set up packet capture within each router's network namespace.
    """
    print(f"\nSetting up packet capture in: {capture_dir}")

    # Ensure we have permission to write to the directory
    os.system(f'chmod -R 777 {capture_dir}')

    # Kill any existing tcpdump processes
    os.system('pkill -f tcpdump')

    # Create the tcpdump command template
    # Using -B 4096 to increase buffer size and help prevent packet drops
    tcpdump_cmd = (
        'tcpdump -i any -s 0 -n -B 4096 -w /tmp/capture_{}.pcap '
        '"ip proto ospf or icmp or tcp or udp" '
        '2>/dev/null &'
    )

    # Start capture for each satellite
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
        print("\nPacket capture successfully started on network namespaces.")
    else:
        print("\nWarning: Packet captures may not have started properly.")


def stop_packet_capture():
    '''
    Stop all running tcpdump processes.
    '''
    os.system('pkill -f tcpdump')
    time.sleep(1)
    print("Stopped all tcpdump processes.")


def merge_captures(save_dir):
    """
    Merge all individual capture files into one, then remove the individual files.
    """
    output_file = os.path.join(save_dir, "network_capture.pcap")

    # Find all capture files in /tmp
    capture_files = list(Path('/tmp').glob('capture_*.pcap'))

    if capture_files:
        # Prefer mergecap if available, otherwise fall back to cat
        if os.system('which mergecap >/dev/null 2>&1') == 0:
            cmd = f'mergecap -w {output_file} /tmp/capture_*.pcap'
        else:
            cmd = f'cat /tmp/capture_*.pcap > {output_file}'

        os.system(cmd)
        print(f"\nMerged captures into {output_file}")

        # Clean up individual capture files
        for file in capture_files:
            os.unlink(file)


def cleanup_network():
    '''
    Cleanup network resources and processes, saving monitoring data and DNS.
    '''
    global net, frrt, cleanup_in_progress, enable_monitoring

    if cleanup_in_progress:
        return
    cleanup_in_progress = True

    try:
        # Create a timestamped directory now in case we need to store partial data on Ctrl+C
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        save_dir = os.path.join(os.getcwd(), 'captures', timestamp)
        os.makedirs(save_dir, exist_ok=True)

        # If FRR is running, stop it and consolidate any SQL data
        if frrt is not None:
            print("Stopping FRR routers...")
            frrt.stop_routers()
            time.sleep(1)

            if enable_monitoring:
                # Consolidate monitoring databases
                try:
                    working_dbs = []
                    for node in frrt.nodes.values():
                        if os.path.exists(node.working_db):
                            working_dbs.append(node.working_db)

                    consolidated_db = os.path.join(save_dir, 'monitoring.sqlite')
                    consolidate_databases(frrt.db_file, working_dbs, consolidated_db)
                    print(f"Monitoring data saved to: {consolidated_db}")
                except Exception as e:
                    print(f"Error consolidating databases: {e}")

        # If monitoring is enabled, stop packet capture and merge PCAPs
        if enable_monitoring:
            print("Stopping packet capture...")
            stop_packet_capture()
            try:
                merge_captures(save_dir)
            except Exception as e:
                print(f"Error merging packet captures: {e}")

        # Clean up Mininet network and restore DNS
        if net is not None:
            print("Cleaning up DNS configuration...")
            cleanup_dns(net)

            print("Stopping Mininet network...")
            net.stop()  # or net.cleanup(), but net.stop() + manual DNS cleanup is more explicit
            net = None

        # Final system-level cleanup
        print("Performing final cleanup...")
        os.system('pkill -f "watchfrr|zebra|ospfd|staticd" 2>/dev/null')
        os.system('ip -all netns delete 2>/dev/null')
        os.system('rm -rf /tmp/frr.* /tmp/zebra.* /tmp/ospfd.*')

    except Exception as e:
        print(f"Error during cleanup: {e}")
    finally:
        cleanup_in_progress = False


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


def run(num_rings, num_routers, use_cli, use_mnet, stable_monitors,
        ground_stations, enable_mon, ground_station_data):
    '''
    Execute the simulation of an FRR router network.
    '''
    global net, frrt, enable_monitoring
    enable_monitoring = enable_mon  # Ensure global sees the correct flag

    try:
        ensure_clean_state()

        # Build the network
        graph = torus_topo.create_network(num_rings, num_routers,
                                          ground_stations, ground_station_data)
        frr_config_topo.annotate_graph(graph)
        topo = frr_topo.NetxTopo(graph)

        # Start Mininet
        if use_mnet:
            # Backup host DNS
            if os.path.exists('/etc/hosts'):
                os.system('cp /etc/hosts /etc/hosts.mininet.bak')
            if os.path.exists('/etc/resolv.conf'):
                os.system('cp /etc/resolv.conf /etc/resolv.conf.mininet.bak')

            net = Mininet(topo=topo)
            net.start()
            configure_dns(net, graph)

            if enable_monitoring:
                # Let the network stabilize for a moment
                time.sleep(2)
                # Start packet captures
                # (capture directory is created in cleanup, so we just run captures here)
                setup_packet_capture(net, graph, "/tmp")  # or a subdir if you prefer

        # Start FRR
        frrt = frr_topo.FrrSimRuntime(topo, net, stable_monitors)
        print("Starting FRR routers...")
        frrt.start_routers()
        time.sleep(2)

        # Optionally open terminals
        if net is not None:
            nodes_to_open = ['G_LON', 'R0_0']
            for node_name in nodes_to_open:
                node = net.get(node_name)
                if node:
                    makeTerm(node, title=f'Terminal for {node.name}')
                    print(f"Opened terminal for {node.name}")

        # Either drop to CLI or run headless with driver
        if use_cli and net is not None:
            CLI(net)
        else:
            # Handle Ctrl+C
            signal.signal(signal.SIGINT, signal_handler)
            driver.run(frrt)

    except Exception as e:
        print(f"Error during execution: {e}")
        cleanup_network()
        sys.exit(1)

    finally:
        # If we haven't cleaned up (i.e., not a Ctrl+C but a normal exit), do it here
        if not cleanup_in_progress:
            cleanup_network()
        cleanup_webpack()


def usage():
    print("Usage: python3 -m emulation.mnet.run_mn [--cli] [--no-mnet] [--monitor] <config_file>")


if __name__ == "__main__":
    # Process flags
    use_cli = "--cli" in sys.argv
    use_mnet = "--no-mnet" not in sys.argv
    enable_monitoring = "--monitor" in sys.argv

    # Strip flags from argv to locate config file
    args = [arg for arg in sys.argv[1:] if not arg.startswith('--')]

    if len(args) != 1:
        usage()
        sys.exit(-1)

    config_file = args[0]

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser['network'] = {}
    parser['monitor'] = {}

    try:
        parser.read(config_file)
    except Exception as e:
        print(f"Error reading config file: {e}")
        usage()
        sys.exit(-1)

    ground_station_data = {}
    if 'ground_stations' in parser:
        for name, coords in parser['ground_stations'].items():
            lat, lon = map(float, coords.split(','))
            ground_station_data[name] = (lat, lon)

    num_rings = parser['network'].getint('rings', 4)
    num_routers = parser['network'].getint('routers', 4)
    ground_stations = parser['network'].getboolean('ground_stations', False)
    stable_monitors = parser['monitor'].getboolean('stable_monitors', False)

    if num_rings < 1 or num_rings > 30 or num_routers < 1 or num_routers > 30:
        print("Rings or nodes count out of range")
        sys.exit(-1)

    setLogLevel("info")

    # Start webpack in watch mode so that frontend changes are automatically built
    webpack_dir = os.path.join("emulation", "mnet", "static", "js")
    try:
        webpack_process = subprocess.Popen(["npm", "run", "watch"], cwd=webpack_dir)
        print("Started webpack watch process in", webpack_dir)
    except Exception as e:
        print("Error starting webpack watch process:", e)
        webpack_process = None

    # Print some startup info
    print(f"\nStarting simulation with:")
    print(f"- Number of rings: {num_rings}")
    print(f"- Routers per ring: {num_routers}")
    print(f"- Ground stations enabled: {ground_stations}")
    print(f"- Monitoring enabled: {enable_monitoring}")
    print(f"- Stable monitors: {stable_monitors}")
    print(f"- CLI mode: {use_cli}")
    print(f"- Mininet enabled: {use_mnet}")

    if enable_monitoring:
        print("\nMonitoring is enabled. Final captures/databases will be saved in:")
        print("captures/<TIMESTAMP>/")

    # Run the simulation
    run(num_rings, num_routers, use_cli, use_mnet, stable_monitors,
        ground_stations, enable_monitoring, ground_station_data)
