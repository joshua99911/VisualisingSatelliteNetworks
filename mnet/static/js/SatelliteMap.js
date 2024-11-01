const SatelliteMap = () => {
  const [satellites, setSatellites] = React.useState([]);
  const [groundStations, setGroundStations] = React.useState([]);
  const [satelliteLinks, setSatelliteLinks] = React.useState([]);
  const [groundUplinks, setGroundUplinks] = React.useState([]);
  const [showSatelliteLinks, setShowSatelliteLinks] = React.useState(false);
  const [showGroundLinks, setShowGroundLinks] = React.useState(false);
  const [hoveredNode, setHoveredNode] = React.useState(null);
  
  React.useEffect(() => {
    const fetchPositions = async () => {
      try {
        const response = await fetch('/positions');
        const data = await response.json();
        setSatellites(data.satellites || []);
        setGroundStations(data.ground_stations || []);
        setSatelliteLinks(data.satellite_links || []);
        setGroundUplinks(data.ground_uplinks || []);
      } catch (error) {
        console.error('Failed to fetch positions:', error);
      }
    };

    // Fetch initially and then every 10 seconds
    fetchPositions();
    const interval = setInterval(fetchPositions, 10000);
    return () => clearInterval(interval);
  }, []);

  const mapWidth = 800;
  const mapHeight = 400;
  
  // Function to get coordinates for a node
  const getNodeCoordinates = (node) => {
    const x = ((node.lon + 180) / 360) * mapWidth;
    const y = ((90 - node.lat) / 180) * mapHeight;
    return [x, y];
  };
  
  // Get connected nodes for a given node name
  const getConnectedNodes = (nodeName) => {
    const connections = new Set();
    
    // Check satellite links
    satelliteLinks.forEach(link => {
      if (link.node1_name === nodeName && link.up) {
        connections.add(link.node2_name);
      } else if (link.node2_name === nodeName && link.up) {
        connections.add(link.node1_name);
      }
    });
    
    // Check ground links
    groundUplinks.forEach(station => {
      if (station.ground_node === nodeName) {
        station.uplinks.forEach(uplink => {
          connections.add(uplink.sat_node);
        });
      } else {
        station.uplinks.forEach(uplink => {
          if (uplink.sat_node === nodeName) {
            connections.add(station.ground_node);
          }
        });
      }
    });
    
    return connections;
  };
  
  // Create links between nodes
  const renderLinks = () => {
    const links = [];
    
    // Helper to find node by name
    const findNode = (name) => {
      const sat = satellites.find(s => s.name === name);
      if (sat) return sat;
      return groundStations.find(g => g.name === name);
    };

    const drawLink = (node1Name, node2Name, color, opacity, key) => {
      const node1 = findNode(node1Name);
      const node2 = findNode(node2Name);
      if (node1 && node2) {
        const [x1, y1] = getNodeCoordinates(node1);
        const [x2, y2] = getNodeCoordinates(node2);
        links.push(
          React.createElement('line', {
            key: key,
            x1, y1, x2, y2,
            stroke: color,
            strokeWidth: hoveredNode ? "2" : "1",
            strokeOpacity: opacity
          })
        );
      }
    };
    
    if (hoveredNode) {
      // When a node is hovered, only show its connections
      const connections = getConnectedNodes(hoveredNode);
      connections.forEach((connectedNode, index) => {
        const isSatelliteLink = hoveredNode.startsWith('R') && connectedNode.startsWith('R');
        drawLink(
          hoveredNode,
          connectedNode,
          isSatelliteLink ? "#1d4ed8" : "#ef4444",
          1,
          `hover-link-${index}`
        );
      });
    } else {
      // Normal link display based on toggles
      if (showSatelliteLinks) {
        satelliteLinks.forEach((link, index) => {
          if (link.up) {
            drawLink(
              link.node1_name,
              link.node2_name,
              "#1d4ed8",
              0.5,
              `sat-link-${index}`
            );
          }
        });
      }
      
      if (showGroundLinks) {
        groundUplinks.forEach((station, stationIndex) => {
          station.uplinks.forEach((uplink, uplinkIndex) => {
            drawLink(
              station.ground_node,
              uplink.sat_node,
              "#ef4444",
              0.5,
              `ground-link-${stationIndex}-${uplinkIndex}`
            );
          });
        });
      }
    }
    
    return links;
  };

  // Handle node click to open in new tab
  const handleNodeClick = (nodeName) => {
    const path = nodeName.startsWith('R') ? 'router' : 'station';
    window.open(`/view/${path}/${nodeName}`, '_blank');
  };
  
  return React.createElement(
    'div', 
    { className: "w-full max-w-4xl mt-4" },
    [
      // Controls
      React.createElement(
        'div',
        { className: "mb-4 flex gap-4" },
        [
          React.createElement(
            'label',
            { className: "flex items-center gap-2" },
            [
              React.createElement('input', {
                type: "checkbox",
                checked: showSatelliteLinks,
                onChange: (e) => setShowSatelliteLinks(e.target.checked),
                className: "form-checkbox h-4 w-4 text-blue-600"
              }),
              "Show Satellite Links"
            ]
          ),
          React.createElement(
            'label',
            { className: "flex items-center gap-2" },
            [
              React.createElement('input', {
                type: "checkbox",
                checked: showGroundLinks,
                onChange: (e) => setShowGroundLinks(e.target.checked),
                className: "form-checkbox h-4 w-4 text-red-600"
              }),
              "Show Ground Links"
            ]
          )
        ]
      ),
      // Map
      React.createElement(
        'div', 
        { className: "relative w-full h-96 border border-gray-200 rounded-lg" },
        React.createElement(
          'svg',
          {
            viewBox: "0 0 800 400",
            className: "w-full h-full",
            style: { backgroundColor: '#f0f0f0' }
          },
          [
            // Grid lines - latitudes
            ...Array.from({ length: 9 }, (_, i) => 
              React.createElement('line', {
                key: `lat-${i}`,
                x1: 0,
                y1: i * (mapHeight/8),
                x2: mapWidth,
                y2: i * (mapHeight/8),
                stroke: "#ccc",
                strokeWidth: "1"
              })
            ),
            // Grid lines - longitudes
            ...Array.from({ length: 17 }, (_, i) => 
              React.createElement('line', {
                key: `lon-${i}`,
                x1: i * (mapWidth/16),
                y1: 0,
                x2: i * (mapWidth/16),
                y2: mapHeight,
                stroke: "#ccc",
                strokeWidth: "1"
              })
            ),
            // Links
            ...renderLinks(),
            // Ground Stations
            ...groundStations.map((station, index) => {
              const x = ((station.lon + 180) / 360) * mapWidth;
              const y = ((90 - station.lat) / 180) * mapHeight;
              
              return React.createElement(
                'g',
                { 
                  key: `gs-${station.name}`,
                  onMouseEnter: () => setHoveredNode(station.name),
                  onMouseLeave: () => setHoveredNode(null),
                  onClick: () => handleNodeClick(station.name),
                  style: { cursor: 'pointer' }
                },
                [
                  React.createElement('rect', {
                    x: x - 4,
                    y: y - 4,
                    width: 8,
                    height: 8,
                    fill: hoveredNode === station.name ? "#dc2626" : "#ef4444",
                    transform: `rotate(45 ${x} ${y})`
                  }),
                  React.createElement('text', {
                    x: x + 6,
                    y: y + 4,
                    fontSize: "10",
                    fill: "#ef4444",
                    fontWeight: hoveredNode === station.name ? "bold" : "normal"
                  }, station.name)
                ]
              );
            }),
            // Satellites
            ...satellites.map((sat, index) => {
              const x = ((sat.lon + 180) / 360) * mapWidth;
              const y = ((90 - sat.lat) / 180) * mapHeight;
              
              return React.createElement(
                'g',
                { 
                  key: `sat-${sat.name}`,
                  onMouseEnter: () => setHoveredNode(sat.name),
                  onMouseLeave: () => setHoveredNode(null),
                  onClick: () => handleNodeClick(sat.name),
                  style: { cursor: 'pointer' }
                },
                [
                  React.createElement('circle', {
                    cx: x,
                    cy: y,
                    r: hoveredNode === sat.name ? "5" : "4",
                    fill: hoveredNode === sat.name ? "#1e40af" : "#1d4ed8"
                  }),
                  React.createElement('text', {
                    x: x + 6,
                    y: y + 4,
                    fontSize: "10",
                    fill: "#1d4ed8",
                    fontWeight: hoveredNode === sat.name ? "bold" : "normal"
                  }, sat.name)
                ]
              );
            })
          ]
        )
      )
    ]
  );
};