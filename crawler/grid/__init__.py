"""AG Grid support (M1b): resolve grid cells by row identity x column, act on them, and emit
parameterized, verified XPath templates that survive row virtualization, column
virtualization, pinned columns, sorting and both AG Grid DOM layouts (v31-v35 split rows,
v36+ unified rows).

    cell = resolve_cell(page, GridTarget(RowRef(key_value="A-950"), "Quantity", grid="Orders"), cfg, 10_000)
    act_on_cell(page, cell, "fill", "5", cfg)
    cell.template.xpath   # //div[@id='ordersGrid']//*[@row-index][...{orderId}...]//*[@col-id='quantity']
"""
from .resolve import GridError, ResolvedCell, ensure_grid_engine, resolve_cell, suggest_name
from .xpath import camel, fill_template, xpath_literal

try:  # act_on_cell lives in its own module
    from .actions import act_on_cell
except ImportError:  # pragma: no cover
    raise

__all__ = ["ResolvedCell", "GridError", "resolve_cell", "act_on_cell", "suggest_name",
           "ensure_grid_engine", "xpath_literal", "fill_template", "camel"]
