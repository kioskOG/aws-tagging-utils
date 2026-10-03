from typing import Dict, Optional
from src.governance.models import ValidationResult
from src.governance.schema_provider import SchemaProvider
from src.governance.normalizer import TagNormalizer
from src.governance.policy import PolicyEvaluator


class TagGovernanceEngine:
    """
    Central Tag Governance Engine.
    Coordinates schema loading, normalization, and policy evaluation.
    """
    def __init__(
        self, 
        schema_provider: SchemaProvider, 
        unknown_tags_behavior: str = "warn",
        enable_normalization: bool = True
    ):
        self.schema_provider = schema_provider
        self.unknown_tags_behavior = unknown_tags_behavior
        self.enable_normalization = enable_normalization
        
        self.schema = self.schema_provider.get_schema()
        self.normalizer = TagNormalizer(self.schema)
        self.policy_evaluator = PolicyEvaluator(self.schema, self.unknown_tags_behavior)

    def evaluate(self, tags: Dict[str, str], resource_id: Optional[str] = None) -> ValidationResult:
        result = ValidationResult(resource=resource_id)
        
        # 1. Normalize tags if enabled
        if self.enable_normalization:
            normalized_tags, norm_violations = self.normalizer.normalize(tags)
            for v in norm_violations:
                result.add_violation(v)
        else:
            normalized_tags = dict(tags)
            
        result.normalized_tags = normalized_tags
        
        # 2. Evaluate policies against normalized tags
        policy_violations = self.policy_evaluator.evaluate(normalized_tags)
        for v in policy_violations:
            result.add_violation(v)
            
        return result
