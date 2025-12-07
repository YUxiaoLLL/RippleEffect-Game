import os
import requests

# Ensure directory exists
output_dir = "static/images/onboarding"
os.makedirs(output_dir, exist_ok=True)

assets = {
    # Light Theme (Councilor, Resident, etc.)
    "light_bg_main.png": "https://www.figma.com/api/mcp/asset/889c3e31-b45b-4cf3-b581-9022c7ae38fe", # Texture
    "light_mask_1.png": "https://www.figma.com/api/mcp/asset/e18ad604-452c-45ec-b970-77eb46a7cea3",
    "light_mask_2.png": "https://www.figma.com/api/mcp/asset/19f19b02-5187-48dd-a199-11595206e50b",
    "light_mask_3.png": "https://www.figma.com/api/mcp/asset/f4121385-8fdc-4003-bbe0-632b119f3170",
    "line_separator.png": "https://www.figma.com/api/mcp/asset/252beb94-f9ec-4c04-b424-6095b1ca389f",
    
    # Dark Theme (Developer)
    "dark_bg_texture.png": "https://www.figma.com/api/mcp/asset/f53b1f0a-6909-4220-8e17-ddcc613ab93c",
    
    # Character Portraits (using the 'Ani' assets from Figma which seem to be the full portraits)
    "councilor_portrait.png": "https://www.figma.com/api/mcp/asset/c410d0f9-0bc6-456f-9ef1-885145ced18b",
    "developer_portrait.png": "https://www.figma.com/api/mcp/asset/d6d3a7fd-9467-48e8-96c9-1d7078d5c8df",
    
    # Note: I need to infer or use placeholders for other characters since I only got specific node IDs for Councilor and Developer.
    # I will use the Councilor portrait as a placeholder for other Light theme characters for now, 
    # or I can try to reuse the small icons from role selection if they are high res enough (probably not).
    # ideally the user would provide links for all profiles, but I will proceed with these for the template structure.
    
    # UI Elements
    "back_arrow_box.png": "https://www.figma.com/api/mcp/asset/b4782e87-8962-41ce-9f54-02d2af9dddc2", # Vector inside the box
    "line_8.png": "https://www.figma.com/api/mcp/asset/165ba24b-1839-49ef-8d58-1c79ca71036b" # Line in text area
}

for filename, url in assets.items():
    try:
        print(f"Downloading {filename}...")
        response = requests.get(url)
        response.raise_for_status()
        with open(os.path.join(output_dir, filename), 'wb') as f:
            f.write(response.content)
        print(f"Saved {filename}")
    except Exception as e:
        print(f"Failed to download {filename}: {e}")
