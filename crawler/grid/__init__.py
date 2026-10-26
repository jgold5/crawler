"""AG Grid support (M1b): resolve grid cells by row identity x column, act on them, and emit
parameterized, verified XPath templates that survive row virtualization, column
virtualization, pinned columns, sorting and both AG Grid DOM layouts (v31-v35 split rows,
v36+ unified rows).

    cell = resolve_cell(page, GridTarget(RowRef(key_value="A-950"), "Quantity", grid="Orders"), cfg, 10_000)
    act_on_cell(page, cell, "fill", "5", cfg)
    cell.template.xpath
    # AG Grid 36 (one element per row):
    #   //ag-grid-angular[@aria-label='Orders']//*[@row-index][.//*[@col-id='orderId'][normalize-space()={orderId}]]//*[@col-id='quantity']
    # AG Grid 31-35 (row split over pinned/center containers, joined by @row-index at runtime):
    #   //ag-grid-angular[@aria-label='Orders']//*[@row-index][@row-index=ancestor::*[@aria-rowcount]//*[@col-id='orderId']
    #     [normalize-space()={orderId}]/ancestor::*/@row-index]//*[@col-id='quantity']

Fast paths: GridDef.api_expr, else Angular dev builds' ng.getComponent(<ag-grid-angular>).api,
else frame-driven virtual scrolling of the grid's own scrollers. See crawler/grid/aggrid.js.
"""
from .actions import act_on_cell
from .resolve import GridError, ResolvedCell, ensure_grid_engine, resolve_cell, suggest_name
from .xpath import camel, fill_template, xpath_literal

__all__ = ["ResolvedCell", "GridError", "resolve_cell", "act_on_cell", "suggest_name",
           "ensure_grid_engine", "xpath_literal", "fill_template", "camel"]
