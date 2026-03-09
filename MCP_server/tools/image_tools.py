import base64
import requests
import os
import time
from server import mcp_tool
from fastapi import UploadFile, File
from typing import Union, Dict
from pathlib import Path

API_URL = os.environ.get(
    "VISION_API_URL",
    "http://149.165.153.129:8000/v1/chat/completions"
)
API_KEY = os.environ.get("VISION_API_KEY")
MODEL_NAME = os.environ.get(
    "VISION_API_MODEL",
    "Qwen/Qwen2.5-VL-7B-Instruct"
)

HEADERS = {"Content-Type": "application/json"}
if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

# --- Helper ---
def encode_bytes(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("utf-8")

# --- MCP Tools ---
@mcp_tool(
    tool_description=(
        "Describe the contents of an image using the configured vision model.\n\n"
        "Example usage:\n"
        '# Basic usage (uses default prompt):\n'
        'curl -X POST "http://149.165.147.219:8000/api/tool/describe_image" '
        '-F "file=@image.jpg"\n\n'
        '# With custom prompt_text:\n'
        'curl -X POST "http://149.165.147.219:8000/api/tool/describe_image" '
        '-F "file=@image.jpg" '
        '-F "prompt_text=What objects, people, or activities are visible in this image?"'
    )
)
def describe_image(
    file: Union[Dict[str, str], UploadFile],
    prompt_text: str = "Describe what is in this image."
) -> str:
    """Describe the contents of an image using the configured vision model.
    
    Production-ready: Accepts base64-encoded files or HTTP multipart uploads.
    
    Args:
        file: Image input - supports:
            - Dict: {"content": "base64_string", "name": "filename.jpg"} [REST/JSON APIs]
            - UploadFile: HTTP multipart upload [Web forms, standard file uploads]
        prompt_text: Custom prompt for the vision model
    
    Returns:
        Text description of the image contents
    
    Examples:
        # REST API with base64
        describe_image(file={"content": "iVBORw0...", "name": "img.jpg"})
        
        # HTTP multipart upload
        describe_image(file=upload_file_object)
    """
    # Convert input to bytes
    if isinstance(file, dict):
        # Base64 dict (for JSON APIs, MCP over HTTP, LangChain)
        if 'content' in file:
            image_bytes = base64.b64decode(file['content'])
        elif 'base64' in file:
            image_bytes = base64.b64decode(file['base64'])
        else:
            raise ValueError("Dict must contain 'content' or 'base64' key with base64-encoded image")
    
    elif hasattr(file, 'file'):
        # UploadFile (FastAPI multipart upload)
        image_bytes = file.file.read()
        file.file.close()
    
    else:
        raise ValueError(
            f"Unsupported file type: {type(file)}. "
            "Production server only accepts: "
            "1) Dict with base64 content: {{'content': 'base64...', 'name': 'file.jpg'}} "
            "2) HTTP multipart upload (UploadFile)"
        )
    
    # Encode to base64 for vision API
    image_b64 = encode_bytes(image_bytes)
    
    # Build request body
    body = {
        "model": MODEL_NAME,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]
        }],
        "stream": False
    }
    try:
        print(f"describe_image: file_bytes={len(image_bytes)} prompt_len={len(prompt_text)}")
        start_time = time.monotonic()
        response = requests.post(API_URL, headers=HEADERS, json=body, timeout=30)
        elapsed = time.monotonic() - start_time
        print(f"describe_image: got response status={response.status_code} in {elapsed:.2f}s")
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"Failed to describe image: {str(e)}")

@mcp_tool(
    tool_description=(
         "Describe a map image with focus on area, problem, and provided information.\n\n"
        "Example usage:\n"
        '# Basic usage (uses default prompt):\n'
        'curl -X POST "http://149.165.147.219:8000/api/tool/describe_map" '
        '-F "file=@tgis_a_2343063_f0007_c.jpg"\n\n'
        '# With custom prompt_text:\n'
        'curl -X POST "http://149.165.147.219:8000/api/tool/describe_map" '
        '-F "file=@tgis_a_2343063_f0007_c.jpg" '
        '-F "prompt_text=Focus on identifying water bodies, urban areas, and transportation networks."'
    )
)
def describe_map(
    file: Union[str, bytes, UploadFile],
    prompt_text: str = (
        "Describe the given map. Focus on which area the map depicts, "
        "what problem the map describes, and what information the map provides. "
        "Format the response in markdown format."
    )
) -> str:
    """Describe a map image with focus on area, problem, and provided information.
    
    Args:
        file: The map image file to analyze (uploaded file)
        prompt_text: Custom prompt focusing on area, problem, and information (default provided)
    
    Returns:
        Markdown-formatted description of the map
    """
    if isinstance(file, str):
        with open(file, 'rb') as f:
            image_bytes = f.read()
    elif isinstance(file, bytes):
        image_bytes = file
    elif hasattr(file, 'file'):
        image_bytes = file.file.read()
        file.file.close()
    else:
        raise ValueError(f"Unsupported file type: {type(file)}")
    image_b64 = encode_bytes(image_bytes)
    body = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                ]
            }
        ],
        "stream": False
    }
    try:
        print(
            "describe_map: file_bytes=%s prompt_len=%s"
            % (len(image_bytes), len(prompt_text))
        )
        start_time = time.monotonic()
        print("describe_map: sending request...")
        response = requests.post(API_URL, headers=HEADERS, json=body)
        elapsed = time.monotonic() - start_time
        print("describe_map: got response status=%s in %.2fs" % (response.status_code, elapsed))
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"Failed to describe image: {str(e)}")
