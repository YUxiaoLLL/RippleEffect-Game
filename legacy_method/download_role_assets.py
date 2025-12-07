import requests
import os

assets = {
    "bg_icon.png": "https://www.figma.com/api/mcp/asset/4170cab7-9f58-4eb9-834c-86a2ebe5f596",
    "developer.png": "https://www.figma.com/api/mcp/asset/48f163bb-c11f-4369-ac5c-1d2ff6cee0e2",
    "councilor.png": "https://www.figma.com/api/mcp/asset/c60dbd0f-f98c-4e19-b251-a2af9263a4fc",
    "resident.png": "https://www.figma.com/api/mcp/asset/ddb3b531-00ff-4838-b923-18362e3b6698",
    "activist.png": "https://www.figma.com/api/mcp/asset/8f9108c6-f73d-400f-b9c4-ff2d68e31dac",
    "header_icon.png": "https://www.figma.com/api/mcp/asset/65e88606-92ea-40b4-9f8a-43eeaaa01ab8",
    "architect.png": "https://www.figma.com/api/mcp/asset/bbf90801-4567-48c3-8264-0ee7acd0d053",
    "future_buyer.png": "https://www.figma.com/api/mcp/asset/f97aac4a-6fd3-40fc-8194-f4c154e04309"
}

output_dir = "static/images/role_selection"
os.makedirs(output_dir, exist_ok=True)

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
