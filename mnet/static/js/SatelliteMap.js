const SatelliteMap = () => {
  // Constants
  const mapWidth = 800;
  const mapHeight = 400;
  const originalImageWidth = 5400;
  const originalImageHeight = 2700;
  
  // Calculate max scale based on image dimensions
  const scaleX = originalImageWidth / mapWidth;
  const scaleY = originalImageHeight / mapHeight;
  const MAX_SCALE = Math.max(scaleX, scaleY);
  const MIN_SCALE = 1;

  // State
  const [satellites, setSatellites] = React.useState([]);
  const [groundStations, setGroundStations] = React.useState([]);
  const [satelliteLinks, setSatelliteLinks] = React.useState([]);
  const [groundUplinks, setGroundUplinks] = React.useState([]);
  const [showInPlaneLinks, setShowInPlaneLinks] = React.useState(false);
  const [showCrossPlaneLinks, setShowCrossPlaneLinks] = React.useState(false);
  const [showGroundLinks, setShowGroundLinks] = React.useState(false);
  const [hoveredNode, setHoveredNode] = React.useState(null);
  
  // Pan and zoom state
  const [transform, setTransform] = React.useState({ x: 0, y: 0, scale: 1 });
  const [isDragging, setIsDragging] = React.useState(false);
  const [dragStart, setDragStart] = React.useState({ x: 0, y: 0 });
  
  // Helper function to determine if two satellites are in the same plane
  const areInSamePlane = (sat1Name, sat2Name) => {
    const ring1 = parseInt(sat1Name.split('_')[0].substring(1));
    const ring2 = parseInt(sat2Name.split('_')[0].substring(1));
    return ring1 === ring2;
  };
  
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

    fetchPositions();
    const interval = setInterval(fetchPositions, 10000);
    return () => clearInterval(interval);
  }, []);

  // Helper function to clamp value between min and max
  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);
  const handleWheel = (e) => {
    e.preventDefault();  // Prevent page scroll

    const scaleFactor = e.deltaY > 0 ? 0.9 : 1.1;
    const newScale = clamp(transform.scale * scaleFactor, MIN_SCALE, MAX_SCALE);
    
    // If we've hit the scale limits, don't proceed with the transform
    if ((transform.scale === MAX_SCALE && scaleFactor > 1) ||
        (transform.scale === MIN_SCALE && scaleFactor < 1)) {
      return;
    }
    
    const boundingRect = e.currentTarget.getBoundingClientRect();
    const mouseX = e.clientX - boundingRect.left;
    const mouseY = e.clientY - boundingRect.top;

    // Calculate the point to zoom towards (in SVG coordinates)
    const zoomPointX = (mouseX - transform.x) / transform.scale;
    const zoomPointY = (mouseY - transform.y) / transform.scale;

    // Calculate new position
    const newX = mouseX - zoomPointX * newScale;
    const newY = mouseY - zoomPointY * newScale;

    // Calculate bounds
    const maxX = mapWidth * (newScale - 1);
    const maxY = mapHeight * (newScale - 1);

    // Clamp the position while allowing zoom at edges
    const clampedX = clamp(newX, -maxX, 0);
    const clampedY = clamp(newY, -maxY, 0);

    setTransform({
      scale: newScale,
      x: clampedX,
      y: clampedY
    });
  };

  const handleMouseDown = (e) => {
    if (e.button === 0) { // Left mouse button only
      setIsDragging(true);
      setDragStart({ x: e.clientX - transform.x, y: e.clientY - transform.y });
    }
  };

  const handleMouseMove = (e) => {
    if (isDragging) {
      const newX = e.clientX - dragStart.x;
      const newY = e.clientY - dragStart.y;

      // Calculate bounds based on current scale
      const maxX = mapWidth * (transform.scale - 1);
      const maxY = mapHeight * (transform.scale - 1);

      // Clamp position to prevent white space
      setTransform(prev => ({
        ...prev,
        x: clamp(newX, -maxX, 0),
        y: clamp(newY, -maxY, 0)
      }));
    }
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  const handleMapEnter = () => {
    document.body.style.overflow = 'hidden';
  };

  const handleMapLeave = () => {
    document.body.style.overflow = 'auto';
    setIsDragging(false);
  };

  const getNodeCoordinates = (node) => {
    const x = ((node.lon + 180) / 360) * mapWidth;
    const y = ((90 - node.lat) / 180) * mapHeight;
    return [x, y];
  };

  const getConnectedNodes = (nodeName) => {
    const connections = new Set();
    
    satelliteLinks.forEach(link => {
      if (link.node1_name === nodeName && link.up) {
        connections.add(link.node2_name);
      } else if (link.node2_name === nodeName && link.up) {
        connections.add(link.node1_name);
      }
    });
    
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
  const renderLinks = () => {
    const links = [];
    
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
        
        // Check if the link crosses the date line
        const dx = x2 - x1;
        if (Math.abs(dx) > mapWidth / 2) {
          // Link crosses the date line, draw it in two parts
          if (x1 < x2) {
            // Node 1 is on the left
            links.push(
              React.createElement('line', {
                key: `${key}-left`,
                x1, y1,
                x2: x2 - mapWidth, y2,
                stroke: color,
                strokeWidth: (hoveredNode ? "2" : "2") / transform.scale,
                strokeOpacity: opacity
              }),
              React.createElement('line', {
                key: `${key}-right`,
                x1: x1 + mapWidth, y1,
                x2, y2,
                stroke: color,
                strokeWidth: (hoveredNode ? "2" : "2") / transform.scale,
                strokeOpacity: opacity
              })
            );
          } else {
            // Node 1 is on the right
            links.push(
              React.createElement('line', {
                key: `${key}-left`,
                x1: x1 - mapWidth, y1,
                x2, y2,
                stroke: color,
                strokeWidth: (hoveredNode ? "2" : "2") / transform.scale,
                strokeOpacity: opacity
              }),
              React.createElement('line', {
                key: `${key}-right`,
                x1, y1,
                x2: x2 + mapWidth, y2,
                stroke: color,
                strokeWidth: (hoveredNode ? "2" : "2") / transform.scale,
                strokeOpacity: opacity
              })
            );
          }
        } else {
          // Normal link, no wrapping needed
          links.push(
            React.createElement('line', {
              key,
              x1, y1, x2, y2,
              stroke: color,
              strokeWidth: (hoveredNode ? "2" : "2") / transform.scale,
              strokeOpacity: opacity
            })
          );
        }
      }
    };
    
    if (hoveredNode) {
      const connections = getConnectedNodes(hoveredNode);
      connections.forEach((connectedNode, index) => {
        // Determine the type of link and color
        const isGroundLink = hoveredNode.startsWith('G_') || connectedNode.startsWith('G_');
        if (isGroundLink) {
          drawLink(
            hoveredNode,
            connectedNode,
            "#ef4444", // Red for ground links
            1,
            `hover-link-${index}`
          );
        } else {
          const isInPlaneLink = areInSamePlane(hoveredNode, connectedNode);
          drawLink(
            hoveredNode,
            connectedNode,
            isInPlaneLink ? "#22c55e" : "#1d4ed8", // Green for in-plane, blue for cross-plane
            1,
            `hover-link-${index}`
          );
        }
      });
    } else {
      if (showInPlaneLinks || showCrossPlaneLinks) {
        satelliteLinks.forEach((link, index) => {
          if (link.up) {
            const isInPlane = areInSamePlane(link.node1_name, link.node2_name);
            if ((isInPlane && showInPlaneLinks) || (!isInPlane && showCrossPlaneLinks)) {
              drawLink(
                link.node1_name,
                link.node2_name,
                isInPlane ? "#22c55e" : "#1d4ed8", // Green for in-plane, blue for cross-plane
                0.5,
                `sat-link-${index}`
              );
            }
          }
        });
      }
      
      if (showGroundLinks) {
        groundUplinks.forEach((station, stationIndex) => {
          station.uplinks.forEach((uplink, uplinkIndex) => {
            drawLink(
              station.ground_node,
              uplink.sat_node,
              "#ef4444", // Red for ground links
              0.5,
              `ground-link-${stationIndex}-${uplinkIndex}`
            );
          });
        });
      }
    }
    
    return links;
  };

  const handleNodeClick = (nodeName) => {
    const path = nodeName.startsWith('R') ? 'router' : 'station';
    window.open(`/view/${path}/${nodeName}`, '_blank');
  };
  return React.createElement(
    'div', 
    { className: "w-full max-w-4xl mt-4" },
    [
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
                checked: showInPlaneLinks,
                onChange: (e) => setShowInPlaneLinks(e.target.checked),
                className: "form-checkbox h-4 w-4 text-green-600"
              }),
              "Show In-Plane Links"
            ]
          ),
          React.createElement(
            'label',
            { className: "flex items-center gap-2" },
            [
              React.createElement('input', {
                type: "checkbox",
                checked: showCrossPlaneLinks,
                onChange: (e) => setShowCrossPlaneLinks(e.target.checked),
                className: "form-checkbox h-4 w-4 text-blue-600"
              }),
              "Show Cross-Plane Links"
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
      React.createElement(
        'div', 
        { 
          className: "relative w-full h-96 border border-gray-200 rounded-lg overflow-hidden",
          onWheel: handleWheel,
          onMouseDown: handleMouseDown,
          onMouseMove: handleMouseMove,
          onMouseUp: handleMouseUp,
          onMouseEnter: handleMapEnter,
          onMouseLeave: handleMapLeave,
        },
        React.createElement(
          'svg',
          {
            viewBox: "0 0 800 400",
            className: "w-full h-full",
            style: { backgroundColor: '#f0f0f0' }
          },
          [
            React.createElement(
              'g',
              {
                transform: `translate(${transform.x},${transform.y}) scale(${transform.scale})`
              },
              [
                // Background image
                React.createElement('image', {
                  href: "/static/images/worldmap.jpg",
                  x: 0,
                  y: 0,
                  width: mapWidth,
                  height: mapHeight,
                  preserveAspectRatio: "none"
                }),
                ...renderLinks(),
                ...groundStations.map((station, index) => {
                  const x = ((station.lon + 180) / 360) * mapWidth;
                  const y = ((90 - station.lat) / 180) * mapHeight;
                  const size = 8 / transform.scale;
                  
                  return React.createElement(
                    'g',
                    { 
                      key: `gs-${station.name}`,
                      onMouseEnter: (e) => {
                        e.stopPropagation();
                        setHoveredNode(station.name);
                      },
                      onMouseLeave: (e) => {
                        e.stopPropagation();
                        setHoveredNode(null);
                      },
                      onClick: (e) => {
                        e.stopPropagation();
                        handleNodeClick(station.name);
                      },
                      style: { cursor: 'pointer' },
                      transform: `translate(${x},${y})`
                    },
                    [
                      React.createElement('rect', {
                        x: -size/2,
                        y: -size/2,
                        width: size,
                        height: size,
                        fill: hoveredNode === station.name ? "#dc2626" : "#ef4444",
                        transform: `rotate(45, 0, 0)`
                      }),
                      React.createElement('text', {
                        x: size,
                        y: 0,
                        fontSize: 10 / transform.scale,
                        fill: "#ef4444",
                        fontWeight: hoveredNode === station.name ? "bold" : "normal",
                        dominantBaseline: "middle"
                      }, station.name)
                    ]
                  );
                }),
                ...satellites.map((sat, index) => {
                  const x = ((sat.lon + 180) / 360) * mapWidth;
                  const y = ((90 - sat.lat) / 180) * mapHeight;
                  const radius = (hoveredNode === sat.name ? 5 : 4) / transform.scale;
                  
                  return React.createElement(
                    'g',
                    { 
                      key: `sat-${sat.name}`,
                      onMouseEnter: (e) => {
                        e.stopPropagation();
                        setHoveredNode(sat.name);
                      },
                      onMouseLeave: (e) => {
                        e.stopPropagation();
                        setHoveredNode(null);
                      },
                      onClick: (e) => {
                        e.stopPropagation();
                        handleNodeClick(sat.name);
                      },
                      style: { cursor: 'pointer' },
                      transform: `translate(${x},${y})`
                    },
                    [
                      React.createElement('circle', {
                        r: radius,
                        fill: hoveredNode === sat.name ? "#1e40af" : "#1d4ed8"
                      }),
                      React.createElement('text', {
                        x: radius + 2,
                        y: 0,
                        fontSize: 10 / transform.scale,
                        fill: "#1d4ed8",
                        fontWeight: hoveredNode === sat.name ? "bold" : "normal",
                        dominantBaseline: "middle"
                      }, sat.name)
                    ]
                  );
                })
              ]
            )
          ]
        )
      )
    ]
  );
};