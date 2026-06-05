import * as BUI from "@thatopen/ui";
import * as CUI from "@thatopen/ui-obc";
import * as OBC from "@thatopen/components";
import * as OBF from "@thatopen/components-front";
import { appIcons } from "../../globals";

/** Row shape produced by `itemsData` (Name / Value columns, nested children). */
type ItemsPropertyRow = BUI.TableGroupData<Record<string, BUI.TableCellValue>>;

function valueToSearchText(value: BUI.TableCellValue | unknown): string {
  if (value === null || value === undefined) return "";
  const t = typeof value;
  if (t === "string" || t === "number" || t === "boolean") return String(value);
  if (t === "object") {
    if (Array.isArray(value)) return value.map(valueToSearchText).join(" ");
    try {
      return JSON.stringify(value);
    } catch {
      return "";
    }
  }
  return String(value);
}

/** All searchable text in this row and every descendant (IFC values are often nested objects). */
function flattenPropertySubtreeText(row: ItemsPropertyRow | undefined): string {
  if (!row?.data) return "";
  let out = "";
  for (const v of Object.values(row.data) as unknown[]) {
    out += `${valueToSearchText(v)} `;
  }
  for (const child of row.children ?? []) {
    out += flattenPropertySubtreeText(child);
  }
  return out;
}

function propertySubtreeMatchesQuery(
  query: string,
  row: ItemsPropertyRow,
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return flattenPropertySubtreeText(row).toLowerCase().includes(q);
}

export interface ElementsDataPanelState {
  components: OBC.Components;
}

export const elementsDataPanelTemplate: BUI.StatefullComponent<
  ElementsDataPanelState
> = (state) => {
  const { components } = state;

  const highlighter = components.get(OBF.Highlighter);

  const [propsTable, updatePropsTable] = CUI.tables.itemsData({
    components,
    modelIdMap: {},
  });

  propsTable.preserveStructureOnFilter = true;
  propsTable.expanded = true;

  /** `bim-table` resets `filterFunction` whenever `queryString` is set; re-apply after each change. */
  const applyDeepPropertyFilter = () => {
    propsTable.filterFunction = (query, row) =>
      propertySubtreeMatchesQuery(query, row as ItemsPropertyRow);
  };
  applyDeepPropertyFilter();

  highlighter.events.select.onHighlight.add((modelIdMap) => {
    updatePropsTable({ modelIdMap });
    applyDeepPropertyFilter();
  });

  highlighter.events.select.onClear.add(() => {
    updatePropsTable({ modelIdMap: {} });
    applyDeepPropertyFilter();
  });

  const search = (e: Event) => {
    const input = e.target as BUI.TextInput;
    propsTable.queryString = input.value;
    applyDeepPropertyFilter();
  };

  const toggleExpanded = () => {
    propsTable.expanded = !propsTable.expanded;
  };

  const expandAll = () => {
    propsTable.expanded = true;
  };

  const collapseAll = () => {
    propsTable.expanded = false;
  };

  const sectionId = BUI.Manager.newRandomId();

  return BUI.html`
    <bim-panel-section fixed id=${sectionId} icon=${appIcons.TASK} label="Selection Data">
      <p class="selection-data-hint">
        Pick in the 3D view or click a row in the model tree to inspect properties and
        property sets.
      </p>
      <div style="display: flex; flex-wrap: wrap; gap: 0.375rem; align-items: flex-end;">
        <bim-text-input @input=${search} vertical placeholder="Search properties..." debounce="200" style="flex: 1 1 8rem; min-width: 6rem;"></bim-text-input>
        <bim-button style="flex: 0;" @click=${expandAll} label="Expand all" tooltip-title="Expand all" tooltip-text="Show all nested property groups and rows."></bim-button>
        <bim-button style="flex: 0;" @click=${collapseAll} label="Collapse all" tooltip-title="Collapse all" tooltip-text="Hide nested rows to reduce clutter."></bim-button>
        <bim-button style="flex: 0;" @click=${toggleExpanded} icon=${appIcons.EXPAND} tooltip-title="Toggle expand" tooltip-text="Toggle expanded state for the table."></bim-button>
        <bim-button style="flex: 0;" @click=${() => propsTable.downloadData("ElementData", "tsv")} icon=${appIcons.EXPORT} tooltip-title="Export Data" tooltip-text="Export the shown properties to TSV."></bim-button>
      </div>
      ${propsTable}
    </bim-panel-section> 
  `;
};
