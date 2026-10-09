from typing import Dict, List, Tuple
from src.governance.models import TagSchema, ValidationViolation


class TagNormalizer:
    def __init__(self, schema: Dict[str, TagSchema]):
        self.schema = schema
        self._alias_map: Dict[str, str] = {}
        for canonical_key, rules in schema.items():
            if rules.aliases:
                for alias in rules.aliases:
                    # Mapping normalized lower string to canonical key to catch variations
                    self._alias_map[alias.lower()] = canonical_key

    def normalize(self, tags: Dict[str, str]) -> Tuple[Dict[str, str], List[ValidationViolation]]:
        """
        Normalizes tags keys. Returns the normalized tags dictionary and a list of violations (conflicts).
        """
        normalized_tags: Dict[str, str] = {}
        violations: List[ValidationViolation] = []
        
        # Track which original keys map to which canonical key to detect conflicts
        canonical_to_original: Dict[str, List[str]] = {}

        for original_key, value in tags.items():
            canonical_key = self._get_canonical_key(original_key)
            canonical_to_original.setdefault(canonical_key, []).append(original_key)
            
            if canonical_key not in normalized_tags:
                normalized_tags[canonical_key] = value

        # Detect conflicts
        for canonical_key, original_keys in canonical_to_original.items():
            if len(original_keys) > 1:
                # We have a conflict
                violations.append(ValidationViolation(
                    tag=canonical_key,
                    type="NORMALIZATION_CONFLICT",
                    expected=f"Only one tag mapping to {canonical_key}",
                    actual=f"Conflicting keys provided: {', '.join(original_keys)}"
                ))
                # For safety, we shouldn't arbitrarily pick one if values differ
                # But since we already picked the first one in the loop above, we'll let it be, 
                # the violation will fail the strict mode anyway.

        return normalized_tags, violations

    def _get_canonical_key(self, original_key: str) -> str:
        if original_key in self.schema:
            return original_key
            
        lower_key = original_key.lower()
        if lower_key in self._alias_map:
            return self._alias_map[lower_key]
            
        # If it doesn't match an alias, just return the original key
        # Case insensitive exact match fallback (if alias list forgot some case variants)
        for schema_key in self.schema:
            if schema_key.lower() == lower_key:
                return schema_key
                
        return original_key
