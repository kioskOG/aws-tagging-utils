from typing import Dict, List
from src.governance.models import TagSchema, ValidationViolation
from src.governance.validators import Validator


class PolicyEvaluator:
    def __init__(self, schema: Dict[str, TagSchema], unknown_tags_behavior: str = "warn"):
        self.schema = schema
        self.unknown_tags_behavior = unknown_tags_behavior

    def evaluate(self, tags: Dict[str, str]) -> List[ValidationViolation]:
        violations: List[ValidationViolation] = []
        
        # 1. Required tags
        violations.extend(Validator.validate_required(tags, self.schema))
        
        # 2. Allowed values (Enums)
        violations.extend(Validator.validate_allowed_values(tags, self.schema))
        
        # 3. Pattern / Regex
        violations.extend(Validator.validate_pattern(tags, self.schema))
        
        # 4. Unknown tags
        violations.extend(Validator.validate_unknown(tags, self.schema, self.unknown_tags_behavior))
        
        return violations
