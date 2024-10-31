const SatelliteMap = () => {
  const [satellites, setSatellites] = React.useState([]);
  const [groundStations, setGroundStations] = React.useState([]);
  const [satelliteLinks, setSatelliteLinks] = React.useState([]);
  const [groundUplinks, setGroundUplinks] = React.useState([]);
  const [showSatelliteLinks, setShowSatelliteLinks] = React.useState(false);
  const [showGroundLinks, setShowGroundLinks] = React.useState(false);
  
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
  
  // Create links between nodes
  const renderLinks = () => {
    const links = [];
    
    // Helper to find node by name
    const findNode = (name) => {
      const sat = satellites.find(s => s.name === name);
      if (sat) return sat;
      return groundStations.find(g => g.name === name);
    };
    
    if (showSatelliteLinks) {
      satelliteLinks.forEach((link, index) => {
        const node1 = findNode(link.node1_name);
        const node2 = findNode(link.node2_name);
        if (node1 && node2 && link.up) {
          const [x1, y1] = getNodeCoordinates(node1);
          const [x2, y2] = getNodeCoordinates(node2);
          links.push(
            React.createElement('line', {
              key: `sat-link-${index}`,
              x1, y1, x2, y2,
              stroke: "#1d4ed8",
              strokeWidth: "1",
              strokeOpacity: "0.5"
            })
          );
        }
      });
    }
    
    if (showGroundLinks) {
      groundUplinks.forEach((station, stationIndex) => {
        const groundStation = findNode(station.ground_node);
        if (groundStation) {
          station.uplinks.forEach((uplink, uplinkIndex) => {
            const satellite = findNode(uplink.sat_node);
            if (satellite) {
              const [x1, y1] = getNodeCoordinates(groundStation);
              const [x2, y2] = getNodeCoordinates(satellite);
              links.push(
                React.createElement('line', {
                  key: `ground-link-${stationIndex}-${uplinkIndex}`,
                  x1, y1, x2, y2,
                  stroke: "#ef4444",
                  strokeWidth: "1",
                  strokeOpacity: "0.5"
                })
              );
            }
          });
        }
      });
    }
    
    return links;
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
                { key: `gs-${station.name}` },
                [
                  React.createElement('rect', {
                    x: x - 4,
                    y: y - 4,
                    width: 8,
                    height: 8,
                    fill: "#ef4444",
                    transform: `rotate(45 ${x} ${y})`
                  }),
                  React.createElement('text', {
                    x: x + 6,
                    y: y + 4,
                    fontSize: "10",
                    fill: "#ef4444"
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
                { key: `sat-${sat.name}` },
                [
                  React.createElement('circle', {
                    cx: x,
                    cy: y,
                    r: "4",
                    fill: "#1d4ed8"
                  }),
                  React.createElement('text', {
                    x: x + 6,
                    y: y + 4,
                    fontSize: "10",
                    fill: "#1d4ed8"
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