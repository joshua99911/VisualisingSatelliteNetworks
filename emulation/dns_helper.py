#!/usr/bin/python3
'''
Helper script for DNS operations in the unified satellite simulation.
'''

import sys
import requests
import argparse
import subprocess
import os

API_URL = "http://127.0.0.1:8000"

def refresh_dns():
    """Refresh DNS in all nodes"""
    try:
        response = requests.post(f"{API_URL}/refresh_dns")
        if response.status_code == 200:
            print("DNS refreshed successfully")
        else:
            print(f"Error refreshing DNS: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error: {str(e)}")

def open_terminal(node_name):
    """Open a terminal for a node"""
    try:
        response = requests.post(f"{API_URL}/open_terminal", json={"node_name": node_name})
        if response.status_code == 200:
            print(f"Terminal opened for {node_name}")
        else:
            print(f"Error opening terminal: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error: {str(e)}")

def ping_nodes(source, target, count=3):
    """Run a ping from source to target node"""
    try:
        # Try the namespace lookup approach first
        namespace = find_namespace(source)
        if namespace:
            if namespace.isdigit():
                # PID-based namespace
                cmd = ["nsenter", "-t", namespace, "-n", "ping", "-c", str(count), target]
            else:
                # Named namespace
                cmd = ["sudo", "ip", "netns", "exec", namespace, "ping", "-c", str(count), target]
                
            print(f"Running: {' '.join(cmd)}")
            subprocess.run(cmd)
        else:
            # Try using Mininet PID-based approach
            pid = find_mininet_pid(source)
            if pid:
                cmd = ["sudo", "nsenter", "-t", pid, "-n", "ping", "-c", str(count), target]
                print(f"Running: {' '.join(cmd)}")
                subprocess.run(cmd)
            else:
                print(f"Cannot find namespace or PID for {source}")
                
                # Show all available namespaces and PIDs for debugging
                print("\nAvailable network namespaces:")
                subprocess.run(["sudo", "ip", "netns", "list"])
                
                print("\nMininet processes:")
                subprocess.run(["ps", "aux", "|", "grep", "mininet"])
    except Exception as e:
        print(f"Error: {str(e)}")

def find_namespace(node_name):
    """Find the correct network namespace for a node"""
    # Check for pid-based namespace first
    try:
        # Find the PID of the node's bash process
        pid = subprocess.check_output(["pgrep", "-f", f"bash.*mininet:{node_name}"]).decode().strip()
        if pid:
            # Check if this PID has a namespace
            if os.path.exists(f"/proc/{pid}/ns/net"):
                return pid
    except:
        pass
        
    # Check for mn. prefixed namespace
    if os.path.exists(f"/var/run/netns/mn.{node_name}"):
        return f"mn.{node_name}"
        
    # Check for exact name match
    if os.path.exists(f"/var/run/netns/{node_name}"):
        return node_name
        
    # Try to find by listing all namespaces
    try:
        namespaces = subprocess.check_output(["ls", "-1", "/var/run/netns/"]).decode().strip().split('\n')
        # Look for partial matches (in case of truncated names)
        for ns in namespaces:
            if node_name in ns:
                return ns
    except:
        pass
        
    # No matching namespace found
    return None

def find_mininet_pid(node_name):
    """Find PID for a Mininet node process"""
    try:
        # Try different patterns to find the PID
        patterns = [
            f"mininet:{node_name}",
            f"bash.*{node_name}",
            f"{node_name}"
        ]
        
        for pattern in patterns:
            cmd = f"ps aux | grep '{pattern}' | grep -v grep | awk '{{print $2}}'"
            pid = os.popen(cmd).read().strip()
            if pid:
                return pid
    except Exception as e:
        print(f"Error finding PID: {e}")
    
    return None

def list_nodes():
    """List all available nodes in the simulation"""
    try:
        response = requests.get(f"{API_URL}/positions")
        if response.status_code == 200:
            data = response.json()
            print("Available nodes:")
            print("Satellites:")
            for sat in data.get('satellites', []):
                print(f"  {sat['name']}")
            print("Ground stations:")
            for gs in data.get('ground_stations', []):
                print(f"  {gs['name']}")
            print("Vessels:")
            for vessel in data.get('vessels', []):
                print(f"  {vessel['name']}")
        else:
            print(f"Error listing nodes: {response.status_code}")
    except Exception as e:
        print(f"Error: {str(e)}")


def main():
    parser = argparse.ArgumentParser(description="DNS helper for satellite simulation")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    
    # Open terminal command
    terminal_parser = subparsers.add_parser("terminal", help="Open a terminal for a node")
    terminal_parser.add_argument("node", help="Node name")
    
    # Ping command
    ping_parser = subparsers.add_parser("ping", help="Ping from one node to another")
    ping_parser.add_argument("source", help="Source node name")
    ping_parser.add_argument("target", help="Target node name")
    ping_parser.add_argument("-c", "--count", type=int, default=3, help="Number of pings")
    
    # List nodes command
    list_parser = subparsers.add_parser("list", help="List all available nodes")
    
    
    args = parser.parse_args()
    

    if args.command == "terminal":
        open_terminal(args.node)
    elif args.command == "ping":
        ping_nodes(args.source, args.target, args.count)
    elif args.command == "list":
        list_nodes()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()