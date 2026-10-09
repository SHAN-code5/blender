"""Short guides the model can read before working in Blender (MCP resources and get_guide)."""

from importlib import resources

TOPICS = {
    "workflow": "How to approach a Blender task through these tools; units, axes, real-world sizes",
    "modeling": "Primitives, sizes, placement, and the most useful modifiers with their settings",
    "materials": "Material values for common surfaces, color formats, and PBR textures",
    "lighting": "Lighting, cameras, and rendering previews",
    "animation": "Keyframes, turntables, the timeline, and rendering video or GIFs",
    "python": "Writing execute_blender_code scripts and API differences between Blender versions",
    "assets": "Asset libraries (Poly Haven, Sketchfab, Poly Pizza) and AI model generation",
}


def load(topic: str) -> str:
    if topic not in TOPICS:
        raise KeyError(f"unknown guide {topic!r}; available: {', '.join(TOPICS)}")
    return resources.files(__name__).joinpath(f"{topic}.md").read_text(encoding="utf-8")
