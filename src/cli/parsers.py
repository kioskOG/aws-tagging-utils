import re
import os
import yaml
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


class IacParser(ABC):
    @abstractmethod
    def parse_tags(self, file_path: str) -> Dict[str, Dict[str, str]]:
        """
        Parses a file and returns a mapping of resource identifier to its tags.
        Example: {"module.ec2": {"Owner": "devops"}}
        """
        pass

class CloudFormationParser(IacParser):
    def parse_tags(self, file_path: str) -> Dict[str, Dict[str, str]]:
        result = {}
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                # Use yaml.safe_load for both YAML and JSON
                data = yaml.safe_load(f)
                
            if not isinstance(data, dict):
                return result
                
            resources = data.get("Resources", {})
            for logical_id, resource in resources.items():
                if not isinstance(resource, dict):
                    continue
                
                props = resource.get("Properties", {})
                if not isinstance(props, dict):
                    continue
                
                # CFN tags are usually a list of Key/Value dicts
                tags_prop = props.get("Tags")
                tags = {}
                if isinstance(tags_prop, list):
                    for tag_obj in tags_prop:
                        if isinstance(tag_obj, dict) and "Key" in tag_obj and "Value" in tag_obj:
                            tags[tag_obj["Key"]] = str(tag_obj["Value"])
                elif isinstance(tags_prop, dict):
                    # Sometimes tags are just a map in certain pseudo-resources or older specs
                    for k, v in tags_prop.items():
                        tags[k] = str(v)
                        
                if tags:
                    result[f"cfn/{logical_id}"] = tags
                    
        except Exception:
            # Ignore parse errors for simple validation scanning
            pass
            
        return result

# Tag values that are expressions (var.x, "${...}", lookups) exist but are only known at plan
# time; the validator checks their presence, not their value.
UNKNOWN_VALUE = "<computed>"


def _match_brace(text: str, open_idx: int) -> int:
    """Index of the brace closing text[open_idx] ('{'), skipping strings and comments; -1 if none."""
    depth, i, n = 0, open_idx, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "#" or text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_entries(body: str) -> List[str]:
    """Split a map literal body on newlines/commas at nesting depth 0."""
    entries, buf, depth, in_str, i = [], [], 0, False, 0
    while i < len(body):
        c = body[i]
        if in_str:
            buf.append(c)
            if c == "\\" and i + 1 < len(body):
                buf.append(body[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            buf.append(c)
        elif c in "{[(":
            depth += 1
            buf.append(c)
        elif c in "}])":
            depth -= 1
            buf.append(c)
        elif c == "#" and depth == 0:
            while i < len(body) and body[i] != "\n":
                i += 1
            continue
        elif c in "\n," and depth == 0:
            entries.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    entries.append("".join(buf))
    return [e.strip() for e in entries if e.strip()]


def _literal(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        inner = value[1:-1]
        return UNKNOWN_VALUE if "${" in inner else inner
    if re.fullmatch(r"-?\d+(\.\d+)?|true|false", value):
        return value
    return UNKNOWN_VALUE


class TerraformParser(IacParser):
    """
    Static .tf parsing for `resource` blocks.

    - A literal `tags = { ... }` map is validated. Values that are expressions count as present
      with an unknown value (UNKNOWN_VALUE).
    - Tags built by functions or variables (`merge(...)`, `var.tags`) can't be resolved from
      source; those resources are reported as unresolved, never as compliant.
    - Resources without a `tags` argument are skipped: not every resource type is taggable, and
      provider `default_tags` may cover them. Validate a plan (terraform show -json) for that.
    """

    _RESOURCE = re.compile(r'^\s*resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', re.MULTILINE)
    _TAGS = re.compile(r'^\s*tags\s*=\s*', re.MULTILINE)

    def parse(self, file_path: str) -> Tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
        """(tags per resource, {resource: reason} for tags that can't be resolved statically)."""
        tags_by_resource: Dict[str, Dict[str, str]] = {}
        unresolved: Dict[str, str] = {}
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError):
            return tags_by_resource, unresolved

        for m in self._RESOURCE.finditer(content):
            end = _match_brace(content, m.end() - 1)
            if end < 0:
                continue
            body = content[m.end():end]
            rid = f"tf/{m.group(1)}.{m.group(2)}"
            # Only top-level arguments: blank out nested blocks first
            top = body
            for nested in re.finditer(r'^\s*[A-Za-z_][\w-]*\s*\{', body, re.MULTILINE):
                close = _match_brace(body, nested.end() - 1)
                if close > 0:
                    top = top[:nested.start()] + " " * (close + 1 - nested.start()) + top[close + 1:]
            tm = self._TAGS.search(top)
            if not tm:
                continue
            rest = body[tm.end():]
            if not rest.startswith("{"):
                expr = rest.split("\n", 1)[0].strip()
                unresolved[rid] = f"tags = {expr[:60]}{'...' if len(expr) > 60 else ''} can't be resolved statically"
                continue
            close = _match_brace(rest, 0)
            if close < 0:
                continue
            tags: Dict[str, str] = {}
            for entry in _split_entries(rest[1:close]):
                km = re.match(r'("([^"]+)"|[A-Za-z0-9_.:/-]+)\s*[=:]\s*(.+)$', entry, re.DOTALL)
                if km:
                    tags[km.group(2) or km.group(1)] = _literal(km.group(3))
            tags_by_resource[rid] = tags
        return tags_by_resource, unresolved

    def parse_tags(self, file_path: str) -> Dict[str, Dict[str, str]]:
        return self.parse(file_path)[0]


class TerraformPlanParser(IacParser):
    """
    `terraform show -json <planfile>` output. Uses `tags_all`, which includes provider
    default_tags, so this is the accurate check for Terraform and Terragrunt stacks.
    Every taggable resource (one with a tags/tags_all attribute) is evaluated, including
    resources with no tags at all.
    """

    @staticmethod
    def is_plan(data: Any) -> bool:
        return isinstance(data, dict) and "resource_changes" in data

    def parse(self, file_path: str) -> Tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
        import json
        with open(file_path, "r", encoding="utf-8") as f:
            return self.parse_data(json.load(f))

    def parse_data(self, data: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
        tags_by_resource: Dict[str, Dict[str, str]] = {}
        unresolved: Dict[str, str] = {}
        for rc in data.get("resource_changes") or []:
            change = rc.get("change") or {}
            if rc.get("mode") == "data" or change.get("actions") == ["delete"]:
                continue
            after = change.get("after") or {}
            if not isinstance(after, dict) or ("tags_all" not in after and "tags" not in after):
                continue
            rid = f"plan/{rc.get('address')}"
            unknown = (change.get("after_unknown") or {}).get("tags_all")
            if unknown is True:
                unresolved[rid] = "tags_all is only known after apply"
                continue
            tags = {k: str(v) for k, v in (after.get("tags_all") or after.get("tags") or {}).items()}
            if isinstance(unknown, dict):
                tags.update({k: UNKNOWN_VALUE for k, v in unknown.items() if v is True})
            tags_by_resource[rid] = tags
        return tags_by_resource, unresolved

    def parse_tags(self, file_path: str) -> Dict[str, Dict[str, str]]:
        return self.parse(file_path)[0]


_REMOTE_SOURCE = re.compile(r"^(git::|git@|github\.com|bitbucket\.org|tfr://|https?://|s3::|gcs::|hg::)")


def terragrunt_module_dir(terragrunt_file: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Local module directory referenced by terragrunt.hcl's `terraform { source = ... }`.
    Returns (path, None), or (None, reason) when it isn't a local, resolvable path.
    """
    try:
        with open(terragrunt_file, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return None, str(e)
    m = re.search(r'^\s*terraform\s*\{', content, re.MULTILINE)
    if not m:
        return None, "no terraform block (source may come from an included config)"
    block = content[m.end():_match_brace(content, m.end() - 1)]
    sm = re.search(r'^\s*source\s*=\s*"([^"]+)"', block, re.MULTILINE)
    if not sm:
        return None, "terraform block has no literal source"
    source = sm.group(1)
    if _REMOTE_SOURCE.match(source) or "?ref=" in source:
        return None, f"remote module source {source}"
    here = os.path.dirname(os.path.abspath(terragrunt_file))
    source = source.replace("${get_terragrunt_dir()}", here)
    if "${" in source:
        return None, f"source {source} uses functions that can't be resolved statically"
    base, _, sub = source.partition("//")
    path = os.path.normpath(os.path.join(here, base, sub))
    if not os.path.isdir(path):
        return None, f"module directory {path} not found"
    return path, None
