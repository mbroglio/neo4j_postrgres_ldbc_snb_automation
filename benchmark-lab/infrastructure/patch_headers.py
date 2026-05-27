import os, glob, subprocess

raw_dir = os.environ.get('RAW_DATA_DIR', './out-sf0.1/graphs/csv/raw/composite-projected-fk')
header_dir = os.path.join(os.path.dirname(raw_dir), 'headers')
os.makedirs(header_dir, exist_ok=True)

def process_entity(entity_type, is_edge):
    files = glob.glob(os.path.join(raw_dir, '**', entity_type, '*.csv'), recursive=True)
    if not files:
        return
    
    # Read header from the first file
    with open(files[0], 'r') as f:
        first_line = f.readline().strip()
        header = first_line.split('|')
    
    # Check if this file has already been processed (no letters in first fields of first line)
    # Actually, the most reliable way is to check if the first line is data
    is_header = any(not part.strip().replace('.','').replace('-','').replace(':','').replace('_','').replace('(','').replace(')','').isdigit() for part in header)
    
    if not is_header:
        print(f"Skipping {entity_type} (already processed)")
        return

    if is_edge:
        parts = entity_type.split('_')
        source_group = parts[0]
        target_group = parts[-1]
        mapping = {'City': 'Place', 'Country': 'Place', 'University': 'Organisation', 'Company': 'Organisation'}
        source_group = mapping.get(source_group, source_group)
        target_group = mapping.get(target_group, target_group)
        
        new_header = []
        start_found = False
        for h in header:
            if h.endswith('Id') or h.startswith(':START_ID') or h.startswith(':END_ID'):
                if not start_found:
                    new_header.append(f':START_ID({source_group})')
                    start_found = True
                else:
                    new_header.append(f':END_ID({target_group})')
            else:
                new_header.append(h)
        header_str = "|".join(new_header)
        for f in files:
            subprocess.run(['sed', '-i', '1d', f])
    else:
        id_idx = -1
        for i, h in enumerate(header):
            if h.lower() == 'id' or h.startswith(':ID'):
                id_idx = i
                header[i] = 'id:long'
                break
        if id_idx == -1: return
        new_header = [f':ID({entity_type})'] + header
        header_str = "|".join(new_header)
        for f in files:
            cmd = f"awk -F'|' 'BEGIN {{OFS=\"|\"}} NR==1 {{next}} {{print ${id_idx+1}, $0}}' {f} > {f}.tmp && mv {f}.tmp {f}"
            subprocess.run(cmd, shell=True)

    header_file = os.path.join(header_dir, f"{entity_type}-header.csv")
    with open(header_file, 'w') as f:
        f.write(header_str + '\n')

entities = []
edges = []
for sub in ['dynamic', 'static']:
    sub_dir = os.path.join(raw_dir, sub)
    if not os.path.exists(sub_dir): continue
    for name in os.listdir(sub_dir):
        if os.path.isdir(os.path.join(sub_dir, name)):
            if '_' in name: edges.append((name, True))
            else: entities.append((name, False))

for entity, is_edge in entities + edges:
    print(f"Processing {entity}...")
    process_entity(entity, is_edge)
