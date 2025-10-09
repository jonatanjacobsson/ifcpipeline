"""
MergeTasksFromPrevious Recipe

This recipe preserves IfcTask history across IFC model versions using a per-revision
approach. Each model update creates ONE task representing that entire revision, with
all changed elements assigned to it. Element-specific change details are stored in
PM PropertySets.

This design supports weekly IFC workflows where one task = one model update/review.

Recipe Name: MergeTasksFromPrevious
Version: 0.1.0
Description: Merge task history from previous IFC model and add revision task from diff
Author: IFC Pipeline Team
Date: 2025-10-09

Key Changes in v2.0.0:
- Changed from one-task-per-element to one-task-per-revision
- Tasks represent model updates/revisions, not individual element changes
- Element-specific details stored in PM PropertySet
- Sequential task relationships (PM1 → PM2 → PM3)
- Optional revision naming support

References:
- IfcOpenShell Sequence API: https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html
"""

import json
import logging
import re
from typing import Dict, List, Optional, Set, Tuple

import ifcopenshell
import ifcopenshell.api
from ifcopenshell.util import element as uel
from ifcpatch import BasePatcher

logger = logging.getLogger(__name__)

# Hardcoded constants as per specification
# Support both "modified" and "changed" from different IfcDiff versions
CREATE_TASKS_FOR = ["modified", "changed", "added"]  # Create tasks for modified/changed and added elements
TASK_STATUS = "COMPLETED"                  # All tasks marked as completed
ALL_TASKS_AS_MILESTONES = True            # All tasks are milestones
WORK_SCHEDULE_NAME = "PM History"         # Default schedule name
SKIP_PSET_GENERATION = False              # Always generate PM psets
PREDEFINED_TYPE = "OPERATION"             # Task type for changes


class Patcher(BasePatcher):
    """
    MergeTasksFromPrevious patcher using a per-revision approach.
    
    Creates ONE task per model revision/update, with all changed elements assigned
    to that task. Element-specific change details are stored in PM PropertySets.
    
    This patcher:
    - Re-injects all IfcTask entities from the previous model
    - Creates ONE new task representing this entire revision
    - Assigns all changed elements to this single task
    - Creates sequential relationships between revision tasks (PM1 → PM2 → PM3)
    - Generates "PM" PropertySet on each affected element with:
      - History: "PM1, PM2, PM3"
      - Latest: "PM3"
      - PM1: "IfcWall Wall-123 - added"
      - PM2: "IfcWall Wall-123 - modified (geometry)"
    
    Parameters:
        file: The target IFC model (new model) - provided by ifcpatch framework
        logger: Logger instance for output
        prev_path: Path to previous IFC model with existing tasks
        diff_path: Path to IfcDiff JSON output file
        pm_code_prefix: Prefix for PM codes (default: "PM")
        description_template: Template for element descriptions (default: "{ifc_class} {name} - {type}")
        revision_name: Optional name for this revision (e.g., "Week 1", "2025-10-09")
    
    Example:
        patcher = Patcher(
            new_ifc,
            logger,
            "/path/to/prev.ifc",
            "/path/to/diff.json",
            "PM",
            "{ifc_class} {name} - {type}",
            "Week 2"
        )
        patcher.patch()
        output = patcher.get_output()
    """
    
    def __init__(
        self,
        file: ifcopenshell.file,
        logger: logging.Logger,
        prev_path: str,
        diff_path: str,
        pm_code_prefix: str = "PM",
        description_template: str = "{ifc_class} {name} - {type}",
        revision_name: Optional[str] = None
    ):
        """Initialize the patcher with required parameters.
        
        Args:
            revision_name: Optional name for this revision (e.g., "Week 1", "2025-10-09")
        """
        super().__init__(file, logger)
        
        # Validate and store arguments
        self.prev_path = prev_path
        self.diff_path = diff_path
        self.pm_code_prefix = pm_code_prefix if pm_code_prefix else "PM"
        self.description_template = description_template if description_template else "{ifc_class} {name} - {type}"
        self.revision_name = revision_name
        
        # Will be populated during patch
        self.prev_ifc: Optional[ifcopenshell.file] = None
        self.guid_index: Dict[str, ifcopenshell.entity_instance] = {}
        self.task_mapping: Dict[int, ifcopenshell.entity_instance] = {}
        self.name_to_task: Dict[str, ifcopenshell.entity_instance] = {}
        self.work_schedule: Optional[ifcopenshell.entity_instance] = None
        self.pm_counter: int = 1
        self.element_change_details: Dict[ifcopenshell.entity_instance, str] = {}  # Element-specific descriptions
        
        self.logger.info(
            f"Initialized MergeTasksFromPrevious: "
            f"prev='{prev_path}', diff='{diff_path}', prefix='{self.pm_code_prefix}'"
        )
    
    def patch(self) -> None:
        """
        Execute the patching logic.
        
        This method:
        1. Opens the previous IFC file
        2. Creates GUID index for the target file
        3. Ensures work schedule exists
        4. Clones all tasks from previous model
        5. Recreates task relationships
        6. Adds new tasks from diff
        7. Generates PM property sets
        """
        try:
            self.logger.info("=" * 80)
            self.logger.info("Starting MergeTasksFromPrevious patch operation")
            self.logger.info("=" * 80)
            
            # Step 1: Open previous IFC file
            self.logger.info(f"Step 1/7: Opening previous IFC file: {self.prev_path}")
            self.prev_ifc = ifcopenshell.open(self.prev_path)
            self.logger.info(f"Previous IFC opened: {self.prev_ifc.schema} schema")
            
            # Step 2: Create GUID index for target file
            self.logger.info("Step 2/7: Creating GUID index for target file")
            self.guid_index = self._index_by_guid(self.file)
            self.logger.info(f"Indexed {len(self.guid_index)} elements by GlobalId")
            
            # Step 3: Ensure work schedule exists
            self.logger.info("Step 3/7: Ensuring work schedule exists")
            self.work_schedule = self._get_or_create_work_schedule()
            self.logger.info(f"Using work schedule: {self.work_schedule.Name}")
            
            # Step 4: Clone tasks from previous model
            self.logger.info("Step 4/7: Cloning tasks from previous model")
            self._clone_tasks_from_previous()
            
            # Step 5: Recreate task relationships
            self.logger.info("Step 5/7: Recreating task relationships")
            self._recreate_all_relationships()
            
            # Step 6: Add new tasks from diff
            self.logger.info("Step 6/7: Adding new tasks from diff")
            self._add_tasks_from_diff()
            
            # Step 7: Generate PM property sets
            if not SKIP_PSET_GENERATION:
                self.logger.info("Step 7/7: Generating PM property sets")
                self._rebuild_all_pm_psets()
            else:
                self.logger.info("Step 7/7: Skipping PM property set generation (disabled)")
            
            self.logger.info("=" * 80)
            self.logger.info("MergeTasksFromPrevious patch operation completed successfully")
            self.logger.info("=" * 80)
            
        except Exception as e:
            self.logger.error(f"Error during MergeTasksFromPrevious patch: {str(e)}", exc_info=True)
            raise
    
    def get_output(self) -> ifcopenshell.file:
        """Return the patched IFC file."""
        return self.file
    
    # =========================================================================
    # Helper Methods: Indexing and Work Schedule
    # =========================================================================
    
    def _index_by_guid(self, ifc_file: ifcopenshell.file) -> Dict[str, ifcopenshell.entity_instance]:
        """Create an index of all elements by their GlobalId."""
        index = {}
        for element in ifc_file.by_type("IfcRoot"):
            if hasattr(element, "GlobalId") and element.GlobalId:
                index[element.GlobalId] = element
        return index
    
    def _get_or_create_work_schedule(self) -> ifcopenshell.entity_instance:
        """Get existing work schedule or create a new one."""
        # Try to find existing schedule with our name
        schedules = self.file.by_type("IfcWorkSchedule")
        for schedule in schedules:
            if schedule.Name == WORK_SCHEDULE_NAME:
                self.logger.info(f"Found existing work schedule: {WORK_SCHEDULE_NAME}")
                return schedule
        
        # Use first available schedule if any
        if schedules:
            self.logger.info(f"Using existing work schedule: {schedules[0].Name}")
            return schedules[0]
        
        # Create new work schedule
        self.logger.info(f"Creating new work schedule: {WORK_SCHEDULE_NAME}")
        schedule = ifcopenshell.api.run(
            "sequence.add_work_schedule",
            self.file,
            name=WORK_SCHEDULE_NAME,
            predefined_type="NOTDEFINED"
        )
        return schedule
    
    def _detect_next_pm_number(self) -> int:
        """
        Detect the next PM number by scanning existing tasks in previous model.
        Returns the next available number.
        """
        max_num = 0
        pattern = re.compile(rf"^{re.escape(self.pm_code_prefix)}(\d+)$")
        
        for task in self.prev_ifc.by_type("IfcTask"):
            if task.Name:
                match = pattern.match(task.Name)
                if match:
                    num = int(match.group(1))
                    max_num = max(max_num, num)
        
        next_num = max_num + 1
        self.logger.info(f"Detected next PM number: {self.pm_code_prefix}{next_num}")
        return next_num
    
    # =========================================================================
    # Helper Methods: Task Cloning
    # =========================================================================
    
    def _clone_tasks_from_previous(self) -> None:
        """Clone all tasks from previous model to target model."""
        prev_tasks = self.prev_ifc.by_type("IfcTask")
        self.logger.info(f"Found {len(prev_tasks)} tasks in previous model")
        
        if not prev_tasks:
            self.logger.warning("No tasks found in previous model")
            return
        
        cloned_count = 0
        for i, prev_task in enumerate(prev_tasks):
            if (i + 1) % 50 == 0:
                self.logger.info(f"Cloning task {i + 1}/{len(prev_tasks)}")
            
            try:
                new_task = self._clone_task_with_api(prev_task)
                if new_task:
                    cloned_count += 1
            except Exception as e:
                self.logger.warning(f"Failed to clone task {prev_task.GlobalId}: {str(e)}")
                continue
        
        self.logger.info(f"Successfully cloned {cloned_count}/{len(prev_tasks)} tasks")
    
    def _clone_task_with_api(self, prev_task: ifcopenshell.entity_instance) -> Optional[ifcopenshell.entity_instance]:
        """
        Clone a single task from previous model using ifcopenshell.api.
        
        References:
        - add_task: https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.add_task
        - add_task_time: https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.add_task_time
        - edit_task_time: https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.edit_task_time
        """
        # Check if task already exists (idempotency)
        if prev_task.GlobalId in self.guid_index:
            existing = self.guid_index[prev_task.GlobalId]
            if existing.is_a("IfcTask"):
                self.logger.debug(f"Task {prev_task.GlobalId} already exists, skipping")
                self.task_mapping[prev_task.id()] = existing
                if prev_task.Name:
                    self.name_to_task[prev_task.Name] = existing
                return existing
        
        # Create new task using API - handle IFC2X3 vs IFC4+ schema differences
        schema = self.file.schema
        task_params = {
            "work_schedule": self.work_schedule,
            "name": prev_task.Name or "Unnamed Task",
            "description": getattr(prev_task, "Description", None),
            "predefined_type": getattr(prev_task, "PredefinedType", "NOTDEFINED")
        }
        
        # Only add identification for IFC4+ (IFC2X3 doesn't support it)
        if schema in ["IFC4", "IFC4X3"] or schema.startswith("IFC4"):
            identification = getattr(prev_task, "Identification", None)
            if identification:
                task_params["identification"] = identification
        
        new_task = ifcopenshell.api.run(
            "sequence.add_task",
            self.file,
            **task_params
        )
        
        # Preserve original GlobalId for continuity
        new_task.GlobalId = prev_task.GlobalId
        
        # Copy additional attributes using edit_task - schema-aware
        attributes = {}
        if hasattr(prev_task, "ObjectType") and prev_task.ObjectType:
            attributes["ObjectType"] = prev_task.ObjectType
        if hasattr(prev_task, "Status") and prev_task.Status:
            attributes["Status"] = prev_task.Status
        if hasattr(prev_task, "WorkMethod") and prev_task.WorkMethod:
            attributes["WorkMethod"] = prev_task.WorkMethod
        if hasattr(prev_task, "IsMilestone"):
            attributes["IsMilestone"] = prev_task.IsMilestone
        if hasattr(prev_task, "Priority") and prev_task.Priority:
            attributes["Priority"] = prev_task.Priority
        
        # Handle description field - schema-aware
        if schema in ["IFC4", "IFC4X3"] or schema.startswith("IFC4"):
            # IFC4+: Use LongDescription
            if hasattr(prev_task, "LongDescription") and prev_task.LongDescription:
                attributes["LongDescription"] = prev_task.LongDescription
        else:
            # IFC2X3: Use Description
            if hasattr(prev_task, "Description") and prev_task.Description:
                attributes["Description"] = prev_task.Description
        
        if attributes:
            ifcopenshell.api.run("sequence.edit_task", self.file, task=new_task, attributes=attributes)
        
        # Clone task time if present
        if hasattr(prev_task, "TaskTime") and prev_task.TaskTime:
            self._clone_task_time(prev_task.TaskTime, new_task)
        
        # Store mapping and index
        self.task_mapping[prev_task.id()] = new_task
        if prev_task.Name:
            self.name_to_task[prev_task.Name] = new_task
        self.guid_index[new_task.GlobalId] = new_task
        
        return new_task
    
    def _clone_task_time(self, prev_time: ifcopenshell.entity_instance, new_task: ifcopenshell.entity_instance) -> None:
        """Clone task time attributes to a new task."""
        # Determine if recurring
        is_recurring = prev_time.is_a("IfcTaskTimeRecurring")
        
        # Create task time
        new_time = ifcopenshell.api.run(
            "sequence.add_task_time",
            self.file,
            task=new_task,
            is_recurring=is_recurring
        )
        
        # Copy attributes
        time_attributes = {}
        for attr in ["ScheduleDuration", "ScheduleStart", "ScheduleFinish", 
                     "EarlyStart", "EarlyFinish", "LateStart", "LateFinish",
                     "FreeFloat", "TotalFloat", "IsCritical", "StatusTime",
                     "ActualDuration", "ActualStart", "ActualFinish",
                     "RemainingTime", "Completion"]:
            if hasattr(prev_time, attr):
                value = getattr(prev_time, attr)
                if value is not None:
                    time_attributes[attr] = value
        
        if time_attributes:
            ifcopenshell.api.run(
                "sequence.edit_task_time",
                self.file,
                task_time=new_time,
                attributes=time_attributes
            )
    
    # =========================================================================
    # Helper Methods: Relationship Recreation
    # =========================================================================
    
    def _recreate_all_relationships(self) -> None:
        """Recreate all task relationships from previous model."""
        self._recreate_process_assignments()
        self._recreate_sequences()
    
    def _recreate_process_assignments(self) -> None:
        """
        Recreate IfcRelAssignsToProcess relationships.
        
        References:
        - assign_process: https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.assign_process
        """
        assignments = self.prev_ifc.by_type("IfcRelAssignsToProcess")
        self.logger.info(f"Recreating {len(assignments)} process assignments")
        
        recreated = 0
        for assignment in assignments:
            prev_task = assignment.RelatingProcess
            if not prev_task or prev_task.id() not in self.task_mapping:
                continue
            
            new_task = self.task_mapping[prev_task.id()]
            
            # Map related objects to new model
            for related_obj in assignment.RelatedObjects or []:
                if not hasattr(related_obj, "GlobalId"):
                    continue
                
                target_obj = self.guid_index.get(related_obj.GlobalId)
                if not target_obj:
                    continue
                
                # Check if relationship already exists
                if self._has_process_assignment(new_task, target_obj):
                    continue
                
                try:
                    ifcopenshell.api.run(
                        "sequence.assign_process",
                        self.file,
                        relating_process=new_task,
                        related_object=target_obj
                    )
                    recreated += 1
                except Exception as e:
                    self.logger.warning(f"Failed to assign process: {str(e)}")
        
        self.logger.info(f"Recreated {recreated} process assignments")
    
    def _has_process_assignment(self, task: ifcopenshell.entity_instance, obj: ifcopenshell.entity_instance) -> bool:
        """Check if a process assignment already exists."""
        for rel in self.file.by_type("IfcRelAssignsToProcess"):
            if rel.RelatingProcess == task and obj in (rel.RelatedObjects or []):
                return True
        return False
    
    def _recreate_sequences(self) -> None:
        """
        Recreate IfcRelSequence relationships.
        
        References:
        - assign_sequence: https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.assign_sequence
        - assign_lag_time: https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.assign_lag_time
        """
        sequences = self.prev_ifc.by_type("IfcRelSequence")
        self.logger.info(f"Recreating {len(sequences)} task sequences")
        
        recreated = 0
        for seq in sequences:
            prev_relating = getattr(seq, "RelatingProcess", None)
            prev_related = getattr(seq, "RelatedProcess", None)
            
            if not prev_relating or not prev_related:
                continue
            
            if prev_relating.id() not in self.task_mapping or prev_related.id() not in self.task_mapping:
                continue
            
            new_relating = self.task_mapping[prev_relating.id()]
            new_related = self.task_mapping[prev_related.id()]
            
            # Check if sequence already exists
            if self._has_sequence(new_relating, new_related):
                continue
            
            try:
                # Create sequence
                sequence_type = getattr(seq, "SequenceType", "FINISH_START")
                new_seq = ifcopenshell.api.run(
                    "sequence.assign_sequence",
                    self.file,
                    relating_process=new_relating,
                    related_process=new_related,
                    sequence_type=sequence_type
                )
                
                # Add lag time if present
                if hasattr(seq, "TimeLag") and seq.TimeLag:
                    lag_value = getattr(seq.TimeLag, "LagValue", None)
                    duration_type = getattr(seq.TimeLag, "DurationType", "WORKTIME")
                    if lag_value:
                        ifcopenshell.api.run(
                            "sequence.assign_lag_time",
                            self.file,
                            rel_sequence=new_seq,
                            lag_value=str(lag_value),
                            duration_type=duration_type
                        )
                
                recreated += 1
            except Exception as e:
                self.logger.warning(f"Failed to create sequence: {str(e)}")
        
        self.logger.info(f"Recreated {recreated} sequences")
    
    def _has_sequence(self, relating: ifcopenshell.entity_instance, related: ifcopenshell.entity_instance) -> bool:
        """Check if a sequence relationship already exists."""
        for seq in self.file.by_type("IfcRelSequence"):
            if seq.RelatingProcess == relating and seq.RelatedProcess == related:
                return True
        return False
    
    # =========================================================================
    # Helper Methods: Add Tasks from Diff
    # =========================================================================
    
    def _add_tasks_from_diff(self) -> None:
        """
        Add ONE PM task for this entire revision based on diff results.
        
        Creates a single task representing all changes in this model update/revision,
        then assigns all changed elements to that task. Element-specific details are
        stored in the PM PropertySet on each element.
        
        References:
        - add_task: https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.add_task
        - edit_task: https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.edit_task
        - assign_process: https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.assign_process
        - assign_sequence: https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.assign_sequence
        """
        # Load diff data
        try:
            with open(self.diff_path, 'r', encoding='utf-8') as f:
                diff_data = json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load diff file: {str(e)}")
            raise
        
        # Count changes
        added_list = diff_data.get("added", [])
        changed_dict = diff_data.get("changed", {})
        deleted_list = diff_data.get("deleted", [])
        
        total_added = len(added_list)
        total_changed = len(changed_dict)
        total_deleted = len(deleted_list)
        total_changes = total_added + total_changed
        
        self.logger.info(
            f"Processing diff: {total_changed} changed, {total_added} added, "
            f"{total_deleted} deleted ({total_changes} total changes to process)"
        )
        
        # If no changes, skip task creation
        if total_changes == 0:
            self.logger.info("No changes to process, skipping task creation")
            return
        
        # Detect PM number for this revision
        self.pm_counter = self._detect_next_pm_number()
        pm_code = f"{self.pm_code_prefix}{self.pm_counter}"
        
        # Collect all changed elements and their details
        all_elements = []
        
        # Process added elements
        for guid in added_list:
            element = self.guid_index.get(guid)
            if element:
                all_elements.append(element)
                self.element_change_details[element] = self._get_change_description(
                    element, "added", None
                )
        
        # Process changed elements
        for guid, metadata in changed_dict.items():
            element = self.guid_index.get(guid)
            if element:
                all_elements.append(element)
                self.element_change_details[element] = self._get_change_description(
                    element, "changed", metadata
                )
        
        if not all_elements:
            self.logger.warning(
                f"No elements found in target model from {total_changes} changes in diff"
            )
            return
        
        self.logger.info(f"Found {len(all_elements)} elements in target model to assign to {pm_code}")
        
        # Create ONE task for this entire revision
        task = self._create_revision_task(pm_code, len(all_elements), total_added, total_changed)
        
        # Assign all elements to this task
        assigned_count = 0
        for element in all_elements:
            try:
                ifcopenshell.api.run(
                    "sequence.assign_process",
                    self.file,
                    relating_process=task,
                    related_object=element
                )
                assigned_count += 1
            except Exception as e:
                self.logger.warning(f"Failed to assign element {element.GlobalId} to {pm_code}: {str(e)}")
        
        self.logger.info(f"Assigned {assigned_count} elements to task {pm_code}")
        
        # Create sequence with previous PM task if exists
        self._create_sequence_to_previous_pm(task, self.pm_counter - 1)
        
        # Store task in mapping
        self.name_to_task[pm_code] = task
        
        self.logger.info(f"Created revision task {pm_code} for {len(all_elements)} elements")
    
    def _create_revision_task(
        self,
        pm_code: str,
        element_count: int,
        added_count: int,
        changed_count: int
    ) -> ifcopenshell.entity_instance:
        """Create a single task representing this entire revision."""
        # Build task description
        if self.revision_name:
            description = f"Revision {pm_code} ({self.revision_name}): {element_count} changes"
        else:
            description = f"Revision {pm_code}: {element_count} changes"
        
        description += f" ({added_count} added, {changed_count} changed)"
        
        # Create task - handle IFC2X3 vs IFC4+ schema differences
        # IFC2X3.IfcTask doesn't have Identification attribute
        schema = self.file.schema
        task_params = {
            "work_schedule": self.work_schedule,
            "name": pm_code,
            "predefined_type": PREDEFINED_TYPE
        }
        
        # Only add identification for IFC4 and above (IFC2X3 doesn't support it)
        if schema in ["IFC4", "IFC4X3"] or schema.startswith("IFC4"):
            task_params["identification"] = pm_code
        
        task = ifcopenshell.api.run(
            "sequence.add_task",
            self.file,
            **task_params
        )
        
        # Set task attributes - schema-aware
        task_attributes = {
            "Status": TASK_STATUS,
            "IsMilestone": ALL_TASKS_AS_MILESTONES
        }
        
        # IFC4+ has LongDescription, IFC2X3 only has Description
        if schema in ["IFC4", "IFC4X3"] or schema.startswith("IFC4"):
            task_attributes["LongDescription"] = description
        else:
            # IFC2X3: Use Description field instead
            task_attributes["Description"] = description
        
        ifcopenshell.api.run(
            "sequence.edit_task",
            self.file,
            task=task,
            attributes=task_attributes
        )
        
        self.logger.info(f"Created task {pm_code}: {description}")
        return task
    
    def _get_change_description(
        self,
        element: ifcopenshell.entity_instance,
        change_type: str,
        diff_metadata: Optional[dict]
    ) -> str:
        """Generate element-specific change description."""
        ifc_class = element.is_a() if hasattr(element, 'is_a') else "Unknown"
        name = getattr(element, 'Name', 'Unnamed') or 'Unnamed'
        
        # Extract specific change info from metadata
        change_details = []
        if diff_metadata:
            if diff_metadata.get("geometry_changed"):
                change_details.append("geometry")
            if diff_metadata.get("properties_changed"):
                change_details.append("properties")
            if diff_metadata.get("materials_changed"):
                change_details.append("materials")
        
        # Build description using template
        description = self.description_template.format(
            ifc_class=ifc_class,
            name=name,
            type=change_type
        )
        
        # Append details if available
        if change_details:
            description += f" ({', '.join(change_details)})"
        
        return description
    
    def _create_sequence_to_previous_pm(
        self,
        current_task: ifcopenshell.entity_instance,
        previous_pm_number: int
    ) -> None:
        """Create sequence relationship to the previous PM task."""
        if previous_pm_number < 1:
            return  # No previous PM task
        
        previous_pm_code = f"{self.pm_code_prefix}{previous_pm_number}"
        previous_task = self.name_to_task.get(previous_pm_code)
        
        if not previous_task:
            self.logger.debug(f"No previous task {previous_pm_code} found for sequencing")
            return
        
        # Check if sequence already exists
        if self._has_sequence(previous_task, current_task):
            self.logger.debug(f"Sequence {previous_pm_code} → {current_task.Name} already exists")
            return
        
        try:
            ifcopenshell.api.run(
                "sequence.assign_sequence",
                self.file,
                relating_process=previous_task,
                related_process=current_task,
                sequence_type="FINISH_START"
            )
            self.logger.info(f"Created sequence: {previous_pm_code} → {current_task.Name}")
        except Exception as e:
            self.logger.warning(f"Failed to create sequence {previous_pm_code} → {current_task.Name}: {str(e)}")
    
    def _find_last_task_for_element(self, element: ifcopenshell.entity_instance) -> Optional[ifcopenshell.entity_instance]:
        """Find the last (terminal) task associated with an element."""
        # Get all tasks assigned to this element
        tasks = []
        for rel in self.file.by_type("IfcRelAssignsToProcess"):
            if element in (rel.RelatedObjects or []):
                tasks.append(rel.RelatingProcess)
        
        if not tasks:
            return None
        
        # Find terminal tasks (no outgoing sequences)
        outgoing_tasks = {seq.RelatingProcess for seq in self.file.by_type("IfcRelSequence")}
        terminal_tasks = [t for t in tasks if t not in outgoing_tasks]
        
        return terminal_tasks[0] if terminal_tasks else tasks[-1]
    
    # =========================================================================
    # Helper Methods: PM Property Sets
    # =========================================================================
    
    def _rebuild_all_pm_psets(self) -> None:
        """Rebuild PM property sets for all affected elements."""
        # Find all elements with task assignments
        elements_with_tasks: Set[ifcopenshell.entity_instance] = set()
        
        for rel in self.file.by_type("IfcRelAssignsToProcess"):
            for obj in rel.RelatedObjects or []:
                elements_with_tasks.add(obj)
        
        self.logger.info(f"Rebuilding PM psets for {len(elements_with_tasks)} elements")
        
        rebuilt = 0
        for i, element in enumerate(elements_with_tasks):
            if (i + 1) % 100 == 0:
                self.logger.info(f"Processing element {i + 1}/{len(elements_with_tasks)}")
            
            try:
                self._build_pm_pset_for_element(element)
                rebuilt += 1
            except Exception as e:
                self.logger.warning(f"Failed to build PM pset for element: {str(e)}")
        
        self.logger.info(f"Successfully rebuilt {rebuilt} PM property sets")
    
    def _build_pm_pset_for_element(self, element: ifcopenshell.entity_instance) -> None:
        """Build PM property set for a single element."""
        # Get all tasks for this element
        tasks = self._get_tasks_for_element(element)
        if not tasks:
            return
        
        # Order tasks chronologically
        ordered_tasks = self._order_tasks_chronologically(tasks)
        if not ordered_tasks:
            return
        
        # Get existing PM PropertySet from previous model if element exists there
        prev_pm_data = {}
        if self.prev_ifc:
            try:
                prev_element = self.prev_ifc.by_guid(element.GlobalId)
                prev_pset = uel.get_pset(prev_element, "PM")
                if prev_pset:
                    prev_pm_data = dict(prev_pset)
            except:
                pass  # Element not in previous model or no PM pset
        
        # Build property set data
        pset_data = {}
        
        # Historik: comma-separated task names
        task_names = [t.Name for t in ordered_tasks if t.Name]
        if task_names:
            pset_data["Historik"] = ", ".join(task_names)
        
        # Senaste: latest task name
        if task_names:
            pset_data["Senaste"] = task_names[-1]
        
        # Individual task properties: PMx = element-specific description
        for task in ordered_tasks:
            if task.Name:
                # For current revision (in element_change_details), use new description
                if element in self.element_change_details:
                    description = self.element_change_details[element]
                # For previous revisions, preserve description from previous model's PM pset
                elif task.Name in prev_pm_data:
                    description = prev_pm_data[task.Name]
                # Fallback: use task's LongDescription (revision summary)
                else:
                    description = getattr(task, "LongDescription", None) or getattr(task, "Description", None) or ""
                
                pset_data[task.Name] = description
        
        # Create/update property set using ifcopenshell.api
        if pset_data:
            # Check if PM pset already exists
            existing_pset = uel.get_pset(element, "PM")
            
            if existing_pset:
                # Update existing pset
                ifcopenshell.api.run(
                    "pset.edit_pset",
                    self.file,
                    pset=self.file.by_guid(existing_pset["id"]),
                    properties=pset_data
                )
            else:
                # Create new pset
                pset = ifcopenshell.api.run(
                    "pset.add_pset",
                    self.file,
                    product=element,
                    name="PM"
                )
                # Add properties to the new pset
                ifcopenshell.api.run(
                    "pset.edit_pset",
                    self.file,
                    pset=pset,
                    properties=pset_data
                )
    
    def _get_tasks_for_element(self, element: ifcopenshell.entity_instance) -> List[ifcopenshell.entity_instance]:
        """Get all tasks assigned to an element."""
        tasks = []
        for rel in self.file.by_type("IfcRelAssignsToProcess"):
            if element in (rel.RelatedObjects or []):
                tasks.append(rel.RelatingProcess)
        return list(set(tasks))  # Deduplicate
    
    def _order_tasks_chronologically(self, tasks: List[ifcopenshell.entity_instance]) -> List[ifcopenshell.entity_instance]:
        """
        Order tasks chronologically using topological sort on sequences.
        Falls back to task time sorting if no sequences exist.
        """
        if not tasks:
            return []
        
        # Build sequence graph
        task_set = set(tasks)
        nexts: Dict[ifcopenshell.entity_instance, Set[ifcopenshell.entity_instance]] = {}
        prevs: Dict[ifcopenshell.entity_instance, Set[ifcopenshell.entity_instance]] = {}
        
        for seq in self.file.by_type("IfcRelSequence"):
            relating = seq.RelatingProcess
            related = seq.RelatedProcess
            
            if relating in task_set and related in task_set:
                nexts.setdefault(relating, set()).add(related)
                prevs.setdefault(related, set()).add(relating)
        
        # Topological sort
        if nexts or prevs:
            ordered = []
            visited = set()
            
            # Find starting tasks (no predecessors)
            starts = [t for t in tasks if t not in prevs]
            if not starts:
                # Fallback: use any task if there's a cycle
                starts = [tasks[0]]
            
            def visit(task):
                if task in visited:
                    return
                visited.add(task)
                ordered.append(task)
                for next_task in nexts.get(task, []):
                    visit(next_task)
            
            for start in starts:
                visit(start)
            
            # Add any unvisited tasks
            for task in tasks:
                if task not in visited:
                    ordered.append(task)
            
            return ordered
        
        # Fallback: sort by task time or name
        def sort_key(task):
            task_time = getattr(task, "TaskTime", None)
            if task_time:
                start = getattr(task_time, "ActualStart", None) or getattr(task_time, "ScheduleStart", None)
                if start:
                    return (0, str(start))
            return (1, task.Name or "")
        
        return sorted(tasks, key=sort_key)

