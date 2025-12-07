import json
import os
from shapely.geometry import shape, mapping, box, Polygon, MultiPolygon, LineString
from shapely.ops import unary_union, split

# 数据目录
# Use absolute path relative to this script file
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data_cleaned')

def load_geometry(filename):
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        print(f"Warning: {filename} not found, skipping.")
        return []
    
    try:
        with open(path, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading {filename}: {e}")
        return []
    
    geoms = []
    for feature in data['features']:
        # 1. 过滤掉那个 Outlier 建筑 (ID: osgb1000041681948)
        if feature.get('properties', {}).get('fid') == 'osgb1000041681948':
            continue
            
        s = shape(feature['geometry'])
        
        # Normalize to list of Polygons
        valid_polys = []
        if s.is_valid:
            if not s.is_empty:
                if isinstance(s, Polygon):
                    valid_polys.append(s)
                elif isinstance(s, MultiPolygon):
                    valid_polys.extend(s.geoms)
        else:
            fixed = s.buffer(0)
            if not fixed.is_empty:
                if isinstance(fixed, Polygon):
                    valid_polys.append(fixed)
                elif isinstance(fixed, MultiPolygon):
                    valid_polys.extend(fixed.geoms)
        
        geoms.extend(valid_polys)
            
    return geoms

def safe_union(geoms):
    if not geoms:
        return Polygon()
    
    # Try the fast way first
    try:
        return unary_union(geoms)
    except Exception:
        print("  ! Fast union failed, switching to iterative safe union...")
        
    # Fallback to slow but safe iterative union
    merged = Polygon()
    for i, g in enumerate(geoms):
        try:
            if not g.is_valid or g.is_empty:
                continue
            merged = merged.union(g)
        except Exception as e:
            print(f"    Skipping bad geometry at index {i}: {e}")
            continue
            
    return merged

def generate_open_spaces():
    print("Loading existing layers...")
    buildings = load_geometry('buildings_3d.geojson')
    print(f"Loaded {len(buildings)} buildings.")
    
    roads = load_geometry('roads.geojson') 
    water = load_geometry('water.geojson')
    greens = load_geometry('greens.geojson')
    paths = load_geometry('paths.geojson') 

    if not buildings:
        print("Error: No building data found. Cannot determine bounds.")
        return

    print("Calculating world bounds based on buildings...")
    # 1. 计算底板范围 (Optimized: no need for union just for bounds)
    minx = min(g.bounds[0] for g in buildings)
    miny = min(g.bounds[1] for g in buildings)
    maxx = max(g.bounds[2] for g in buildings)
    maxy = max(g.bounds[3] for g in buildings)
    
    # 加上 5% padding
    dx = maxx - minx
    dy = maxy - miny
    padding = 0.05
    base_box = box(
        minx - dx * padding, 
        miny - dy * padding, 
        maxx + dx * padding, 
        maxy + dy * padding
    )
    
    print(f"World Bounds: {base_box.bounds}")

    print("Merging obstacles (this may take a moment)...")
    
    # 2. 合并所有“非空地”元素 - Incremental Safe Approach
    print("Merging buildings...")
    union_buildings = safe_union(buildings)

    print("Merging roads...")
    union_roads = safe_union(roads)

    print("Merging water...")
    union_water = safe_union(water)

    print("Merging greens...")
    union_greens = safe_union(greens)

    print("Merging paths...")
    union_paths = safe_union(paths)

    print("Combining all obstacles...")
    obstacle_union = union_buildings.union(union_roads).union(union_water).union(union_greens).union(union_paths)
    
    print("Calculating Boolean Difference (Base - Obstacles)...")
    
    # 3. 执行减法：底板 - 障碍物
    # 这一步就是数学上的“补集运算”
    open_space_geom = base_box.difference(obstacle_union)

    # 4. 提取独立多边形
    raw_plots = []
    if isinstance(open_space_geom, Polygon):
        raw_plots.append(open_space_geom)
    elif isinstance(open_space_geom, MultiPolygon):
        raw_plots.extend(open_space_geom.geoms)
    
    print(f"Found {len(raw_plots)} raw fragments. Now applying morphological splitting...")

    # --- Morphological Splitting (Erosion -> Dilation) ---
    # Logic: Shrink by X meters to break narrow necks, then grow back.
    
    # Pass 1: Standard Split (Back to 8.0m as user liked it)
    BASE_EROSION = 8.0 
    
    final_plots = []
    
    def recursive_split(geom, erosion_dist, depth=0):
        if geom.area < 100: return []
        
        # 1. Erode
        eroded = geom.buffer(-erosion_dist)
        
        if eroded.is_empty:
            # If it disappears, it's a corridor/narrow space
            return [{ "geom": geom, "type": "corridor" }]
            
        # 2. Dilate back
        dilated = eroded.buffer(erosion_dist * 0.9)
        
        # 3. Separate disjoint parts
        parts = []
        if isinstance(dilated, Polygon):
            parts.append(dilated)
        elif isinstance(dilated, MultiPolygon):
            parts.extend(dilated.geoms)
            
        results = []
        courtyard_parts = []
        
        for part in parts:
            clean_part = part.intersection(geom)
            
            # --- STRATEGY 1: FORCE GEOMETRIC SPLIT (For Massive "Fat" Blobs) ---
            # If > 15,000m2, morphological split likely failed to find a neck.
            # We must cut it with a straight line.
            if clean_part.area > 15000 and depth < 5:
                print(f"  - MASSIVE chunk ({clean_part.area:.0f}m2). Force slicing geometrically...")
                
                minx, miny, maxx, maxy = clean_part.bounds
                width = maxx - minx
                height = maxy - miny
                centroid = clean_part.centroid
                
                # Cut perpendicular to longest side
                if width > height:
                    # Vertical cut
                    cut_line = LineString([(centroid.x, miny - 10), (centroid.x, maxy + 10)])
                else:
                    # Horizontal cut
                    cut_line = LineString([(minx - 10, centroid.y), (maxx + 10, centroid.y)])
                
                # Execute split using difference (more robust than ops.split)
                splitter_poly = cut_line.buffer(0.05) 
                split_result = clean_part.difference(splitter_poly)
                
                # Handle result
                pieces = []
                if isinstance(split_result, Polygon):
                    pieces.append(split_result)
                elif isinstance(split_result, MultiPolygon):
                    pieces.extend(split_result.geoms)
                
                # Recurse on pieces
                for piece in pieces:
                    if piece.area > 50:
                         # Continue with standard erosion for the new piece
                        sub_results = recursive_split(piece, erosion_dist, depth + 1)
                        results.extend(sub_results)
                        for sub in sub_results:
                            if sub['type'] == 'courtyard':
                                courtyard_parts.append(sub['geom'])
                continue # Skip the rest for this part

            # --- STRATEGY 2: TARGETED MORPHOLOGICAL SPLIT (For Large Complex Shapes) ---
            # If > 4,000m2, try eroding harder to find a neck.
            if clean_part.area > 4000 and depth < 3:
                print(f"  - Large chunk detected ({clean_part.area:.0f}m2), applying TARGETED EROSION...")
                sub_results = recursive_split(clean_part, erosion_dist * 1.5, depth + 1)
                
                # Fail-safe: if it vanished into corridors, keep original
                all_corridors = all(sub['type'] == 'corridor' for sub in sub_results)
                if all_corridors and sub_results: # ensure sub_results not empty
                    print(f"    ! Erosion killed it. Keeping original.")
                    results.append({ "geom": clean_part, "type": "courtyard" })
                    courtyard_parts.append(clean_part)
                else:
                    results.extend(sub_results)
                    for sub in sub_results:
                        if sub['type'] == 'courtyard':
                            courtyard_parts.append(sub['geom'])
            else:
                # Accept as is
                results.append({ "geom": clean_part, "type": "courtyard" })
                courtyard_parts.append(clean_part)
        
        # 4. Identify the "Corridor" (Residue)
        if courtyard_parts:
            combined_cy = safe_union(courtyard_parts)
            residue = geom.difference(combined_cy)
        else:
            residue = geom
            
        # Split residue into distinct corridors
        if isinstance(residue, Polygon):
            if residue.area > 20:
                results.append({ "geom": residue, "type": "corridor" })
        elif isinstance(residue, MultiPolygon):
            for r in residue.geoms:
                if r.area > 20:
                    results.append({ "geom": r, "type": "corridor" })
                    
        return results

    for raw_plot in raw_plots:
        if raw_plot.area < 50: continue
        # Apply recursive splitting
        splits = recursive_split(raw_plot, BASE_EROSION)
        final_plots.extend(splits)

    print(f"Morphological split resulted in {len(final_plots)} sub-plots (before filtering).")

    # --- STABILIZE IDs: Spatial Sorting ---
    # Sort plots by their centroid coordinates:
    # Primary key: -Y (North to South)
    # Secondary key: X (West to East)
    # This ensures that 'courtyard_1' is always the top-left-most plot.
    final_plots.sort(key=lambda item: (-item['geom'].centroid.y, item['geom'].centroid.x))

    # 5. 生成 GeoJSON
    features = []
    valid_count = 0
    
    for idx, item in enumerate(final_plots):
        plot = item['geom']
        p_type = item['type']
        
        # User Request: Block/Remove corridors entirely
        if p_type == 'corridor':
            continue
            
        valid_count += 1
        features.append({
            "type": "Feature",
            "properties": {
                "id": f"{p_type}_{valid_count}", # Renumber nicely
                "type": p_type,
                "area": round(plot.area, 2)
            },
            "geometry": mapping(plot)
        })

    output = {
        "type": "FeatureCollection",
        "features": features
    }
    
    out_path = os.path.join(DATA_DIR, 'open_spaces.geojson')
    with open(out_path, 'w') as f:
        json.dump(output, f)
    
    print(f"Successfully generated {len(features)} open space plots at {out_path}")

if __name__ == "__main__":
    generate_open_spaces()
