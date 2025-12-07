import requests
import os

assets = {
    "background.png": "https://www.figma.com/api/mcp/asset/24b9e2d3-025e-46ab-9340-a2a47860df23",
    "chapter4_bakery.png": "https://www.figma.com/api/mcp/asset/17ddba4a-4f1e-48de-ad19-622c6b4c9647",
    "chapter3_miniapart.png": "https://www.figma.com/api/mcp/asset/a7a9d582-9d6a-41ba-ac32-a78ccb24d508",
    "chapter2_garden.png": "https://www.figma.com/api/mcp/asset/a3687dc0-9ea1-4d81-8aaa-854f0ae1def6",
    "chapter1_venue.png": "https://www.figma.com/api/mcp/asset/7dfb22f1-338e-4844-88ff-3e51603747d2",
    "line_texture.png": "https://www.figma.com/api/mcp/asset/e0b4eb1d-74d9-4929-93d4-017744c5900e"
}

output_dir = "static/images/chapter_selection"

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
