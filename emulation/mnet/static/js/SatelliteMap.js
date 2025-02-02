/** 
 * SatelliteMap.js 
 * (Load with <script type="text/babel" src="/static/js/SatelliteMap.js"></script>)
 * Make sure React, ReactDOM, and Babel are loaded in your HTML <head> or <body> before this script.
 */

// 1) Define your SatelliteIcon component (inline SVG, no exports)
const SatelliteIcon = ({ size = 12, color = 'currentColor' }) => {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={color}
      stroke={color}
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M13 7 9 3 5 7l4 4" />
      <path d="m17 11 4 4-4 4-4-4" />
      <path d="m8 12 4 4 6-6-4-4Z" />
      <path d="m16 8 3-3" />
      <path d="M9 21a6 6 0 0 0-6-6" />
    </svg>
  );
};

// 2) Define your GroundStationIcon component
const GroundStationIcon = ({ size = 12, color = 'currentColor' }) => {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={color}
      stroke={color}
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 10a7.31 7.31 0 0 0 10 10Z"/>
      <path d="m9 15 3-3"/>
      <path d="M17 13a6 6 0 0 0-6-6"/>
      <path d="M21 13A10 10 0 0 0 11 3"/>
    </svg>
  );
};

// 3) Define ShipIcon component
const ShipIcon = ({ size = 12, color = 'currentColor' }) => {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={color}
      stroke={color}
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M2 21c.6.5 1.2 1 2.4 1 2.4 0 2.4-1 4.8-1 2.4 0 2.4 1 4.8 1 2.4 0 2.4-1 4.8-1 1.2 0 1.8.5 2.4 1"/>
      <path d="M4 19l-2 2"/>
      <path d="M22 19l-2 2"/>
      <path d="M12 6H8v5c0 1 .5 2 2 2s2-1 2-2z"/>
      <path d="M16 8h0M4 15l16 .01M20 16.01L12 16"/>
      <path d="M12 10v6"/>
      <path d="M12 3v3"/>
    </svg>
  );
};
// 4) Define the main SatelliteMap component
function SatelliteMap() {
  // === Dimensions ===
  const mapWidth = 800;
  const mapHeight = 400;

  // Maximum zoom based on original full-size map
  const originalImageWidth = 5400;
  const originalImageHeight = 2700;
  const MAX_SCALE = Math.max(
    originalImageWidth / mapWidth,
    originalImageHeight / mapHeight
  );
  const MIN_SCALE = 1;

  // === React State ===
  const [satellites, setSatellites] = React.useState([]);
  const [groundStations, setGroundStations] = React.useState([]);
  const [vessels, setVessels] = React.useState([]);
  const [satelliteLinks, setSatelliteLinks] = React.useState([]);
  const [groundUplinks, setGroundUplinks] = React.useState([]);

  // Link visibility toggles
  const [showInPlaneLinks, setShowInPlaneLinks] = React.useState(false);
  const [showCrossPlaneLinks, setShowCrossPlaneLinks] = React.useState(false);
  const [showGroundLinks, setShowGroundLinks] = React.useState(false);

  // Hovered node name
  const [hoveredNode, setHoveredNode] = React.useState(null);

  // Pan & zoom
  const [transform, setTransform] = React.useState({ x: 0, y: 0, scale: 1 });
  const [isDragging, setIsDragging] = React.useState(false);
  const [dragStart, setDragStart] = React.useState({ x: 0, y: 0 });

  // === Fetch Data from /positions ===
  React.useEffect(() => {
    const fetchPositions = async () => {
      try {
        const res = await fetch('/positions');
        const data = await res.json();
        console.log("Received data:", data); // Debug log
        setSatellites(data.satellites || []);
        setGroundStations(data.ground_stations || []);
        setVessels(data.vessels || []);
        setSatelliteLinks(data.satellite_links || []);
        setGroundUplinks(data.ground_uplinks || []);
      } catch (err) {
        console.error('Error fetching positions:', err);
      }
    };

    fetchPositions();
    const interval = setInterval(fetchPositions, 10000);
    return () => clearInterval(interval);
  }, []);

  // === Mouse Handlers ===
  function handleMouseDown(e) {
    if (e.button === 0) {
      setIsDragging(true);
      setDragStart({ x: e.clientX - transform.x, y: e.clientY - transform.y });
    }
  }

  function handleMouseMove(e) {
    if (isDragging) {
      const newX = e.clientX - dragStart.x;
      const newY = e.clientY - dragStart.y;
      const maxX = mapWidth * (transform.scale - 1);
      const maxY = mapHeight * (transform.scale - 1);

      setTransform((prev) => ({
        ...prev,
        x: clamp(newX, -maxX, 0),
        y: clamp(newY, -maxY, 0),
      }));
    }
  }

  function handleMouseUp() {
    setIsDragging(false);
  }

  function handleMapEnter() {
    document.body.style.overflow = 'hidden';
  }

  function handleMapLeave() {
    document.body.style.overflow = 'auto';
    setIsDragging(false);
  }
// === Utility Functions ===
function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function getNodeCoordinates(node) {
  const x = ((node.lon + 180) / 360) * mapWidth;
  const y = ((90 - node.lat) / 180) * mapHeight;
  return [x, y];
}

function areInSamePlane(sat1Name, sat2Name) {
  const ring1 = parseInt(sat1Name.split('_')[0].substring(1));
  const ring2 = parseInt(sat2Name.split('_')[0].substring(1));
  return ring1 === ring2;
}

// Find a node by name (satellite, ground station, or vessel)
function findNode(name) {
  return (
    satellites.find((s) => s.name === name) ||
    groundStations.find((g) => g.name === name) ||
    vessels.find((v) => v.name === name)
  );
}

// All connected nodes for a given nodeName
function getConnectedNodes(nodeName) {
  const connections = new Set();

  // Satellite-satellite
  satelliteLinks.forEach((link) => {
    if (link.up) {
      if (link.node1_name === nodeName) {
        connections.add(link.node2_name);
      } else if (link.node2_name === nodeName) {
        connections.add(link.node1_name);
      }
    }
  });

  // Ground station and vessel uplinks
  groundUplinks.forEach((station) => {
    if (station.ground_node === nodeName) {
      station.uplinks.forEach((uplink) => connections.add(uplink.sat_node));
    } else {
      station.uplinks.forEach((uplink) => {
        if (uplink.sat_node === nodeName) {
          connections.add(station.ground_node);
        }
      });
    }
  });

  return connections;
}

// Node click handler
function handleNodeClick(nodeName) {
  const path = nodeName.startsWith('R') ? 'router' : 
              nodeName.startsWith('G_') ? 'station' : 
              'vessel';
  window.open(`/view/${path}/${nodeName}`, '_blank');
}

// === Render link lines ===
function renderLinks() {
  const elements = [];
  let keyCounter = 0;

  function drawLink(node1Name, node2Name, color, opacity, k) {
    const node1 = findNode(node1Name);
    const node2 = findNode(node2Name);
    if (!node1 || !node2) return;

    const [x1, y1] = getNodeCoordinates(node1);
    const [x2, y2] = getNodeCoordinates(node2);
    const dx = x2 - x1;
    const strokeWidth = 2 / transform.scale;

    // Date-line wrap handling
    if (Math.abs(dx) > mapWidth / 2) {
      if (x1 < x2) {
        elements.push(
          <line
            key={`${k}-left`}
            x1={x1}
            y1={y1}
            x2={x2 - mapWidth}
            y2={y2}
            stroke={color}
            strokeWidth={strokeWidth}
            strokeOpacity={opacity}
          />,
          <line
            key={`${k}-right`}
            x1={x1 + mapWidth}
            y1={y1}
            x2={x2}
            y2={y2}
            stroke={color}
            strokeWidth={strokeWidth}
            strokeOpacity={opacity}
          />
        );
      } else {
        elements.push(
          <line
            key={`${k}-left`}
            x1={x1 - mapWidth}
            y1={y1}
            x2={x2}
            y2={y2}
            stroke={color}
            strokeWidth={strokeWidth}
            strokeOpacity={opacity}
          />,
          <line
            key={`${k}-right`}
            x1={x1}
            y1={y1}
            x2={x2 + mapWidth}
            y2={y2}
            stroke={color}
            strokeWidth={strokeWidth}
            strokeOpacity={opacity}
          />
        );
      }
    } else {
      elements.push(
        <line
          key={k}
          x1={x1}
          y1={y1}
          x2={x2}
          y2={y2}
          stroke={color}
          strokeWidth={strokeWidth}
          strokeOpacity={opacity}
        />
      );
    }
  }
// If hovering a node, highlight just that node's connections
if (hoveredNode) {
  const connected = getConnectedNodes(hoveredNode);
  connected.forEach((otherName) => {
    const isGroundLink = 
      hoveredNode.startsWith('G_') || 
      hoveredNode.startsWith('V_') || 
      otherName.startsWith('G_') || 
      otherName.startsWith('V_');
    const color = isGroundLink
      ? '#ef4444'
      : areInSamePlane(hoveredNode, otherName)
      ? '#22c55e'
      : '#1d4ed8';

    drawLink(hoveredNode, otherName, color, 1, `hover-${keyCounter++}`);
  });
  return elements;
}

// Otherwise use toggles
if (showInPlaneLinks || showCrossPlaneLinks) {
  satelliteLinks.forEach((link, idx) => {
    if (link.up) {
      const inPlane = areInSamePlane(link.node1_name, link.node2_name);
      if ((inPlane && showInPlaneLinks) || (!inPlane && showCrossPlaneLinks)) {
        const color = inPlane ? '#22c55e' : '#1d4ed8';
        drawLink(link.node1_name, link.node2_name, color, 0.5, `sat-${idx}`);
      }
    }
  });
}

if (showGroundLinks) {
  groundUplinks.forEach((station, sIdx) => {
    station.uplinks.forEach((uplink, uIdx) => {
      if (uplink) {
        drawLink(
          station.ground_node,
          uplink.sat_node,
          '#ef4444',
          0.5,
          `gs-${sIdx}-${uIdx}`
        );
      }
    });
  });
}

return elements;
}

// Pick which icon to use
function getIconComponent(node) {
if (node.name.startsWith('G_')) {
  return GroundStationIcon;
} else if (node.name.startsWith('V_')) {
  return ShipIcon;
}
return SatelliteIcon;
}

// === Render all nodes ===
function renderNodes() {
console.log("Rendering nodes - Vessels:", vessels); // Debug log
const allNodes = [...satellites, ...groundStations, ...vessels];

return allNodes.map((node) => {
  const [x, y] = getNodeCoordinates(node);
  const isHovered = hoveredNode === node.name;
  const iconSize = 12;

  // Decide colors based on node type
  const defaultColor = node.name.startsWith('G_') ? '#22c55e' : 
                     node.name.startsWith('V_') ? '#1d4ed8' : 
                     '#1d4ed8';
  const hoverColor = 'red';
  const iconColor = isHovered ? hoverColor : defaultColor;
  const textColor = isHovered ? hoverColor : defaultColor;

  // Pick icon component based on node type
  const IconComponent = getIconComponent(node);

  return (
    <g
      key={node.name}
      transform={`translate(${x - iconSize / 2}, ${y - iconSize / 2})`}
      style={{ cursor: 'pointer' }}
      onMouseEnter={(e) => {
        e.stopPropagation();
        setHoveredNode(node.name);
      }}
      onMouseLeave={(e) => {
        e.stopPropagation();
        setHoveredNode(null);
      }}
      onClick={(e) => {
        e.stopPropagation();
        handleNodeClick(node.name);
      }}
    >
      <IconComponent size={iconSize} color={iconColor} />
      <text
        x={iconSize + 4}
        y={iconSize / 2}
        fontSize={12}
        fill={textColor}
        //fontWeight="bold"
        alignmentBaseline="middle"
      >
        {node.name}
      </text>
    </g>
  );
});
}
// Handle wheel zoom
function handleWheel(e) {
  e.preventDefault();
  const scaleFactor = e.deltaY > 0 ? 0.9 : 1.1;
  const newScale = clamp(transform.scale * scaleFactor, MIN_SCALE, MAX_SCALE);

  if (
    (transform.scale === MAX_SCALE && scaleFactor > 1) ||
    (transform.scale === MIN_SCALE && scaleFactor < 1)
  ) {
    return; // At zoom limit
  }

  const rect = e.currentTarget.getBoundingClientRect();
  const mouseX = e.clientX - rect.left;
  const mouseY = e.clientY - rect.top;

  // Zoom point in map coords
  const zoomPointX = (mouseX - transform.x) / transform.scale;
  const zoomPointY = (mouseY - transform.y) / transform.scale;

  // New top-left
  const newX = mouseX - zoomPointX * newScale;
  const newY = mouseY - zoomPointY * newScale;

  const maxX = mapWidth * (newScale - 1);
  const maxY = mapHeight * (newScale - 1);

  setTransform({
    scale: newScale,
    x: clamp(newX, -maxX, 0),
    y: clamp(newY, -maxY, 0),
  });
}

// === Return JSX ===
return (
  <div className="w-full max-w-4xl mt-4">
    {/* Toggles */}
    <div className="mb-4 flex gap-4">
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={showInPlaneLinks}
          onChange={(e) => setShowInPlaneLinks(e.target.checked)}
        />
        Show In-Plane Links
      </label>
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={showCrossPlaneLinks}
          onChange={(e) => setCrossPlaneLinks(e.target.checked)}
        />
        Show Cross-Plane Links
      </label>
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={showGroundLinks}
          onChange={(e) => setShowGroundLinks(e.target.checked)}
        />
        Show Ground Links
      </label>
    </div>

    {/* The map container */}
    <div
      className="relative w-full h-96 border border-gray-200 rounded-lg overflow-hidden"
      onWheel={handleWheel}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseEnter={handleMapEnter}
      onMouseLeave={handleMapLeave}
    >
      <svg
        viewBox="0 0 800 400"
        className="w-full h-full"
        style={{ backgroundColor: '#f0f0f0' }}
      >
        <g
          transform={`translate(${transform.x}, ${transform.y}) scale(${transform.scale})`}
        >
          {/* Background image */}
          <image
            href="/static/images/worldmap.jpg"
            x={0}
            y={0}
            width={mapWidth}
            height={mapHeight}
            preserveAspectRatio="none"
          />

          {/* Link lines */}
          {renderLinks()}

          {/* Node icons + labels */}
          {renderNodes()}
        </g>
      </svg>
    </div>
  </div>
);
}

// 5) Finally, render your SatelliteMap into #satellite-map
ReactDOM.render(<SatelliteMap />, document.getElementById('satellite-map'));