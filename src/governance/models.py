from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class TagSchema:
    key: str
    description: str = ""
    required: bool = False
    protected: bool = False
    allowed_values: Optional[List[str]] = None
    pattern: Optional[str] = None
    type: str = "string"
    aliases: Optional[List[str]] = None
    finops: Optional[Dict[str, Any]] = None

    # Future extensibility
    data_type: str = "string"
    case_sensitive: bool = True
    owner: Optional[str] = None
    immutable: bool = False


@dataclass
class ValidationViolation:
    tag: str
    type: str  # e.g., MISSING_REQUIRED, INVALID_VALUE, INVALID_FORMAT, UNKNOWN_TAG, NORMALIZATION_CONFLICT
    expected: Optional[str] = None
    actual: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "tag": self.tag,
            "type": self.type,
        }
        if self.expected is not None:
            result["expected"] = self.expected
        if self.actual is not None:
            result["actual"] = self.actual
        return result


@dataclass
class ValidationResult:
    compliant: bool = True
    resource: Optional[str] = None
    violations: List[ValidationViolation] = field(default_factory=list)
    normalized_tags: Dict[str, str] = field(default_factory=dict)
    
    def add_violation(self, violation: ValidationViolation):
        self.compliant = False
        self.violations.append(violation)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "compliant": self.compliant,
            "resource": self.resource,
            "violations": [v.to_dict() for v in self.violations],
        }
