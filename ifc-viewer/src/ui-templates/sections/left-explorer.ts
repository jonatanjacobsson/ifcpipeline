import * as BUI from "@thatopen/ui";
import * as OBC from "@thatopen/components";
import { CONTENT_GRID_GAP } from "../../globals";
import { modelsPanelTemplate } from "./models";
import { spatialTreePanelTemplate } from "./spatial-tree";
import { viewpointsPanelTemplate } from "./viewpoints";

export interface LeftExplorerPanelState {
  components: OBC.Components;
  world: OBC.World;
}

export const leftExplorerPanelTemplate: BUI.StatefullComponent<
  LeftExplorerPanelState
> = (state) => {
  const [modelsEl] = BUI.Component.create(modelsPanelTemplate, {
    components: state.components,
  });

  const [spatialEl] = BUI.Component.create(spatialTreePanelTemplate, {
    components: state.components,
  });

  const [viewpointsEl] = BUI.Component.create(
    viewpointsPanelTemplate,
    {
      components: state.components,
      world: state.world,
    },
  );

  return BUI.html`
    <div
      class="left-explorer-column"
      style="display:flex; flex-direction:column; gap:${CONTENT_GRID_GAP}; height:100%; min-height:0; overflow:hidden;"
    >
      <div style="flex:0 0 auto; min-height:0;">${modelsEl}</div>
      <div
        style="flex:1 1 0; min-height:0; display:flex; flex-direction:column; overflow:hidden;"
      >
        ${spatialEl}
      </div>
      <div
        style="flex:0 0 auto; max-height:14rem; min-height:0; overflow:auto;"
      >
        ${viewpointsEl}
      </div>
    </div>
  `;
};
