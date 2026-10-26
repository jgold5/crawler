"""Fixed vocabularies for probe output.

Everything the probe writes is either a number/boolean, a member of one of these
fixed sets, a version string, or a generalized regex/selector whose literal parts
come from these sets. Nothing here may be site-specific.
"""
from __future__ import annotations

# Words that may appear literally inside generated regexes, data-attribute names and
# spinner selectors. Framework prefixes and generic UI vocabulary only.
FRAMEWORK_PREFIXES = frozenset("""
mui mat cdk ng ag ext gen x yui gwt jsx j id idt jsf ember rc react radix
headlessui downshift el ant dx k v vue p pr ui sc css jss tss emotion styled
svelte lit bp chakra fl ion q n e t s r u a b c d f g h i l m o w y z
""".split())

GENERIC_WORDS = frozenset("""
data test tests testid testids tid qa cy automation automationid e2e auto selenium testing
key keys name hook hooks ref locator component comp role element el field col row cell index
idx action target type value state slot id ids uid item items label control input
button link menu menuitem tab tabs panel dialog modal form grid table list option
header footer nav section region container wrapper root icon text title
spinner loader loading progress skeleton overlay busy spin
""".split())

SAFE_SPINNER_WORDS = ("spinner", "loader", "loading", "progress", "skeleton", "overlay", "busy", "spin")

# Data-attribute name parts that are framework/library noise rather than identifiers.
FRAMEWORK_DATA_ATTRS = frozenset("""
data-reactroot data-reactid data-state data-orientation data-placement data-side data-align
data-disabled data-highlighted data-selected data-checked data-active data-open data-focus
data-headlessui-state data-radix-collection-item data-popper-placement data-popper-reference-hidden
data-popper-escaped data-bs-toggle data-bs-target data-bs-dismiss data-toggle data-target
data-dismiss data-turbo data-turbolinks data-slot data-theme data-mode data-ag-css
data-ag-css-version data-ref data-value
""".split())

AG_THEMES = frozenset("""
ag-theme-alpine ag-theme-alpine-dark ag-theme-alpine-auto-dark ag-theme-balham ag-theme-balham-dark
ag-theme-balham-auto-dark ag-theme-material ag-theme-material-dark ag-theme-material-auto-dark
ag-theme-quartz ag-theme-quartz-dark ag-theme-quartz-auto-dark ag-theme-fresh ag-theme-dark
ag-theme-blue ag-theme-bootstrap
""".split())

# ---- enums (closed sets of output strings) ----
ID_SHAPES = ("react-useid", "uuid", "trailing-counter", "long-number", "hash-blob",
             "framework-prefix", "semantic-word", "semantic-with-digit", "other")
UNSTABLE_ID_SHAPES = frozenset({"react-useid", "uuid", "trailing-counter", "long-number", "hash-blob",
                                "framework-prefix"})
CLASS_SHAPES = ("css-in-js", "css-module", "hashed", "utility", "state", "framework", "bem",
                "semantic", "other")
UNSTABLE_CLASS_SHAPES = frozenset({"css-in-js", "css-module", "hashed"})
VALUE_SHAPES = ("kebab", "camel", "pascal", "snake", "upper-snake", "lower-word", "dotted",
                "numeric", "uuid", "hash-like", "free-text", "other")
ROW_ID_SHAPES = ("none", "sequential-number", "number", "key-like", "uuid", "hash-like", "mixed")
COL_ID_SHAPES = ("camel", "snake", "kebab", "lower-word", "pascal", "numeric", "auto-suffix", "dotted", "other")
ROW_MODELS = ("clientSide", "infinite", "serverSide", "viewport", "unknown")
DOM_LAYOUTS = ("normal", "autoHeight", "print", "unknown")
READY_BLOCKERS = ("spinner", "mutations", "network", "readystate", "navigation", "none")
SPINNER_SIGNALS = ("role-progressbar", "aria-busy", "ag-overlay-loading", "class-word", "animated-custom",
                   "custom")
FRAMEWORKS = ("react", "angular", "angularjs", "vue", "jquery", "ember", "extjs", "backbone", "knockout",
              "svelte", "dojo", "kendo", "devextreme", "primefaces", "jsf", "gwt", "bootstrap", "mui",
              "angular-material", "lit", "polymer", "stencil", "salesforce-lightning", "wicket", "zk",
              "vaadin", "blazor", "htmx", "alpine", "preact", "solid", "nextjs", "nuxt")
OTHER_GRIDS = ("html-table", "role-grid-non-ag", "kendo-grid", "devextreme-grid", "handsontable", "slickgrid",
               "tabulator", "mui-datagrid", "ui-grid", "primeng-table", "ext-grid", "vaadin-grid", "jqgrid",
               "datatables", "tanstack-table")
LINK_SKIPS = ("external", "logout", "download", "non-get", "action-like", "non-http", "duplicate-shape",
              "same-document", "over-limit")
INPUT_LABEL_KINDS = ("label-for", "wrapping-label", "aria-label", "aria-labelledby", "title-only",
                     "placeholder-only", "none")
BUTTON_NAME_KINDS = ("text", "aria-label", "aria-labelledby", "title", "icon-only", "empty")
INTERACTIVE_KINDS = ("button", "input", "link", "select", "textarea", "gridcell", "role", "editable")
DATA_ATTR_KINDS = ("test-id", "identifier-like", "framework", "vue-scoped", "other")
