import frappe
import time
import re
from functools import lru_cache
from typing import List, Dict, Any, Optional, Union

# ================================
# ORIGINAL FUNCTIONS FOR FRAPPE/ERP (Returns Tuples)
# ================================

@frappe.whitelist()
def custom_item_search(doctype, txt, searchfield, start, page_len, filters):
    """
    Enhanced search function for Item lookup with priority-based search (FRAPPE/ERP VERSION)
    Returns: List of tuples for Frappe compatibility
    
    Priority order:
    1. Exact custom_sku_code match (highest priority)
    2. Exact item_code match
    3. Exact item_name match
    4. Word boundary match in custom_sku_code (e.g. "1234" in SKU "abc-1234")
    5. Word boundary match in item_name (e.g. "1234" in "abc 1234" ranks above "xyz1234")
    6. Word boundary match in item_code
    7. Space-insensitive item_name match (e.g., "rohanrambhiya" matches "rohan ram bhiya")
    8. Space-insensitive item_code match
    9. Space-insensitive custom_sku_code match
    10. Partial item_code match
    11. Partial item_name match
    12. Partial custom_sku_code match (lowest priority)

    This version maintains performance for large datasets (23k+ items)
    """
    # Clean the search text
    search_text = txt.strip() if txt and txt.strip() else ""
    
    # If no search terms, return standard query
    if not search_text:
        return frappe.db.sql("""
            SELECT DISTINCT item.name, item.item_name, COALESCE(item.custom_sku_code, '') as custom_sku_code
            FROM `tabItem` item
            WHERE item.disabled = 0
            ORDER BY item.item_name
            LIMIT %s, %s
        """, (start, page_len))

    # Split search text into terms for partial matching
    terms = search_text.split()
    
    # Space-normalized search term for space-insensitive matching
    search_no_spaces = search_text.replace(' ', '').replace('-', '').replace('_', '').lower()
    
    # Construct conditions for partial item_name matching (all terms must match)
    name_conditions = []
    name_params = []
    
    for term in terms:
        name_conditions.append("item.item_name LIKE %s")
        name_params.append(f'%{term}%')
    
    name_where_clause = " AND ".join(name_conditions) if name_conditions else "1=1"
    
    # Construct conditions for partial item_code matching (all terms must match)
    code_conditions = []
    code_params = []
    
    for term in terms:
        code_conditions.append("item.item_code LIKE %s")
        code_params.append(f'%{term}%')
    
    code_where_clause = " AND ".join(code_conditions) if code_conditions else "1=1"
    
    # Construct conditions for partial custom_sku_code matching (all terms must match)
    sku_conditions = []
    sku_params = []
    
    for term in terms:
        sku_conditions.append("item.custom_sku_code LIKE %s")
        sku_params.append(f'%{term}%')
    
    sku_where_clause = " AND ".join(sku_conditions) if sku_conditions else "1=1"
    
    # Word boundary conditions for custom_sku_code
    word_boundary_conditions = []
    word_boundary_params = []

    for term in terms:
        word_boundary_conditions.append("item.custom_sku_code REGEXP %s")
        word_boundary_params.append(f'(^|[^a-zA-Z0-9]){re.escape(term)}([^a-zA-Z0-9]|$)')

    word_boundary_where_clause = " AND ".join(word_boundary_conditions) if word_boundary_conditions else "1=1"

    # Word boundary conditions for item_name (catches "1234" in "abc 1234" before "xyz1234")
    name_word_boundary_conditions = []
    name_word_boundary_params = []

    for term in terms:
        name_word_boundary_conditions.append("item.item_name REGEXP %s")
        name_word_boundary_params.append(f'(^|[^a-zA-Z0-9]){re.escape(term)}([^a-zA-Z0-9]|$)')

    name_word_boundary_where_clause = " AND ".join(name_word_boundary_conditions) if name_word_boundary_conditions else "1=1"

    # Word boundary conditions for item_code
    code_word_boundary_conditions = []
    code_word_boundary_params = []

    for term in terms:
        code_word_boundary_conditions.append("item.item_code REGEXP %s")
        code_word_boundary_params.append(f'(^|[^a-zA-Z0-9]){re.escape(term)}([^a-zA-Z0-9]|$)')

    code_word_boundary_where_clause = " AND ".join(code_word_boundary_conditions) if code_word_boundary_conditions else "1=1"

    # Enhanced query with space-insensitive matching
    query = f"""
        SELECT DISTINCT item.name, item.item_name, COALESCE(item.custom_sku_code, '') as custom_sku_code
        FROM `tabItem` item
        WHERE item.disabled = 0
        AND (
            item.custom_sku_code = %s              -- 1. Exact custom_sku_code match
            OR item.item_code = %s                 -- 2. Exact item_code match
            OR item.item_name = %s                 -- 3. Exact item_name match
            OR (item.custom_sku_code IS NOT NULL AND {word_boundary_where_clause})  -- 4. Word boundary match in SKU
            OR ({name_word_boundary_where_clause}) -- 5. Word boundary match in item_name
            OR ({code_word_boundary_where_clause}) -- 6. Word boundary match in item_code
            OR (LOWER(REPLACE(REPLACE(REPLACE(item.item_name, ' ', ''), '-', ''), '_', '')) LIKE %s)  -- 7. Space-insensitive item_name
            OR (LOWER(REPLACE(REPLACE(REPLACE(item.item_code, ' ', ''), '-', ''), '_', '')) LIKE %s)  -- 8. Space-insensitive item_code
            OR (item.custom_sku_code IS NOT NULL AND LOWER(REPLACE(REPLACE(REPLACE(item.custom_sku_code, ' ', ''), '-', ''), '_', '')) LIKE %s)  -- 9. Space-insensitive SKU
            OR ({code_where_clause})               -- 10. Partial item_code match
            OR ({name_where_clause})               -- 11. Partial item_name match
            OR (item.custom_sku_code IS NOT NULL AND {sku_where_clause})  -- 12. Partial custom_sku_code match
        )
        ORDER BY
            CASE
                WHEN item.custom_sku_code = %s THEN 1      -- 1. Exact custom_sku_code (highest priority)
                WHEN item.item_code = %s THEN 2            -- 2. Exact item_code
                WHEN item.item_name = %s THEN 3            -- 3. Exact item_name
                WHEN item.custom_sku_code IS NOT NULL AND ({word_boundary_where_clause}) THEN 4  -- 4. Word boundary in SKU
                WHEN ({name_word_boundary_where_clause}) THEN 5  -- 5. Word boundary in item_name
                WHEN ({code_word_boundary_where_clause}) THEN 6  -- 6. Word boundary in item_code
                WHEN LOWER(REPLACE(REPLACE(REPLACE(item.item_name, ' ', ''), '-', ''), '_', '')) LIKE %s THEN 7  -- 7. Space-insensitive item_name
                WHEN LOWER(REPLACE(REPLACE(REPLACE(item.item_code, ' ', ''), '-', ''), '_', '')) LIKE %s THEN 8  -- 8. Space-insensitive item_code
                WHEN item.custom_sku_code IS NOT NULL AND LOWER(REPLACE(REPLACE(REPLACE(item.custom_sku_code, ' ', ''), '-', ''), '_', '')) LIKE %s THEN 9  -- 9. Space-insensitive SKU
                WHEN ({code_where_clause}) THEN 10         -- 10. Partial item_code
                WHEN ({name_where_clause}) THEN 11         -- 11. Partial item_name
                WHEN item.custom_sku_code IS NOT NULL AND ({sku_where_clause}) THEN 12  -- 12. Partial custom_sku_code
                ELSE 13
            END,
            LENGTH(COALESCE(item.item_name, '')),          -- Shorter names first within same priority
            item.item_name                                 -- Alphabetical within same length
        LIMIT %s, %s
    """

    # Prepare parameters in the correct order:
    query_params = (
        [search_text, search_text, search_text] +  # WHERE clause exact matches
        word_boundary_params +                     # WHERE SKU word boundary
        name_word_boundary_params +                # WHERE item_name word boundary
        code_word_boundary_params +                # WHERE item_code word boundary
        [f'%{search_no_spaces}%', f'%{search_no_spaces}%', f'%{search_no_spaces}%'] +  # WHERE space-insensitive matching
        code_params +                              # WHERE partial code matching
        name_params +                              # WHERE partial name matching
        sku_params +                               # WHERE partial SKU matching
        [search_text, search_text, search_text] +  # ORDER BY exact matches
        word_boundary_params +                     # ORDER BY SKU word boundary
        name_word_boundary_params +                # ORDER BY item_name word boundary
        code_word_boundary_params +                # ORDER BY item_code word boundary
        [f'%{search_no_spaces}%', f'%{search_no_spaces}%', f'%{search_no_spaces}%'] +  # ORDER BY space-insensitive matching
        code_params +                              # ORDER BY partial code matching
        name_params +                              # ORDER BY partial name matching
        sku_params +                               # ORDER BY partial SKU matching
        [start, page_len]                          # Pagination
    )
    
    results = frappe.db.sql(query, query_params)
    
    return results

@frappe.whitelist()
def debounced_item_search(doctype, txt, searchfield, start, page_len, filters):
    """
    Debounced version of custom_item_search - optimized for performance (FRAPPE/ERP VERSION)
    Returns: List of tuples for Frappe compatibility
    """
    # Call the main search function directly without logging
    return custom_item_search(doctype, txt, searchfield, start, page_len, filters)

# ================================
# NEW FUNCTIONS FOR BACKEND/CUSTOM APP (Returns Dictionaries)
# ================================

# Cache for field validation to avoid repeated lookups
@lru_cache(maxsize=1)
def get_valid_item_fields():
    """Get valid Item doctype fields from database metadata"""
    try:
        item_meta = frappe.get_meta("Item")
        return {field.fieldname for field in item_meta.fields} | {'name', 'creation', 'modified', 'owner', 'modified_by'}
    except Exception:
        # Fallback to hardcoded list if meta is not available
        return {
            'name', 'item_name', 'item_code', 'custom_sku_code', 'brand', 
            'standard_rate', 'image', 'max_discount', 'stock_uom', 'description',
            'item_group', 'disabled', 'has_variants', 'variant_of', 'creation',
            'modified', 'owner', 'modified_by'
        }

def parse_fields(fields: Union[str, List[str], None]) -> List[str]:
    """Parse and validate field list with proper error handling"""
    if not fields:
        return ['name', 'item_code', 'item_name', 'custom_sku_code', 'brand', 'standard_rate', 'image', 'max_discount', 'stock_uom']
    
    if isinstance(fields, str):
        try:
            # Try JSON parsing first
            import json
            field_list = json.loads(fields)
        except (json.JSONDecodeError, ValueError):
            # Fallback to comma-separated parsing
            field_list = [f.strip() for f in fields.split(',') if f.strip()]
    else:
        field_list = list(fields) if fields else []
    
    # Validate fields against Item doctype
    valid_fields = get_valid_item_fields()
    validated_fields = []
    
    for field in field_list:
        if field in valid_fields:
            validated_fields.append(field)
    
    # Ensure essential fields are included
    essential_fields = ['name', 'item_code', 'item_name', 'custom_sku_code', 'brand', 'standard_rate', 'image', 'max_discount', 'stock_uom']
    for field in essential_fields:
        if field not in validated_fields:
            validated_fields.append(field)
    
    return validated_fields

def build_select_clause(field_list: List[str]) -> str:
    """Build SELECT clause with proper field mapping for dictionary return"""
    select_fields = []
    
    # Essential field mapping for clean output
    essential_mapping = {
        'name': 'item.name as name',
        'item_code': 'item.item_code as item_code', 
        'item_name': 'item.item_name as item_name',
        'custom_sku_code': "COALESCE(item.custom_sku_code, '') as custom_sku_code",
        'brand': 'item.brand as brand',
        'standard_rate': 'item.standard_rate as standard_rate',
        'image': 'item.image as image',
        'max_discount': 'item.max_discount as max_discount',
        'stock_uom': 'item.stock_uom as stock_uom'
    }
    
    # Add essential fields first to ensure they're always present
    for essential_field, essential_select in essential_mapping.items():
        if essential_field in field_list:
            select_fields.append(essential_select)
    
    # Add other requested fields
    for field in field_list:
        if field not in essential_mapping:
            select_fields.append(f"item.{field} as {field}")
    
    return "SELECT DISTINCT " + ", ".join(select_fields)

def build_search_conditions(terms: List[str], search_text: str) -> Dict[str, Any]:
    """Build search conditions and parameters for different match types"""
    conditions = {}
    
    # Space-normalized search term for space-insensitive matching
    search_no_spaces = search_text.replace(' ', '').replace('-', '').replace('_', '').lower()
    conditions['search_no_spaces'] = search_no_spaces
    
    # Partial matching conditions for all terms
    def build_partial_conditions(field: str, terms: List[str]) -> tuple:
        conditions_list = [f"item.{field} LIKE %s" for _ in terms]
        params = [f'%{term}%' for term in terms]
        where_clause = " AND ".join(conditions_list) if conditions_list else "1=1"
        return where_clause, params
    
    # Build conditions for each field type
    conditions['name_where'], conditions['name_params'] = build_partial_conditions('item_name', terms)
    conditions['code_where'], conditions['code_params'] = build_partial_conditions('item_code', terms)
    conditions['sku_where'], conditions['sku_params'] = build_partial_conditions('custom_sku_code', terms)
    
    # Word boundary conditions for SKU (more precise matching)
    word_boundary_conditions = []
    word_boundary_params = []

    for term in terms:
        word_boundary_conditions.append("item.custom_sku_code REGEXP %s")
        escaped_term = re.escape(term)
        word_boundary_params.append(f'(^|[^a-zA-Z0-9]){escaped_term}([^a-zA-Z0-9]|$)')

    conditions['word_boundary_where'] = " AND ".join(word_boundary_conditions) if word_boundary_conditions else "1=1"
    conditions['word_boundary_params'] = word_boundary_params

    # Word boundary conditions for item_name (catches "1234" in "abc 1234" before "xyz1234")
    name_wb_conditions = []
    name_wb_params = []

    for term in terms:
        name_wb_conditions.append("item.item_name REGEXP %s")
        name_wb_params.append(f'(^|[^a-zA-Z0-9]){re.escape(term)}([^a-zA-Z0-9]|$)')

    conditions['name_word_boundary_where'] = " AND ".join(name_wb_conditions) if name_wb_conditions else "1=1"
    conditions['name_word_boundary_params'] = name_wb_params

    # Word boundary conditions for item_code
    code_wb_conditions = []
    code_wb_params = []

    for term in terms:
        code_wb_conditions.append("item.item_code REGEXP %s")
        code_wb_params.append(f'(^|[^a-zA-Z0-9]){re.escape(term)}([^a-zA-Z0-9]|$)')

    conditions['code_word_boundary_where'] = " AND ".join(code_wb_conditions) if code_wb_conditions else "1=1"
    conditions['code_word_boundary_params'] = code_wb_params

    return conditions


def build_filters_clause(filters) -> tuple:
    """
    Parse filters dict/string and build SQL WHERE conditions.
    Supports:
      - Simple equality:      {"has_variants": 0}
      - Frappe-style list:    {"item_group": ["IN", ["Electronics", "Clothing"]]}
      - Operators:            {"standard_rate": [">=", 100]}
      - BETWEEN:              {"standard_rate": ["BETWEEN", [100, 500]]}
      - IS NULL check:        {"brand": None}
    
    Returns: (where_clause_string, params_list)
    The returned where_clause_string starts with " AND " if non-empty,
    so it can be safely appended after existing WHERE conditions.
    """
    if not filters:
        return "", []

    if isinstance(filters, str):
        try:
            import json
            filters = json.loads(filters)
        except (json.JSONDecodeError, ValueError):
            return "", []

    if not isinstance(filters, dict):
        return "", []

    valid_fields = get_valid_item_fields()
    clauses = []
    params = []

    for field, value in filters.items():

        if field not in valid_fields:
            frappe.log_error(f"Invalid filter field ignored: {field}", "Item Search Filter Warning")
            continue

        if isinstance(value, list):
            if len(value) == 2 and isinstance(value[0], str):
                operator = value[0].strip().upper()
                operand = value[1]

                if operator in ("IN", "NOT IN") and isinstance(operand, list):
                    if operand:
                        placeholders = ", ".join(["%s"] * len(operand))
                        clauses.append(f"item.{field} {operator} ({placeholders})")
                        params.extend(operand)

                elif operator in ("=", "!=", ">", "<", ">=", "<=", "LIKE", "NOT LIKE"):
                    clauses.append(f"item.{field} {operator} %s")
                    params.append(operand)

                elif operator == "BETWEEN" and isinstance(operand, list) and len(operand) == 2:
                    clauses.append(f"item.{field} BETWEEN %s AND %s")
                    params.extend(operand)

                elif operator in ("IS", "IS NOT") and operand is None:
                    null_keyword = "IS NULL" if operator == "IS" else "IS NOT NULL"
                    clauses.append(f"item.{field} {null_keyword}")

            else:
                if value:
                    placeholders = ", ".join(["%s"] * len(value))
                    clauses.append(f"item.{field} IN ({placeholders})")
                    params.extend(value)

        elif value is None:
            clauses.append(f"item.{field} IS NULL")

        else:
            clauses.append(f"item.{field} = %s")
            params.append(value)

    where_clause = (" AND " + " AND ".join(clauses)) if clauses else ""
    return where_clause, params


@frappe.whitelist()
def backend_item_search(doctype, txt, searchfield, start, page_len, filters, fields=None):
    """
    Backend/Custom App search function for Item lookup (BACKEND VERSION)
    Returns: List of dictionaries for custom applications
    
    Priority order:
    1. Exact custom_sku_code match (highest priority)
    2. Exact item_code match
    3. Exact item_name match
    4. Word boundary match in custom_sku_code (e.g. "1234" in SKU "abc-1234")
    5. Word boundary match in item_name (e.g. "1234" in "abc 1234" ranks above "xyz1234")
    6. Word boundary match in item_code
    7. Space-insensitive item_name match (e.g., "rohanrambhiya" matches "rohan ram bhiya")
    8. Space-insensitive item_code match
    9. Space-insensitive custom_sku_code match
    10. Partial item_code match
    11. Partial item_name match
    12. Partial custom_sku_code match (lowest priority)

    Optimizations:
    - Dynamic filters support (has_variants, item_group, brand, etc.)
    - Space-insensitive matching using REPLACE and LOWER
    - Reduced code duplication
    - Better parameter handling
    - Field validation with caching
    - More efficient query construction
    - Improved error handling
    """
    
    # Input validation and sanitization
    search_text = txt.strip() if txt and txt.strip() else ""
    start = max(0, int(start or 0))
    page_len = min(50, max(1, int(page_len or 20)))  # Limit page size for performance
    
    # Parse and validate fields
    field_list = parse_fields(fields)
    select_clause = build_select_clause(field_list)

    filters_clause, filters_params = build_filters_clause(filters)
    
    # Handle empty search - return basic results
    if not search_text:
        query = f"""
            {select_clause}
            FROM `tabItem` item
            WHERE item.disabled = 0
            {filters_clause}
            ORDER BY item.item_name
            LIMIT %s, %s
        """
        try:
            results = frappe.db.sql(query, filters_params + [start, page_len], as_dict=True)
            return results
        except Exception as e:
            frappe.log_error(f"Database error in item search: {str(e)}", "Item Search Error")
            return []
    
    # Optimize search terms
    terms = [term.strip() for term in search_text.split() if term.strip()]
    if not terms:
        return []
    
    # Limit number of search terms to prevent performance issues
    terms = terms[:5]  # Limit to 5 terms maximum
    
    # Build search conditions
    conditions = build_search_conditions(terms, search_text)
    search_no_spaces = conditions['search_no_spaces']
    
    # Construct optimized query with space-insensitive matching
    query = f"""
        {select_clause}
        FROM `tabItem` item USE INDEX (PRIMARY, modified)
        WHERE item.disabled = 0
        {filters_clause}
        AND (
            item.custom_sku_code = %s              -- 1. Exact custom_sku_code match
            OR item.item_code = %s                 -- 2. Exact item_code match
            OR item.item_name = %s                 -- 3. Exact item_name match
            OR (item.custom_sku_code IS NOT NULL AND item.custom_sku_code != '' AND {conditions['word_boundary_where']})  -- 4. Word boundary match in SKU
            OR ({conditions['name_word_boundary_where']})  -- 5. Word boundary match in item_name
            OR ({conditions['code_word_boundary_where']})  -- 6. Word boundary match in item_code
            OR (LOWER(REPLACE(REPLACE(REPLACE(item.item_name, ' ', ''), '-', ''), '_', '')) LIKE %s)  -- 7. Space-insensitive item_name
            OR (LOWER(REPLACE(REPLACE(REPLACE(item.item_code, ' ', ''), '-', ''), '_', '')) LIKE %s)  -- 8. Space-insensitive item_code
            OR (item.custom_sku_code IS NOT NULL AND item.custom_sku_code != '' AND LOWER(REPLACE(REPLACE(REPLACE(item.custom_sku_code, ' ', ''), '-', ''), '_', '')) LIKE %s)  -- 9. Space-insensitive SKU
            OR ({conditions['code_where']})        -- 10. Partial item_code match
            OR ({conditions['name_where']})        -- 11. Partial item_name match
            OR (item.custom_sku_code IS NOT NULL AND item.custom_sku_code != '' AND {conditions['sku_where']})  -- 12. Partial custom_sku_code match
        )
        ORDER BY
            CASE
                WHEN item.custom_sku_code = %s THEN 1      -- 1. Exact custom_sku_code (highest priority)
                WHEN item.item_code = %s THEN 2            -- 2. Exact item_code
                WHEN item.item_name = %s THEN 3            -- 3. Exact item_name
                WHEN item.custom_sku_code IS NOT NULL AND item.custom_sku_code != '' AND ({conditions['word_boundary_where']}) THEN 4  -- 4. Word boundary in SKU
                WHEN ({conditions['name_word_boundary_where']}) THEN 5  -- 5. Word boundary in item_name
                WHEN ({conditions['code_word_boundary_where']}) THEN 6  -- 6. Word boundary in item_code
                WHEN LOWER(REPLACE(REPLACE(REPLACE(item.item_name, ' ', ''), '-', ''), '_', '')) LIKE %s THEN 7  -- 7. Space-insensitive item_name
                WHEN LOWER(REPLACE(REPLACE(REPLACE(item.item_code, ' ', ''), '-', ''), '_', '')) LIKE %s THEN 8  -- 8. Space-insensitive item_code
                WHEN item.custom_sku_code IS NOT NULL AND item.custom_sku_code != '' AND LOWER(REPLACE(REPLACE(REPLACE(item.custom_sku_code, ' ', ''), '-', ''), '_', '')) LIKE %s THEN 9  -- 9. Space-insensitive SKU
                WHEN ({conditions['code_where']}) THEN 10  -- 10. Partial item_code
                WHEN ({conditions['name_where']}) THEN 11  -- 11. Partial item_name
                WHEN item.custom_sku_code IS NOT NULL AND item.custom_sku_code != '' AND ({conditions['sku_where']}) THEN 12  -- 12. Partial custom_sku_code
                ELSE 13
            END,
            LENGTH(COALESCE(item.item_name, '')),          -- Shorter names first within same priority
            item.item_name                                 -- Alphabetical within same length
        LIMIT %s, %s
    """

    # Prepare parameters efficiently
    exact_params = [search_text, search_text, search_text]
    space_insensitive_params = [f'%{search_no_spaces}%', f'%{search_no_spaces}%', f'%{search_no_spaces}%']
    order_params = [search_text, search_text, search_text]
    pagination_params = [start, page_len]

    query_params = (
        filters_params +                                    # Dynamic filter conditions
        exact_params +                                      # WHERE clause exact matches
        conditions['word_boundary_params'] +                # WHERE SKU word boundary
        conditions['name_word_boundary_params'] +           # WHERE item_name word boundary
        conditions['code_word_boundary_params'] +           # WHERE item_code word boundary
        space_insensitive_params +                          # WHERE space-insensitive matching
        conditions['code_params'] +                         # WHERE partial code matching
        conditions['name_params'] +                         # WHERE partial name matching
        conditions['sku_params'] +                          # WHERE partial SKU matching
        order_params +                                      # ORDER BY exact matches
        conditions['word_boundary_params'] +                # ORDER BY SKU word boundary
        conditions['name_word_boundary_params'] +           # ORDER BY item_name word boundary
        conditions['code_word_boundary_params'] +           # ORDER BY item_code word boundary
        space_insensitive_params +                          # ORDER BY space-insensitive matching
        conditions['code_params'] +                         # ORDER BY partial code matching
        conditions['name_params'] +                         # ORDER BY partial name matching
        conditions['sku_params'] +                          # ORDER BY partial SKU matching
        pagination_params                                   # Pagination
    )
    
    try:
        results = frappe.db.sql(query, query_params, as_dict=True)
        return results
    except Exception as e:
        frappe.log_error(f"Database error in item search: {str(e)}", "Item Search Error")
        return []

@frappe.whitelist()
def backend_debounced_item_search(doctype, txt, searchfield, start, page_len, filters, fields=None):
    """
    Debounced version of backend_item_search - optimized for real-time search (BACKEND VERSION)
    Returns: List of dictionaries for custom applications
    
    Additional optimizations for debounced searches:
    - Reduced page size for faster response
    - Early termination for very short queries
    """
    
    # For debounced searches, use smaller page sizes for better responsiveness
    optimized_page_len = min(10, int(page_len or 10))
    
    # Skip very short queries to reduce server load
    search_text = txt.strip() if txt and txt.strip() else ""
    if search_text and len(search_text) < 2:
        return []
    
    return backend_item_search(doctype, txt, searchfield, start, optimized_page_len, filters, fields)

# ================================
# UTILITY FUNCTIONS
# ================================

@frappe.whitelist()
def get_search_stats():
    """Get search performance statistics for monitoring"""
    try:
        stats = frappe.db.sql("""
            SELECT 
                COUNT(*) as total_items,
                COUNT(CASE WHEN custom_sku_code IS NOT NULL AND custom_sku_code != '' THEN 1 END) as items_with_sku,
                COUNT(CASE WHEN disabled = 0 THEN 1 END) as active_items
            FROM `tabItem`
        """, as_dict=True)[0]
        
        return stats
    except Exception as e:
        frappe.log_error(f"Error getting search stats: {str(e)}", "Search Stats Error")
        return {"error": "Unable to fetch statistics"}

@frappe.whitelist()  
def validate_search_indexes():
    """Validate that proper indexes exist for optimal search performance"""
    try:
        indexes = frappe.db.sql("""
            SHOW INDEX FROM `tabItem` 
            WHERE Column_name IN ('item_name', 'item_code', 'custom_sku_code', 'disabled')
        """, as_dict=True)
        
        return {
            "indexes_found": len(indexes),
            "indexes": indexes,
            "recommendations": [
                "Ensure index on (disabled, item_name) for default queries",
                "Consider index on (disabled, custom_sku_code) for SKU searches",
                "Monitor query performance with EXPLAIN"
            ]
        }
    except Exception as e:
        frappe.log_error(f"Error validating indexes: {str(e)}", "Index Validation Error")
        return {"error": "Unable to validate indexes"}