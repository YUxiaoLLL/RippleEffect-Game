import geopandas as gpd
import os
import glob

# --- Configuration ---
INPUT_DIR = os.path.join('..', 'data_raw')
OUTPUT_DIR = os.path.join('..', 'data_cleaned')

def find_gdb_path(root_dir, keyword):
    """Recursively finds a .gdb directory containing the keyword."""
    # Search specifically for .gdb directories
    for root, dirs, files in os.walk(root_dir):
        for d in dirs:
            if d.endswith('.gdb'):
                # If keyword is provided, check if it's in the path or name
                # For topography, we usually look in a 'Topography' folder
                # For heights, we look in 'building_heights' folder
                full_path = os.path.join(root, d)
                if keyword.lower() in full_path.lower():
                    return full_path
    return None

# Dynamically find paths
# 1. Find Topography GDB
# Search for a GDB inside a 'Topography' folder or with 'mastermap' in name
TOPOGRAPHY_GDB = find_gdb_path(INPUT_DIR, 'topography')
if not TOPOGRAPHY_GDB:
    # Fallback: try to find *any* gdb that looks like mastermap if not in Topography folder
    TOPOGRAPHY_GDB = find_gdb_path(INPUT_DIR, 'mastermap')

# 2. Find Heights GDB
# Search for GDB inside 'building_heights' folder or with 'tq' in name
HEIGHTS_GDB = find_gdb_path(INPUT_DIR, 'building_heights')
if not HEIGHTS_GDB:
    HEIGHTS_GDB = find_gdb_path(INPUT_DIR, 'tq')

OUTPUT_GEOJSON = os.path.join(OUTPUT_DIR, 'buildings_3d.geojson')

print(f"Discovered Inputs:\n  Topography: {TOPOGRAPHY_GDB}\n  Heights: {HEIGHTS_GDB}")

# --- Main Processing Function ---
def process_gis_data():
    """Processes raw GIS data into a clean, 3D-ready GeoJSON file."""
    print("--- Starting GIS Data Processing (3D Pipeline) ---")

    if not TOPOGRAPHY_GDB or not HEIGHTS_GDB:
        print("Error: Could not locate necessary GDB files in data_raw.")
        return

    # 1. Load data from File Geodatabase files
    try:
        print(f"Reading topography data from: {TOPOGRAPHY_GDB}")
        # List layers to be safe, usually it's 'TopographicArea'
        topo_layers = gpd.list_layers(TOPOGRAPHY_GDB)
        print(f"  Available layers: {topo_layers['name'].tolist()}")
        # Pick the first layer usually, or look for 'TopographicArea'
        layer_name = 'TopographicArea'
        if layer_name not in topo_layers['name'].values:
             layer_name = topo_layers['name'].iloc[0] # Fallback to first layer
        
        all_layers = gpd.read_file(TOPOGRAPHY_GDB, layer=layer_name)
        
        print(f"Reading height data from: {HEIGHTS_GDB}")
        heights_layers = gpd.list_layers(HEIGHTS_GDB)
        print(f"  Available layers: {heights_layers['name'].tolist()}")
        h_layer = heights_layers['name'].iloc[0]
        heights = gpd.read_file(HEIGHTS_GDB, layer=h_layer)

    except Exception as e:
        print(f"\nError: Could not read Geodatabase files.")
        print(f"Original error: {e}")
        return

    # 2. Filter to building footprints only
    # OS MasterMap feature codes for buildings
    building_codes = [10021, 10023]
    # --- Process Buildings ---
    buildings = all_layers[all_layers['featurecode'].isin(building_codes)].copy()
    print(f"Filtered to {len(buildings)} building footprints.")
    buildings['geometry'] = buildings.geometry.buffer(0)
    heights_simplified = heights[['absh2', 'geometry']]
    buildings_with_height = gpd.sjoin(buildings, heights_simplified, how='inner', predicate='intersects')
    buildings_with_height = buildings_with_height.drop_duplicates(subset=['fid'])
    buildings_with_height = buildings_with_height.rename(columns={'absh2': 'height'})
    print(f"Processed {len(buildings_with_height)} buildings with height data.")

    # --- Process Other Landscape Features ---
    categories = {
        'water': ['Inland Water'],
        'greens': ['Natural Environment'],
        'roads': ['Road Or Track'],
        'paths': ['Path']
    }

    for name, groups in categories.items():
        print(f"Processing {name}...")
        # Filter by descriptive group
        gdf = all_layers[all_layers['descriptivegroup'].isin(groups)].copy()
        # Repair geometry
        gdf['geometry'] = gdf.geometry.buffer(0)
        # Export to its own file
        output_path = os.path.join(OUTPUT_DIR, f"{name}.geojson")
        gdf.to_file(output_path, driver='GeoJSON')
        print(f" -> Exported {len(gdf)} features to {output_path}")

    # --- Export Buildings ---
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    buildings_with_height.to_file(OUTPUT_GEOJSON, driver='GeoJSON')
    print(f"\nSuccessfully exported cleaned building data to: {OUTPUT_GEOJSON}")
    print("--- GIS Data Processing Complete ---")

if __name__ == '__main__':
    process_gis_data()
