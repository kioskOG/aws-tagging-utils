import os
import yaml
import json
from abc import ABC, abstractmethod
from typing import Dict, Any

from src.governance.models import TagSchema
from src.logging_config import get_logger

logger = get_logger(__name__)


class SchemaProvider(ABC):
    @abstractmethod
    def get_schema(self) -> Dict[str, TagSchema]:
        """Returns a dictionary mapping canonical tag keys to their TagSchema."""
        pass


class FileSchemaProvider(SchemaProvider):
    def __init__(self, file_path: str):
        self.file_path = file_path
        self._schema_cache: Dict[str, TagSchema] = {}
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
            
        if not os.path.exists(self.file_path):
            logger.error("Schema file not found: %s", self.file_path)
            self._schema_cache = {}
            self._loaded = True
            return

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                if self.file_path.endswith(".yaml") or self.file_path.endswith(".yml"):
                    data = yaml.safe_load(f)
                else:
                    data = json.load(f)
                    
            tags_config = data.get("tags", {})
            normalization_config = data.get("normalization", {})

            for tag_key, config in tags_config.items():
                aliases = normalization_config.get(tag_key, {}).get("aliases", [])
                schema = TagSchema(
                    key=tag_key,
                    description=config.get("description", ""),
                    required=config.get("required", False),
                    protected=config.get("protected", False),
                    allowed_values=config.get("allowed_values"),
                    pattern=config.get("pattern"),
                    type=config.get("type", "string"),
                    finops=config.get("finops"),
                    aliases=aliases
                )
                self._schema_cache[tag_key] = schema
                
            self._loaded = True
            logger.info("Loaded tag schema from %s with %d tags defined.", self.file_path, len(self._schema_cache))
        except Exception as e:
            logger.error("Failed to load schema from %s: %s", self.file_path, e)
            self._schema_cache = {}
            self._loaded = True

    def get_schema(self) -> Dict[str, TagSchema]:
        self._load()
        return self._schema_cache
