import gspread
from google.oauth2.service_account import Credentials
import difflib
import argparse
import sys
import os

# Configuration
SERVICE_ACCOUNT_FILE = 'service_account.json'
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
SHEET_NAME = "통합DB"

# Keywords to remove for fuzzy matching
REMOVE_KEYWORDS = ['기획', '증정', '세트', '번들', '용량 추가']

# Sentence to prepend
PREPEND_SENTENCE = "이번 이벤트 구성을 통해 제품의 가치를 더욱 합리적으로 경험하시는 데 효과적입니다."

def col_idx_to_letter(idx):
    """Convert 0-based column index to A1 notation letter (e.g., 0->A, 25->Z, 26->AA)."""
    if idx < 0:
        return ""

    result = ""
    while idx >= 0:
        result = chr(idx % 26 + ord('A')) + result
        idx = idx // 26 - 1
    return result

def connect_to_sheet():
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"Error: {SERVICE_ACCOUNT_FILE} not found. Please ensure it is in the root directory.")
        # For dry-run verification in test environment, we might want to skip exit if dry-run is set?
        # But we need sheet data to run logic.
        sys.exit(1)

    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        gc = gspread.authorize(creds)
        sh = gc.open(SHEET_NAME)
        worksheet = sh.get_worksheet(0)
        return worksheet
    except Exception as e:
        print(f"Error connecting to Google Sheet: {e}")
        sys.exit(1)

def find_columns(worksheet):
    # Scan first 10 rows for headers
    headers = worksheet.get('A1:Z10')

    col_map = {
        'category': -1,
        'tags': -1,
        'name': -1,
        'desc': -1
    }

    header_row_idx = -1

    for r_idx, row in enumerate(headers):
        for c_idx, cell in enumerate(row):
            cell_clean = str(cell).strip()
            if '분류' == cell_clean:
                col_map['category'] = c_idx
                header_row_idx = r_idx
            elif '분류/구성 요소' in cell_clean or '태그' in cell_clean:
                 col_map['tags'] = c_idx
            elif '제품명' in cell_clean:
                 col_map['name'] = c_idx
            elif '설명' == cell_clean or '제품 설명' in cell_clean:
                 col_map['desc'] = c_idx

        if col_map['category'] != -1 and col_map['name'] != -1:
            break

    # Fallback defaults (0-based)
    # D=3, E=4, G=6, K=10
    if col_map['category'] == -1:
        print("Warning: 'Category' column not found. Defaulting to D (index 3).")
        col_map['category'] = 3
    if col_map['tags'] == -1:
        print("Warning: 'Tags' column not found. Defaulting to E (index 4).")
        col_map['tags'] = 4
    if col_map['name'] == -1:
        print("Warning: 'Name' column not found. Defaulting to G (index 6).")
        col_map['name'] = 6
    if col_map['desc'] == -1:
        print("Warning: 'Description' column not found. Defaulting to K (index 10).")
        col_map['desc'] = 10

    print(f"Column Mapping: {col_map} (Header Row: {header_row_idx+1})")
    return col_map, header_row_idx

def clean_name(name):
    original = name
    for kw in REMOVE_KEYWORDS:
        name = name.replace(kw, '')
    cleaned = name.strip()
    cleaned = ' '.join(cleaned.split())
    return cleaned

def format_description(new_desc_base):
    if not new_desc_base:
        return ""

    paragraphs = [p.strip() for p in new_desc_base.split('\n') if p.strip()]

    if not paragraphs:
        paragraphs = [""]

    # Prepend sentence to first paragraph
    first_para = paragraphs[0]
    if PREPEND_SENTENCE not in first_para:
        paragraphs[0] = f"{PREPEND_SENTENCE} {first_para}"

    return '\n\n'.join(paragraphs)

def main():
    parser = argparse.ArgumentParser(description='Sync Event Category Data')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without writing to sheet')
    args = parser.parse_args()

    worksheet = connect_to_sheet()
    col_map, header_row_idx = find_columns(worksheet)

    print("Loading data from sheet...")
    all_values = worksheet.get_all_values()

    data_start_row = header_row_idx + 1 if header_row_idx != -1 else 6 # Default to row 7 (index 6) if header not found

    rows = all_values[data_start_row:]

    reference_data = {}
    reference_names = []
    event_targets = []

    print("Analyzing rows...")
    for i, row in enumerate(rows):
        def get_col(idx):
            return row[idx] if idx < len(row) else ""

        category = get_col(col_map['category'])
        name = get_col(col_map['name'])
        tags = get_col(col_map['tags'])
        desc = get_col(col_map['desc'])

        real_row_idx = data_start_row + i + 1

        if not name:
            continue

        if category == '이벤트':
            if not tags or not desc:
                event_targets.append({
                    'row_idx': real_row_idx,
                    'name': name,
                    'tags': tags,
                    'desc': desc
                })
        else:
            reference_data[name] = {'tags': tags, 'desc': desc}
            reference_names.append(name)

    print(f"Found {len(event_targets)} event targets to sync.")
    print(f"Found {len(reference_names)} reference products.")

    updates = []
    synced_count = 0

    for target in event_targets:
        t_name = target['name']
        match_found = None
        match_type = ""
        is_exact = False

        # 1. Exact Match
        if t_name in reference_data:
            match_found = reference_data[t_name]
            match_type = "Exact"
            is_exact = True
        else:
            # 2. Fuzzy Match
            t_name_clean = clean_name(t_name)
            matches = difflib.get_close_matches(t_name_clean, reference_names, n=1, cutoff=0.6)
            if matches:
                best_match_name = matches[0]
                match_found = reference_data[best_match_name]
                match_type = f"Fuzzy ({best_match_name})"

        if match_found:
            new_tags = match_found['tags']
            new_desc_base = match_found['desc']

            row_updates = []

            tag_col_letter = col_idx_to_letter(col_map['tags'])
            desc_col_letter = col_idx_to_letter(col_map['desc'])

            if not target['tags'] and new_tags:
                row_updates.append({
                    'range': f"{tag_col_letter}{target['row_idx']}",
                    'values': [[new_tags]]
                })

            if not target['desc'] and new_desc_base:
                if is_exact:
                    # For Exact Match: Copy verbatim
                    final_desc = new_desc_base
                else:
                    # For Fuzzy Match: Add the sentence
                    final_desc = format_description(new_desc_base)

                row_updates.append({
                    'range': f"{desc_col_letter}{target['row_idx']}",
                    'values': [[final_desc]]
                })

            if row_updates:
                print(f"[{match_type}] Syncing '{t_name}' (Row {target['row_idx']})")
                updates.extend(row_updates)
                synced_count += 1

    print(f"Total updates prepared: {len(updates)}")

    if args.dry_run:
        print("Dry Run: No changes made.")
        for u in updates:
            print(f"  Update {u['range']}: {u['values'][0][0][:50]}...")
    else:
        if updates:
            print("Applying updates...")
            try:
                worksheet.batch_update(updates)
                print(f"Successfully synced {synced_count} rows.")
            except Exception as e:
                print(f"Error applying updates: {e}")
        else:
            print("No updates to apply.")

if __name__ == "__main__":
    main()
