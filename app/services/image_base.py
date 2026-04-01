from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class ImageService(ABC):
    """Base class for Image Generation services"""

    def __init__(self, api_key: str, model: str, base_url: str = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        style: str = "natural",
    ) -> Dict[str, Any]:
        """
        Generate an image from a text prompt.

        Args:
            prompt: Text description of the image to generate
            size: Image size (e.g., '1024x1024', '1080x1350')
            style: Image style ('natural', 'vivid', 'artistic')

        Returns:
            Dict with 'data' (base64), 'media_type', 'provider', 'model', 'size'
        """
        pass
