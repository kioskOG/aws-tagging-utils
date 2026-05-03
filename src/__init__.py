# AWS Tagging Utilities — src package
#
# Submodules:
#   config          – centralized configuration
#   logging_config  – structured logging
#   clients         – boto3 client factory with retry
#   tag_read        – read/discover resources by tags
#   tag_writer      – apply tags to resources (simple)
#   tag_write       – apply tags to resources (advanced, multi-region)
#   tag_on_create   – auto-tag new resources via EventBridge
#   tag_report      – compliance audit & reporting
#   tag_sync        – propagate tags parent → children

__version__ = "0.2.0"
