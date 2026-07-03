from rag_pipeline.search.opengeodata_new import run_opengeodata
import json
from datetime import datetime


bbox = [
    -87.9400876,
    41.644531,
    -87.5241243,
    42.0230529
  ]

# result = run_opengeodata(
#   query="Give me datasets pertaining to the risk of aging dams in the U.S.",
#   bbox=None,
#   call_llm=1,
#   limit=10,
#   providers={"datagov": True, "socrata": True, "cmr": True}
# )

result = run_opengeodata(
  query="dams risk",
  bbox=None,
  call_llm=0,
  limit=10,
  providers={"datagov": True, "socrata": False, "cmr": False}
)

print(json.dumps(result, indent=2))