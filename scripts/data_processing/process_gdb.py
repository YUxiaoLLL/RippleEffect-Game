import geopandas as gpd
import os
import glob
import fiona

# --- Configuration ---
# Input directory where you put your .gdb folder
INPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data_raw')

# Output directory for the frontend
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'frontend', 'static', '3d_data')

# Target Coordinate Reference System (Web Mercator or WGS84)
# Three.js usually works best with relative meters, but for GeoJSON loading mapbox/leaflet styles, WGS84 (EPSG:4326) is standard.
# If your 3D pipeline expects meters (like EPSG:27700 UK Grid), change this. 
# Based on previous context, we likely want WGS84 for GeoJSON portability or keeping original if the frontend handles projection.
# Let's stick to EPSG:4326 (Lat/Lon) for standard GeoJSON, unless your 3D viewer does projection.
# If the previous pipeline used raw coordinates, we might need to check. 
# For now, converting to 4326 is the safest default for web.
TARGET_CRS = "EPSG:4326" 

def list_layers(gdb_path):
    """List all layers in a GDB file."""
    try:
        layers = fiona.listlayers(gdb_path)
        return layers
    except Exception as e:
        print(f"Error reading layers from {gdb_path}: {e}")
        return []

def convert_layer_to_geojson(gdb_path, layer_name, output_name=None):
    """Reads a layer, reprojects it, and saves as GeoJSON."""
    if output_name is None:
        output_name = layer_name

    print(f"Processing layer: {layer_name}...")
    
    try:
        gdf = gpd.read_file(gdb_path, layer=layer_name)
        
        # Reproject if needed
        if gdf.crs != TARGET_CRS:
            print(f"  Reprojecting from {gdf.crs} to {TARGET_CRS}...")
            gdf = gdf.to_crs(TARGET_CRS)
        
        # Ensure output directory exists
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        output_path = os.path.join(OUTPUT_DIR, f"{output_name}.geojson")
        gdf.to_file(output_path, driver='GeoJSON')
        print(f"  Saved to: {output_path}")
        return True
    except Exception as e:
        print(f"  Failed to convert {layer_name}: {e}")
        return False

def main():
    # Find GDB files
    gdb_files = glob.glob(os.path.join(INPUT_DIR, "*.gdb"))
    
    if not gdb_files:
        print(f"No .gdb files found in {INPUT_DIR}")
        print("Please upload your .gdb folder there.")
        return

    print(f"Found GDB files: {gdb_files}")

    for gdb_path in gdb_files:
        print(f"\nScanning {os.path.basename(gdb_path)}...")
        layers = list_layers(gdb_path)
        print(f"Available layers: {layers}")
        
        # --- CUSTOMIZE THIS MAPPING BASED ON YOUR DATA ---
        # Map GDB Layer Name -> Desired Output Filename
        # Example: 'OS_Building' -> 'buildings_3d'
        # If you want to convert EVERYTHING, you can iterate through 'layers'
        
        # Heuristic matching (since exact names might change)
        for layer in layers:
            lower_name = layer.lower()
            output_name = layer # Default
            
            if 'building' in lower_name and 'height' in lower_name:
                output_name = 'buildings_3d'
            elif 'water' in lower_name:
                output_name = 'water'
            elif 'road' in lower_name or 'street' in lower_name:
                output_name = 'roads'
            elif 'green' in lower_name or 'park' in lower_name:
                output_name = 'greens'
            elif 'path' in lower_name:
                output_name = 'paths'
            elif 'open' in lower_name and 'space' in lower_name:
                output_name = 'open_spaces'
                
            # Convert
            convert_layer_to_geojson(gdb_path, layer, output_name)

if __name__ == "__main__":
    main()
