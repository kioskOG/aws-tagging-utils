from src.finops.report import FinOpsReportGenerator
from src.finops.cost_explorer import CostExplorerClient
from src.governance.schema_provider import FileSchemaProvider
import os

os.environ["SQLITE_DB_PATH"] = "test.db"

ce = CostExplorerClient()
schema_provider = FileSchemaProvider("config/tag-schema.yaml")
report_gen = FinOpsReportGenerator(ce, schema_provider)

print(report_gen.generate_report())
