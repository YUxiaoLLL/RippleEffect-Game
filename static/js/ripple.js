let planData;
let view = { x: 0, y: 0, scale: 1 };
let initialScale = 1;
let worldBounds;
let hoveredItem = null;
let selectedItem = null;

const colors = {
    buildings: [50, 50, 50],
    roads: [200, 200, 200],
    paths: [220, 220, 220],
    greens: [180, 220, 180],
    waters: [170, 210, 230],
    default: [128, 128, 128]
};

function preload() {
    fetch('/api/plan-data')
        .then(response => response.json())
        .then(data => {
            planData = data;
            console.log('Plan data loaded:', planData);
            calculateWorldBounds();
            resetView();
        })
        .catch(error => console.error('Error loading plan data:', error));
}

function setup() {
    const container = document.getElementById('plan-view-container');
    const canvas = createCanvas(container.offsetWidth, container.offsetHeight);
    canvas.parent('plan-view-container');
    pixelDensity(1); // Crucial for preventing high-DPI distortion
    noStroke();

    // --- Socket.IO Client Setup ---
    const socket = io();

    socket.on('connect', () => {
        console.log('Socket.IO connected!');
    });

    socket.on('issue_update', (data) => {
        console.log('Received issue update:', data);
        // TODO: Apply issue changes to the p5.js scene
    });
}

function draw() {
    background(250);

    if (!planData) {
        fill(0);
        textAlign(CENTER, CENTER);
        text('Loading map data...', width / 2, height / 2);
        return;
    }

    // Apply camera transformations
    translate(view.x, view.y);
    scale(view.scale); // Apply uniform zoom
    scale(1, -1); // Flip Y-axis


    const mx = screenToWorldX(mouseX);
    const my = screenToWorldY(mouseY);
    hoveredItem = null;

    // Draw all polygons and find hovered item
    for (const category in planData) {
        const color = colors[category] || colors.default;
        for (const item of planData[category]) {
            const isHovered = isPointInPolygon(mx, my, item.polygon.coordinates[0]);
            if (isHovered) {
                hoveredItem = item;
            }

            if (item === hoveredItem) {
                fill(255, 150, 0); // Hover color
            } else if (item === selectedItem) {
                fill(255, 0, 0); // Select color
            } else {
                fill(color[0], color[1], color[2]);
            }

            beginShape();
            for (const point of item.polygon.coordinates[0]) {
                vertex(point[0], point[1]); // Draw with original coordinates
            }
            endShape(CLOSE);
        }
    }

}

function calculateWorldBounds() {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const category in planData) {
        for (const item of planData[category]) {
            const bounds = item.bounds;
            minX = Math.min(minX, bounds[0]);
            minY = Math.min(minY, bounds[1]);
            maxX = Math.max(maxX, bounds[2]);
            maxY = Math.max(maxY, bounds[3]);
        }
    }
    worldBounds = { minX, minY, maxX, maxY };
}

function resetView() {
    const worldWidth = worldBounds.maxX - worldBounds.minX;
    const worldHeight = worldBounds.maxY - worldBounds.minY;
    const canvasAspectRatio = width / height;
    const worldAspectRatio = worldWidth / worldHeight;

    view.scale = Math.min(width / worldWidth, height / worldHeight) * 0.75;
    initialScale = view.scale;

    // Center the view
    const worldScreenWidth = worldWidth * view.scale;
    const worldScreenHeight = worldHeight * view.scale;
    view.x = (width - worldScreenWidth) / 2 - (worldBounds.minX * view.scale);
    view.y = height - ((height - worldScreenHeight) / 2 - (worldBounds.minY * view.scale));
}

// --- Coordinate Transformation Functions ---
function screenToWorldX(screenX) {
    return (screenX - view.x) / view.scale;
}
function screenToWorldY(screenY) {
    return (screenY - view.y) / -view.scale;
}

function mousePressed() {
    if (hoveredItem) {
        selectedItem = hoveredItem;
        updateInfoPanel();
    }
}

function mouseWheel(event) {
    const zoomFactor = 1.1;
    const minScale = initialScale; // Do not allow zooming out further than the initial view
    const maxScale = initialScale * 20; // Allow zooming in 20x from the initial view
    const mx = mouseX;
    const my = mouseY;

    // Get world coordinates before zoom
    const worldX_before = screenToWorldX(mx);
    const worldY_before = screenToWorldY(my);

    // Update the base scale
    if (event.deltaY < 0) {
        view.scale = Math.min(view.scale * zoomFactor, maxScale);
    } else {
        view.scale = Math.max(view.scale / zoomFactor, minScale);
    }

    // Get world coordinates after zoom
    const worldX_after = screenToWorldX(mx);
    const worldY_after = screenToWorldY(my);

    // Adjust view position to keep mouse position constant in world space
    view.x = mx - worldX_before * view.scale;
    view.y = my - worldY_before * -view.scale;

    return false; // Prevent page scrolling
}

function mouseDragged() {
    // Pan speed must be independent of the current zoom level
    view.x += movedX;
    view.y += movedY;
}


function isPointInPolygon(px, py, polygon) {
    let isInside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
        const xi = polygon[i][0], yi = polygon[i][1];
        const xj = polygon[j][0], yj = polygon[j][1];
        const intersect = ((yi > py) !== (yj > py)) && (px < (xj - xi) * (py - yi) / (yj - yi) + xi);
        if (intersect) isInside = !isInside;
    }
    return isInside;
}

function updateInfoPanel() {
    const infoText = document.getElementById('info-text');
    if (selectedItem && selectedItem.name) {
        infoText.textContent = `Selected: ${selectedItem.name}`;
    } else if (selectedItem) {
        infoText.textContent = `Selected: Unnamed ${selectedItem.type}`;
    } else {
        infoText.textContent = 'Click on a shape to see its name.';
    }
}
