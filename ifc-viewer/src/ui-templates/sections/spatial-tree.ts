import * as BUI from "@thatopen/ui";
import * as CUI from "@thatopen/ui-obc";
import * as OBC from "@thatopen/components";
import { appIcons } from "../../globals";

export interface SpatialTreePanelState {
  components: OBC.Components;
}

export const spatialTreePanelTemplate: BUI.StatefullComponent<
  SpatialTreePanelState
> = (state) => {
  const { components } = state;

  const [spatialTree] = CUI.tables.spatialTree({ components, models: [] });
  spatialTree.preserveStructureOnFilter = true;
  spatialTree.style.flex = "1";
  spatialTree.style.minHeight = "0";
  spatialTree.style.overflow = "auto";

  const onSearch = (e: Event) => {
    const input = e.target as BUI.TextInput;
    spatialTree.queryString = input.value;
  };

  return BUI.html`
    <bim-panel-section
      icon=${appIcons.TREE}
      label="Model tree"
      style="flex:1; min-height:0; display:flex; flex-direction:column; overflow:hidden;"
    >
      <bim-text-input
        @input=${onSearch}
        vertical
        placeholder="Search tree..."
        debounce="200"
      ></bim-text-input>
      ${spatialTree}
    </bim-panel-section>
  `;
};
