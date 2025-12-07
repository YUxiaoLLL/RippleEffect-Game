// convert_dxf_to_json.js
// Usage: node convert_dxf_to_json.js <input.dxf> [output.json]
const fs = require('fs');
const DxfParser = require('dxf-parser');

// --- Helper Functions for Parametric Computation ---

/**
 * Computes the centroid of a polygon.
 * @param {Array<[number, number]>} vertices - The vertices of the polygon.
 * @returns {{x: number, y: number}} The centroid coordinates.
 */
function getCentroid(vertices) {
    let area = 0;
    let cx = 0;
    let cy = 0;
    for (let i = 0; i < vertices.length; i++) {
        const p1 = vertices[i];
        const p2 = vertices[(i + 1) % vertices.length];
        const crossProduct = (p1[0] * p2[1] - p2[0] * p1[1]);
        area += crossProduct;
        cx += (p1[0] + p2[0]) * crossProduct;
        cy += (p1[1] + p2[1]) * crossProduct;
    }
    const signedArea = area / 2;
    if (Math.abs(signedArea) < 1e-9) { // Check for degenerate polygons
        // Fallback for degenerate polygons: average of vertices
        let sumX = 0, sumY = 0;
        vertices.forEach(([x, y]) => { sumX += x; sumY += y; });
        return { x: sumX / vertices.length, y: sumY / vertices.length };
    }
    return { x: cx / (6 * signedArea), y: cy / (6 * signedArea) };
}

/**
 * Computes the Axis-Aligned Bounding Box (AABB).
 * @param {Array<[number, number]>} vertices - The vertices of the polygon.
 * @returns {{minX: number, minY: number, maxX: number, maxY: number, width: number, height: number}} The bounding box.
 */
function getBoundingBox(vertices) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    vertices.forEach(([x, y]) => {
        minX = Math.min(minX, x);
        minY = Math.min(minY, y);
        maxX = Math.max(maxX, x);
        maxY = Math.max(maxY, y);
    });
    return { minX, minY, maxX, maxY, width: maxX - minX, height: maxY - minY };
}

// --- Main Execution ---

const input = process.argv[2];
const output = process.argv[3] || 'static/scene.json';

if (!input) {
    console.error('Usage: node convert_dxf_to_json.js <input.dxf> [output.json]');
    process.exit(1);
}

const parser = new DxfParser();
try {
    const dxf = parser.parseSync(fs.readFileSync(input, 'utf8'));
    const sceneState = { entities: [] };
    const codenameCounters = {};

    (dxf.entities || []).forEach(e => {
        // We only care about closed polygonal shapes for now.
        if (e.type === 'LWPOLYLINE' && e.shape === true) { // Use the reliable 'shape' flag for closed LWPOLYLINES
            const vertices = (e.vertices || []).map(v => [v.x, v.y]);
            if (vertices.length < 3) return;

            const layer = e.layer || '0';
            
            // Generate unique codename (e.g., 'hotel-0', 'hotel-1')
            if (!codenameCounters[layer]) {
                codenameCounters[layer] = 0;
            }
            const id = `${layer}-${codenameCounters[layer]++}`;

            // Compute parameters
            const centroid = getCentroid(vertices);
            const bbox = getBoundingBox(vertices);

            const entity = {
                id: id,
                type: layer, // The 'type' is derived from the layer name
                layer: layer,
                geometryType: 'polygon', // For now, all are polygons
                params: {
                    width: parseFloat(bbox.width.toFixed(2)),
                    length: parseFloat(bbox.height.toFixed(2)),
                    center: [parseFloat(centroid.x.toFixed(2)), parseFloat(centroid.y.toFixed(2))],
                    rotation: 0 // Placeholder for now
                },
                // Preserve original geometry for fallback/rendering
                raw_geometry: vertices 
            };

            sceneState.entities.push(entity);
        }
    });

    fs.writeFileSync(output, JSON.stringify(sceneState, null, 2));
    console.log(`✅ Converted to ${output} (entities: ${sceneState.entities.length})`);

} catch (err) {
    console.error('Error parsing DXF file:', err);
    process.exit(1);
}
