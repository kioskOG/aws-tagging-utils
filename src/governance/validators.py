import re
from typing import Dict, List, Optional
from src.governance.models import TagSchema, ValidationViolation


class Validator:
    @staticmethod
    def validate_required(tags: Dict[str, str], schema: Dict[str, TagSchema]) -> List[ValidationViolation]:
        violations = []
        for key, rules in schema.items():
            if rules.required:
                val = tags.get(key)
                if val is None or str(val).strip() == "":
                    violations.append(ValidationViolation(
                        tag=key,
                        type="MISSING_REQUIRED",
                        expected="Value is mandatory",
                        actual="Missing or empty"
                    ))
        return violations

    @staticmethod
    def validate_allowed_values(tags: Dict[str, str], schema: Dict[str, TagSchema]) -> List[ValidationViolation]:
        violations = []
        for key, val in tags.items():
            if not str(val).strip():
                continue # Handled by required validator if needed
            if key in schema and schema[key].allowed_values:
                allowed = schema[key].allowed_values
                # Enforce case sensitivity based on schema (default True)
                # If we need case insensitivity, we'd check rules.case_sensitive
                if val not in allowed:
                    violations.append(ValidationViolation(
                        tag=key,
                        type="INVALID_VALUE",
                        expected=f"One of: {', '.join(allowed)}",
                        actual=val
                    ))
        return violations

    @staticmethod
    def validate_pattern(tags: Dict[str, str], schema: Dict[str, TagSchema]) -> List[ValidationViolation]:
        violations = []
        for key, val in tags.items():
            if not str(val).strip():
                continue
            if key in schema and schema[key].pattern:
                pattern = schema[key].pattern
                try:
                    if not re.match(pattern, val):
                        violations.append(ValidationViolation(
                            tag=key,
                            type="INVALID_FORMAT",
                            expected=f"Must match regex: {pattern}",
                            actual=val
                        ))
                except re.error:
                    # Invalid regex in schema, assume validation fails
                    violations.append(ValidationViolation(
                        tag=key,
                        type="INVALID_FORMAT",
                        expected=f"Invalid schema regex: {pattern}",
                        actual=val
                    ))
        return violations

    @staticmethod
    def validate_unknown(tags: Dict[str, str], schema: Dict[str, TagSchema], behavior: str) -> List[ValidationViolation]:
        violations = []
        if behavior.lower() in ("allow", "ignore"):
            return violations

        for key in tags:
            # aws:* tags are reserved and managed by AWS (e.g. aws:cloudformation:stack-name)
            if key.lower().startswith("aws:"):
                continue
            if key not in schema:
                violations.append(ValidationViolation(
                    tag=key,
                    type="UNKNOWN_TAG",
                    expected="Tag defined in schema",
                    actual="Not defined"
                ))
        return violations
