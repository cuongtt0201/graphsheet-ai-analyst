import pandas as pd

def compress_schema_babeltele(profiles: list[dict]) -> str:
    """Compress Excel data schema/profiles using BabelTele compact syntax to save prompt tokens.
    
    BabelTele Key:
      T:{name} - Table name
      R:{count} - Row count
      # - Numeric measure
      $ - Category (dimension)
      @ - Datetime column
      id - Identifier column
      txt - Free text column
      ∅:{pct}% - Null percentage
      D:{count}[{samples}] - Distinct count and sample values
      Σ:{val} - Sum
      μ:{val} - Mean
    """
    out = []
    for p in profiles:
        sid = p["source_id"]
        out.append(f"T:{sid}(R:{p['row_count']})")
        for cp in p.get("column_profiles", []):
            name = cp['name']
            dtype = cp['dtype']
            role = cp.get('role', '?')
            
            # Map role to mathematical/compact symbols
            role_sym = "#" if role == "measure" else "$" if role == "category" else "@" if role == "date" else "id" if role == "id" else "txt"
            
            bits = [f"{name}({dtype},{role_sym})"]
            
            null_pct = cp.get("null_pct")
            if null_pct and null_pct > 0:
                bits.append(f"∅:{int(null_pct*100)}%")
                
            if "sum" in cp:
                # Numeric stats compressed using compact notation
                bits.append(f"min:{cp.get('min')},max:{cp.get('max')},μ:{cp.get('mean')},Σ:{cp.get('sum')}")
            elif cp.get("sample"):
                vals = ",".join(str(s) for s in cp["sample"][:3])
                bits.append(f"D:{cp.get('distinct', '?')}[{vals}]")
                
            out.append(" •" + "|".join(bits))
    return "\n".join(out)
