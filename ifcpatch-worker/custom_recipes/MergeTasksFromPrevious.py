"""
MergeTasksFromPrevious Recipe

This recipe preserves IfcTask history across IFC model versions using a per-revision
approach. Each model update creates ONE task representing that entire revision, with
all changed elements assigned to it. Element-specific change details are stored in
PM PropertySets.

This design supports weekly IFC workflows where one task = one model update/review.

Recipe Name: MergeTasksFromPrevious
Version: 0.3.2
Description: Merge task history from previous IFC model and add revision task from diff
Author: IFC Pipeline Team
Date: 2025-10-14

Key Changes in v0.3.2:
- FIX: Revision task descriptions now show actual meaningful change counts (not raw diff counts)
- Task description accurately reflects elements with geometry/material/meaningful property changes
- Example: "Revision V.4: 17 changes (5 added, 12 changed)" instead of "(5 added, 1071 changed)"

Key Changes in v0.3.1:
- ENHANCED: Elements with ONLY ignored property changes are now completely skipped
- No task assignment for elements where only timestamps/IDs changed
- Cleaner revision tracking - only truly changed elements appear in revisions
- Logs show count of skipped elements with ignored-only changes

Key Changes in v0.3.0:
- NEW: Property filtering to ignore meaningless changes (timestamps, IDs, metadata)
- Configurable ignored property patterns using glob-style matching (*.id, *.Timestamp, etc.)
- Elements only marked "changed properties" for meaningful property changes
- Default ignores: *.id, *.Timestamp, ePset_ModelInfo.*
- Pass empty list [] to track all properties (disable filtering)

Key Changes in v0.2.2:
- CRITICAL FIX: Validation now rejects generic "changed" fallback values
- True self-healing: Elements with corrupted PM data lose invalid task assignments
- Fixed revision history sorting (natural/numeric sort: V.6, V.7...V.10 not V.10...V.6)
- Only valid element-specific descriptions ("added", "changed properties") pass validation

Key Changes in v2.2.0:
- Added task assignment validation to prevent incorrect assignments
- Elements now only get tasks for revisions where they were actually changed
- Self-healing behavior progressively cleans up incorrect historical data
- Improved fallback logic for missing PM data

Key Changes in v2.1.0:
- Added shared property set optimization for space efficiency
- PM psets are now deduplicated - elements with identical revision history share one pset
- Reduces file size by creating ~50-200 unique psets instead of thousands
- Maintains all element-specific change information

Key Changes in v2.0.0:
- Changed from one-task-per-element to one-task-per-revision
- Tasks represent model updates/revisions, not individual element changes
- Element-specific details stored in PM PropertySet
- Sequential task relationships (PM1 → PM2 → PM3)
- Optional revision naming support

References:
- IfcOpenShell Sequence API: https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html
- IFC Property Sets: https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD1/HTML/schema/ifckernel/lexical/ifcpropertyset.htm
"""

import fnmatch
import json
import logging
import re
from typing import Dict, List, Optional, Set, Tuple

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.guid
from ifcopenshell.util import element as uel

logger = logging.getLogger(__name__)

# Hardcoded constants as per specification
# Support both "modified" and "changed" from different IfcDiff versions
CREATE_TASKS_FOR = ["modified", "changed", "added"]  # Create tasks for modified/changed and added elements
TASK_STATUS = "COMPLETED"                  # All tasks marked as completed
ALL_TASKS_AS_MILESTONES = True            # All tasks are milestones
WORK_SCHEDULE_NAME = "PM History"         # Default schedule name
SKIP_PSET_GENERATION = False              # Always generate PM psets
PREDEFINED_TYPE = "OPERATION"             # Task type for changes
DEFAULT_IGNORED_PROPERTIES = [
    "*.id",                          # All 'id' properties (often internal IDs)
    "*.Timestamp",                   # All timestamp properties (auto-generated)
    "ePset_ModelInfo.*",             # All properties in ePset_ModelInfo (metadata)
]


class Patcher:
    """
    MergeTasksFromPrevious patcher using a per-revision approach.
    
    Creates ONE task per model revision/update, with all changed elements assigned
    to that task. Element-specific change details are stored in PM PropertySets.
    
    This patcher:
    - Re-injects all IfcTask entities from the previous model
    - Creates ONE new task representing this entire revision
    - Assigns all changed elements to this single task
    - Creates sequential relationships between revision tasks (PM1 → PM2 → PM3)
    - Generates shared "PM" PropertySets for space efficiency:
      - Elements with identical revision history share one PropertySet
      - Reduces from thousands of psets to ~50-200 unique psets
      - Properties include:
        - Revision History: "PM1, PM2, PM3"
        - Latest Change: "PM3"
        - PM1: "added"
        - PM2: "modified geometry"
    
    Parameters:
        file: The target IFC model (new model) - provided by ifcpatch framework
        logger: Logger instance for output
        prev_path: Path to previous IFC model with existing tasks
        diff_path: Path to IfcDiff JSON output file
        pm_code_prefix: Prefix for PM codes (default: "PM")
        description_template: Template for element descriptions (default: "{type}")
        revision_name: Optional name for this revision (e.g., "Week 1", "2025-10-09")
        ignored_properties: List of property patterns to ignore (e.g., ["*.id", "*.Timestamp"])
                          If None (default), uses DEFAULT_IGNORED_PROPERTIES
                          Pass [] to track all properties (disable filtering)
    
    Example:
        patcher = Patcher(
            new_ifc,
            logger,
            "/path/to/prev.ifc",
            "/path/to/diff.json",
            "PM",
            "{type}",
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
        description_template: str = "{type}",
        revision_name: Optional[str] = None,
        ignored_properties: Optional[List[str]] = None
    ):
        """Initialize the patcher with required parameters.
        
        Args:
            revision_name: Optional name for this revision (e.g., "Week 1", "2025-10-09")
            ignored_properties: Optional list of property patterns to ignore (e.g., ["*.id", "*.Timestamp"])
                              If None, uses DEFAULT_IGNORED_PROPERTIES. Pass [] to track all properties.
        """
        # Store file and logger
        self.file = file
        self.logger = logger
        
        # Validate and store arguments
        self.prev_path = prev_path
        self.diff_path = diff_path
        self.pm_code_prefix = pm_code_prefix if pm_code_prefix else "PM"
        self.description_template = description_template if description_template else "{type}"
        self.revision_name = revision_name
        
        # Property filtering configuration
        if ignored_properties is None:
            self.ignored_properties = DEFAULT_IGNORED_PROPERTIES
        else:
            self.ignored_properties = ignored_properties
        
        # Will be populated during patch
        self.prev_ifc: Optional[ifcopenshell.file] = None
        self.guid_index: Dict[str, ifcopenshell.entity_instance] = {}
        self.task_mapping: Dict[int, ifcopenshell.entity_instance] = {}
        self.name_to_task: Dict[str, ifcopenshell.entity_instance] = {}
        self.work_schedule: Optional[ifcopenshell.entity_instance] = None
        self.pm_counter: int = 1
        self.element_change_details: Dict[ifcopenshell.entity_instance, str] = {}  # Element-specific descriptions
        self.pset_cache: Dict[tuple, ifcopenshell.entity_instance] = {}  # Cache for shared property sets
        
        self.logger.info(
            f"Initialized MergeTasksFromPrevious: "
            f"prev='{prev_path}', diff='{diff_path}', prefix='{self.pm_code_prefix}'"
        )
        if self.ignored_properties:
            self.logger.info(f"Ignoring property patterns: {', '.join(self.ignored_properties)}")
        else:
            self.logger.info("Tracking all property changes (no filtering)")
    
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
    
    def _get_or_create_owner_history(self) -> Optional[ifcopenshell.entity_instance]:
        """Get existing OwnerHistory or create a minimal one if needed."""
        owner_histories = self.file.by_type("IfcOwnerHistory")
        if owner_histories:
            return owner_histories[0]
        
        # Fallback: create minimal owner history for IFC2X3
        self.logger.warning("No IfcOwnerHistory found, creating minimal one")
        
        person_orgs = self.file.by_type("IfcPersonAndOrganization")
        application = self.file.by_type("IfcApplication")
        
        if not person_orgs:
            person = self.file.create_entity("IfcPerson", None, None, None)
            org = self.file.create_entity("IfcOrganization", None, "Unknown")
            person_org = self.file.create_entity("IfcPersonAndOrganization", person, org)
        else:
            person_org = person_orgs[0]
        
        if not application:
            app = self.file.create_entity("IfcApplication", 
                                         person_org.TheOrganization if hasattr(person_org, 'TheOrganization') else org if not person_orgs else person_org,
                                         "Unknown", "Unknown", "Unknown")
        else:
            app = application[0]
        
        owner_history = self.file.create_entity("IfcOwnerHistory",
                                                person_org, app, None, None, None, None, None, 0)
        return owner_history
    
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
    
    def _should_recreate_task_assignment(
        self,
        prev_element: ifcopenshell.entity_instance,
        task_name: str
    ) -> bool:
        """
        Determine if a task assignment should be recreated based on PM pset history.
        
        This validation prevents incorrect task assignments where elements get assigned
        to tasks from revisions where they weren't actually changed.
        
        Args:
            prev_element: Element from previous model
            task_name: Name of the task (e.g., "V.10", "PM3")
        
        Returns:
            True if element has valid PM data for this task (was actually changed in that revision)
            False if element has no PM data or invalid/fallback data (shouldn't have this task)
        """
        try:
            # Get element's PM pset from previous model
            prev_pset = uel.get_pset(prev_element, "PM")
            if not prev_pset:
                # No PM pset at all - skip old task assignments for this element
                # (Element might be new or from before PM tracking started)
                return False
            
            # Check if element has a property for this specific task
            if task_name not in prev_pset:
                # Element doesn't have this task in its PM pset - incorrect assignment
                return False
            
            # Check if the value is the generic fallback "changed"
            # This indicates corrupted/invalid data from previous runs
            task_value = prev_pset[task_name]
            if task_value == "changed":
                # This is the fallback value - not valid element-specific data
                # Element likely shouldn't have this task assignment
                self.logger.debug(
                    f"Skipping task {task_name} for element {prev_element.GlobalId}: "
                    f"has generic fallback value 'changed'"
                )
                return False
            
            # Element has valid, specific PM data for this task
            # (e.g., "added", "changed properties", "changed geometry", etc.)
            return True
            
        except Exception:
            # If we can't validate, be conservative and skip
            # Better to miss a valid assignment than perpetuate bad data
            return False
    
    def _recreate_all_relationships(self) -> None:
        """Recreate all task relationships from previous model."""
        self._recreate_process_assignments()
        self._recreate_sequences()
    
    def _recreate_process_assignments(self) -> None:
        """
        Recreate IfcRelAssignsToProcess relationships with validation.
        
        Only recreates task assignments where the element actually has PM pset data
        for that task, preventing incorrect assignments where elements get tasks
        from revisions where they weren't changed.
        
        References:
        - assign_process: https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.assign_process
        """
        assignments = self.prev_ifc.by_type("IfcRelAssignsToProcess")
        self.logger.info(f"Recreating {len(assignments)} process assignments (with validation)")
        
        recreated = 0
        skipped = 0
        
        for assignment in assignments:
            prev_task = assignment.RelatingProcess
            if not prev_task or prev_task.id() not in self.task_mapping:
                continue
            
            new_task = self.task_mapping[prev_task.id()]
            task_name = prev_task.Name
            
            # Map related objects to new model
            for related_obj in assignment.RelatedObjects or []:
                if not hasattr(related_obj, "GlobalId"):
                    continue
                
                target_obj = self.guid_index.get(related_obj.GlobalId)
                if not target_obj:
                    continue
                
                # ✅ NEW: Validate task assignment before recreating
                # Skip if element doesn't have PM data for this task (incorrect assignment)
                if task_name and not self._should_recreate_task_assignment(related_obj, task_name):
                    skipped += 1
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
        
        self.logger.info(
            f"Recreated {recreated} valid task assignments, "
            f"skipped {skipped} invalid assignments (self-healing)"
        )
    
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
                # Create sequence - schema-aware
                sequence_type = getattr(seq, "SequenceType", "FINISH_START")
                new_seq = self._create_sequence_relationship(
                    new_relating,
                    new_related,
                    sequence_type
                )
                
                if not new_seq:
                    continue
                
                # Add lag time if present (IFC4+ only, API handles this)
                schema = self.file.schema
                if schema != "IFC2X3" and hasattr(seq, "TimeLag") and seq.TimeLag:
                    lag_value = getattr(seq.TimeLag, "LagValue", None)
                    duration_type = getattr(seq.TimeLag, "DurationType", "WORKTIME")
                    if lag_value:
                        try:
                            ifcopenshell.api.run(
                                "sequence.assign_lag_time",
                                self.file,
                                rel_sequence=new_seq,
                                lag_value=str(lag_value),
                                duration_type=duration_type
                            )
                        except Exception as e:
                            self.logger.warning(f"Failed to assign lag time: {str(e)}")
                
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
    
    def _create_sequence_relationship(
        self,
        relating_process: ifcopenshell.entity_instance,
        related_process: ifcopenshell.entity_instance,
        sequence_type: str = "FINISH_START"
    ) -> Optional[ifcopenshell.entity_instance]:
        """
        Create a sequence relationship in a schema-aware way.
        
        For IFC4+: Uses ifcopenshell.api (TaskTime-aware)
        For IFC2X3: Manually creates IfcRelSequence entity (avoids TaskTime requirement)
        
        Args:
            relating_process: The predecessor task
            related_process: The successor task
            sequence_type: Type of sequence (default: FINISH_START)
        
        Returns:
            Created IfcRelSequence entity or None on failure
        """
        schema = self.file.schema
        
        # IFC2X3: Manual entity creation to avoid TaskTime attribute error
        if schema == "IFC2X3":
            try:
                owner_history = self._get_or_create_owner_history()
                
                rel_sequence = self.file.create_entity(
                    "IfcRelSequence",
                    ifcopenshell.guid.new(),        # GlobalId
                    owner_history,                   # OwnerHistory
                    None,                           # Name
                    None,                           # Description
                    relating_process,               # RelatingProcess
                    related_process,                # RelatedProcess
                    None,                           # TimeLag
                    sequence_type                   # SequenceType
                )
                self.logger.debug(f"Created IFC2X3 sequence: {relating_process.Name} → {related_process.Name}")
                return rel_sequence
            except Exception as e:
                self.logger.warning(f"Failed to manually create IFC2X3 sequence: {str(e)}")
                return None
        
        # IFC4+: Use API (handles TaskTime internally)
        else:
            try:
                rel_sequence = ifcopenshell.api.run(
                    "sequence.assign_sequence",
                    self.file,
                    relating_process=relating_process,
                    related_process=related_process,
                    sequence_type=sequence_type
                )
                self.logger.debug(f"Created IFC4 sequence via API: {relating_process.Name} → {related_process.Name}")
                return rel_sequence
            except Exception as e:
                self.logger.warning(f"Failed to create IFC4 sequence via API: {str(e)}")
                return None
    
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
        skipped_ignored_only = 0
        actual_added_count = 0
        actual_changed_count = 0
        
        # Process added elements
        for guid in added_list:
            element = self.guid_index.get(guid)
            if element:
                all_elements.append(element)
                actual_added_count += 1
                self.element_change_details[element] = self._get_change_description(
                    element, "added", None
                )
        
        # Process changed elements (skip if only ignored properties changed)
        for guid, metadata in changed_dict.items():
            element = self.guid_index.get(guid)
            if element:
                # Check if element has any meaningful changes
                if self._has_any_meaningful_changes(metadata):
                    all_elements.append(element)
                    actual_changed_count += 1
                    self.element_change_details[element] = self._get_change_description(
                        element, "changed", metadata
                    )
                else:
                    # Only ignored properties changed - skip this element
                    skipped_ignored_only += 1
                    self.logger.debug(
                        f"Skipping element {guid}: only ignored properties changed"
                    )
        
        # Log skipped elements
        if skipped_ignored_only > 0:
            self.logger.info(
                f"Skipped {skipped_ignored_only} elements with only ignored property changes "
                f"(timestamps, IDs, metadata)"
            )
        
        if not all_elements:
            self.logger.warning(
                f"No elements found in target model from {total_changes} changes in diff "
                f"({skipped_ignored_only} skipped as ignored-only changes)"
            )
            return
        
        self.logger.info(
            f"Found {len(all_elements)} elements with meaningful changes to assign to {pm_code} "
            f"(processed {total_changes} total changes)"
        )
        
        # Create ONE task for this entire revision (use actual counts, not raw diff counts)
        task = self._create_revision_task(pm_code, len(all_elements), actual_added_count, actual_changed_count)
        
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
            # Check for MEANINGFUL property changes only (filter out ignored patterns)
            if self._has_meaningful_property_changes(diff_metadata):
                change_details.append("properties")
            if diff_metadata.get("materials_changed"):
                change_details.append("materials")
        
        # Build description using template
        description = self.description_template.format(
            type=change_type
        )
        
        # Append details if available
        if change_details:
            description += f" {', '.join(change_details)}"
        
        return description
    
    def _parse_property_path(self, prop_path: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse IfcDiff property path to extract pset and property names.
        
        Args:
            prop_path: Path like "root['ePset_ModelInfo']['Timestamp']"
        
        Returns:
            Tuple of (pset_name, property_name) or (None, None) if can't parse
        """
        # Match pattern: root['PsetName']['PropertyName']
        match = re.match(r"root\['([^']+)'\]\['([^']+)'\]", prop_path)
        if match:
            return match.group(1), match.group(2)
        
        # Alternative pattern: just ['PropertyName'] (less common)
        match = re.match(r"root\['([^']+)'\]$", prop_path)
        if match:
            return None, match.group(1)
        
        return None, None
    
    def _should_ignore_property(self, pset_name: str, prop_name: str) -> bool:
        """
        Check if a property should be ignored based on configured patterns.
        
        Args:
            pset_name: Name of the property set (e.g., 'ePset_ModelInfo')
            prop_name: Name of the property (e.g., 'Timestamp')
        
        Returns:
            True if property should be ignored, False otherwise
        """
        if not self.ignored_properties:
            return False
        
        full_path = f"{pset_name}.{prop_name}"
        
        for pattern in self.ignored_properties:
            # Check full path match (e.g., "ePset_ModelInfo.Timestamp")
            if fnmatch.fnmatch(full_path, pattern):
                return True
            
            # Check property-only match (e.g., "*.id")
            if pattern.startswith("*.") and fnmatch.fnmatch(prop_name, pattern[2:]):
                return True
            
            # Check pset-only match (e.g., "ePset_ModelInfo.*")
            if pattern.endswith(".*") and fnmatch.fnmatch(pset_name, pattern[:-2]):
                return True
        
        return False
    
    def _should_ignore_pset(self, pset_name: str) -> bool:
        """
        Check if an entire property set should be ignored based on configured patterns.
        
        Args:
            pset_name: Name of the property set (e.g., 'ePset_ModelInfo')
        
        Returns:
            True if property set should be ignored, False otherwise
        """
        if not self.ignored_properties:
            return False
        
        for pattern in self.ignored_properties:
            # Check pset-only match (e.g., "ePset_ModelInfo.*")
            if pattern.endswith(".*") and fnmatch.fnmatch(pset_name, pattern[:-2]):
                return True
        
        return False
    
    def _has_meaningful_property_changes(self, diff_metadata: dict) -> bool:
        """
        Check if element has meaningful property changes (excluding ignored patterns).
        
        Args:
            diff_metadata: Change metadata from IfcDiff JSON
        
        Returns:
            True if there are property changes that aren't in the ignored list
        """
        if not diff_metadata or not diff_metadata.get("properties_changed"):
            return False
        
        properties_changed = diff_metadata.get("properties_changed", {})
        meaningful_changes = 0
        ignored_changes = 0
        
        # Check for added property sets/properties (dictionary_item_added)
        added_items = properties_changed.get("dictionary_item_added", [])
        for item_path in added_items:
            pset_name, prop_name = self._parse_property_path(item_path)
            
            if pset_name:
                # Check if entire pset was added or just a property
                if prop_name:
                    # Specific property added
                    if self._should_ignore_property(pset_name, prop_name):
                        ignored_changes += 1
                        self.logger.debug(f"Ignoring added property: {pset_name}.{prop_name}")
                    else:
                        meaningful_changes += 1
                else:
                    # Entire pset added - check if pset itself should be ignored
                    if self._should_ignore_pset(pset_name):
                        ignored_changes += 1
                        self.logger.debug(f"Ignoring added pset: {pset_name}")
                    else:
                        meaningful_changes += 1
            else:
                # Can't parse - assume meaningful to be safe
                meaningful_changes += 1
        
        # Check for removed property sets/properties (dictionary_item_removed)
        removed_items = properties_changed.get("dictionary_item_removed", [])
        for item_path in removed_items:
            pset_name, prop_name = self._parse_property_path(item_path)
            
            if pset_name:
                if prop_name:
                    if self._should_ignore_property(pset_name, prop_name):
                        ignored_changes += 1
                        self.logger.debug(f"Ignoring removed property: {pset_name}.{prop_name}")
                    else:
                        meaningful_changes += 1
                else:
                    if self._should_ignore_pset(pset_name):
                        ignored_changes += 1
                        self.logger.debug(f"Ignoring removed pset: {pset_name}")
                    else:
                        meaningful_changes += 1
            else:
                meaningful_changes += 1
        
        # Check for changed property values (values_changed)
        values_changed = properties_changed.get("values_changed", {})
        for prop_path in values_changed.keys():
            pset_name, prop_name = self._parse_property_path(prop_path)
            
            if pset_name and prop_name:
                if self._should_ignore_property(pset_name, prop_name):
                    ignored_changes += 1
                    self.logger.debug(f"Ignoring property change: {pset_name}.{prop_name}")
                else:
                    meaningful_changes += 1
            else:
                # Can't parse - assume meaningful to be safe
                meaningful_changes += 1
        
        if ignored_changes > 0:
            self.logger.debug(
                f"Property changes: {meaningful_changes} meaningful, {ignored_changes} ignored"
            )
        
        return meaningful_changes > 0
    
    def _has_any_meaningful_changes(self, diff_metadata: Optional[dict]) -> bool:
        """
        Check if element has ANY meaningful changes (geometry, materials, relationships, or meaningful properties).
        
        Args:
            diff_metadata: Change metadata from IfcDiff JSON (None for added elements)
        
        Returns:
            True if element has geometry changes, material changes, relationship changes, or meaningful property changes
            False if only ignored properties changed (or no metadata)
        """
        if not diff_metadata:
            # No metadata means element was added, which is meaningful
            return True
        
        # Check for geometry changes
        if diff_metadata.get("geometry_changed"):
            return True
        
        # Check for material changes
        if diff_metadata.get("materials_changed"):
            return True
        
        # Check for spatial container changes (element moved to different space/building/storey)
        if diff_metadata.get("container_changed"):
            return True
        
        # Check for aggregation relationship changes (part-of relationships)
        if diff_metadata.get("aggregate_changed"):
            return True
        
        # Check for meaningful property changes (filtered)
        if self._has_meaningful_property_changes(diff_metadata):
            return True
        
        # Only ignored properties changed (or nothing changed)
        return False
    
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
            rel_seq = self._create_sequence_relationship(
                previous_task,
                current_task,
                "FINISH_START"
            )
            if rel_seq:
                self.logger.info(f"Created sequence: {previous_pm_code} → {current_task.Name}")
            else:
                self.logger.warning(f"Failed to create sequence {previous_pm_code} → {current_task.Name}")
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
    
    def _create_pset_signature(self, pset_data: Dict[str, str]) -> tuple:
        """
        Create a hashable signature from pset data for deduplication.
        
        Args:
            pset_data: Dictionary of property names to values
        
        Returns:
            Sorted tuple of (key, value) pairs for consistent hashing
        """
        return tuple(sorted(pset_data.items()))
    
    def _rebuild_all_pm_psets(self) -> None:
        """
        Rebuild PM property sets using shared psets for space efficiency.
        
        Three-phase approach:
        1. Collect pset signatures and group elements
        2. Create unique shared psets
        3. Assign shared psets to elements
        """
        # Find all elements with task assignments
        elements_with_tasks: Set[ifcopenshell.entity_instance] = set()
        
        for rel in self.file.by_type("IfcRelAssignsToProcess"):
            for obj in rel.RelatedObjects or []:
                elements_with_tasks.add(obj)
        
        self.logger.info(f"Rebuilding PM psets for {len(elements_with_tasks)} elements")
        
        # Phase 1: Collect pset signatures and group elements
        self.logger.info("Phase 1/3: Collecting pset signatures and grouping elements")
        signature_to_elements: Dict[tuple, List[ifcopenshell.entity_instance]] = {}
        signature_to_data: Dict[tuple, Dict[str, str]] = {}
        
        processed = 0
        for i, element in enumerate(elements_with_tasks):
            if (i + 1) % 100 == 0:
                self.logger.info(f"Processing element {i + 1}/{len(elements_with_tasks)}")
            
            try:
                pset_data = self._build_pm_pset_for_element(element)
                if pset_data:
                    signature = self._create_pset_signature(pset_data)
                    signature_to_elements.setdefault(signature, []).append(element)
                    signature_to_data[signature] = pset_data
                    processed += 1
            except Exception as e:
                self.logger.warning(f"Failed to build PM pset data for element: {str(e)}")
        
        unique_psets = len(signature_to_elements)
        self.logger.info(
            f"Phase 1 complete: {processed} elements processed, "
            f"{unique_psets} unique pset combinations found"
        )
        
        # Calculate statistics
        if signature_to_elements:
            counts = [len(elems) for elems in signature_to_elements.values()]
            avg_sharing = sum(counts) / len(counts)
            min_sharing = min(counts)
            max_sharing = max(counts)
            self.logger.info(
                f"Sharing statistics: avg={avg_sharing:.1f}, min={min_sharing}, max={max_sharing} elements per pset"
            )
        
        # Phase 2: Create unique shared psets
        self.logger.info(f"Phase 2/3: Creating {unique_psets} unique shared psets")
        created = 0
        for signature, pset_data in signature_to_data.items():
            try:
                shared_pset = self._create_shared_pset(pset_data)
                self.pset_cache[signature] = shared_pset
                created += 1
            except Exception as e:
                self.logger.warning(f"Failed to create shared pset: {str(e)}")
        
        self.logger.info(f"Phase 2 complete: Created {created} shared psets")
        
        # Phase 3: Clean up old psets and assign shared psets
        self.logger.info("Phase 3/3: Cleaning up old psets and assigning shared psets")
        self._cleanup_old_pm_psets(elements_with_tasks)
        
        assigned = 0
        for signature, elements in signature_to_elements.items():
            if signature in self.pset_cache:
                try:
                    self._assign_shared_pset(self.pset_cache[signature], elements)
                    assigned += len(elements)
                except Exception as e:
                    self.logger.warning(f"Failed to assign shared pset to elements: {str(e)}")
        
        self.logger.info(
            f"Phase 3 complete: Assigned shared psets to {assigned} elements\n"
            f"Summary: {processed} elements, {unique_psets} unique psets, "
            f"{assigned} assignments"
        )
    
    def _create_shared_pset(self, pset_data: Dict[str, str]) -> ifcopenshell.entity_instance:
        """
        Create a shared IfcPropertySet with the given property data.
        
        Args:
            pset_data: Dictionary of property names to values
        
        Returns:
            The created IfcPropertySet entity
        """
        # Get OwnerHistory
        owner_history = self._get_or_create_owner_history()
        
        # Create property values for each property
        properties = []
        for key, value in pset_data.items():
            prop = self.file.create_entity(
                "IfcPropertySingleValue",
                Name=key,
                NominalValue=self.file.create_entity("IfcText", value)
            )
            properties.append(prop)
        
        # Create PropertySet with all properties
        pset = self.file.create_entity(
            "IfcPropertySet",
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=owner_history,
            Name="PM",
            HasProperties=properties
        )
        
        return pset
    
    def _cleanup_old_pm_psets(self, elements: Set[ifcopenshell.entity_instance]) -> None:
        """
        Remove old individual PM psets from elements before assigning shared ones.
        
        Args:
            elements: Set of elements to clean up
        """
        removed_rels = 0
        removed_psets = 0
        
        # Find all IfcRelDefinesByProperties relationships with PM psets
        for rel in list(self.file.by_type("IfcRelDefinesByProperties")):
            relating_def = rel.RelatingPropertyDefinition
            if not relating_def or not hasattr(relating_def, "Name"):
                continue
            
            if relating_def.Name == "PM":
                # Check if any of our elements are in this relationship
                related = list(rel.RelatedObjects or [])
                elements_to_remove = [obj for obj in related if obj in elements]
                
                if elements_to_remove:
                    # Remove these elements from the relationship
                    for elem in elements_to_remove:
                        related.remove(elem)
                    
                    if related:
                        # Still has other elements, just update the list
                        rel.RelatedObjects = related
                    else:
                        # No more elements, delete the relationship and orphaned pset
                        pset_to_remove = relating_def
                        try:
                            self.file.remove(rel)
                            removed_rels += 1
                            self.file.remove(pset_to_remove)
                            removed_psets += 1
                        except Exception as e:
                            self.logger.warning(f"Failed to remove old PM pset: {str(e)}")
        
        if removed_rels > 0 or removed_psets > 0:
            self.logger.info(f"Cleaned up {removed_rels} relationships and {removed_psets} old PM psets")
    
    def _assign_shared_pset(
        self,
        shared_pset: ifcopenshell.entity_instance,
        elements: List[ifcopenshell.entity_instance]
    ) -> None:
        """
        Assign a shared pset to multiple elements via IfcRelDefinesByProperties.
        
        Args:
            shared_pset: The shared IfcPropertySet to assign
            elements: List of elements to assign the pset to
        """
        if not elements:
            return
        
        owner_history = self._get_or_create_owner_history()
        
        # Create the relationship
        rel = self.file.create_entity(
            "IfcRelDefinesByProperties",
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=owner_history,
            RelatingPropertyDefinition=shared_pset,
            RelatedObjects=elements
        )
    
    def _build_pm_pset_for_element(self, element: ifcopenshell.entity_instance) -> Optional[Dict[str, str]]:
        """
        Build PM property set data for a single element (without creating the pset).
        
        Args:
            element: The element to build pset data for
        
        Returns:
            Dictionary of property names to values, or None if no tasks found
        """
        # Get all tasks for this element
        tasks = self._get_tasks_for_element(element)
        if not tasks:
            return None
        
        # Order tasks chronologically
        ordered_tasks = self._order_tasks_chronologically(tasks)
        if not ordered_tasks:
            return None
        
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
        
        # Revision History: comma-separated task names
        task_names = [t.Name for t in ordered_tasks if t.Name]
        if task_names:
            pset_data["Revision History"] = ", ".join(task_names)
        
        # Latest Change: latest task name
        if task_names:
            pset_data["Latest Change"] = task_names[-1]
        
        # Individual task properties: PMx = element-specific description
        # Determine the latest task for this element
        latest_task = ordered_tasks[-1] if ordered_tasks else None
        
        for task in ordered_tasks:
            if task.Name:
                # For the LATEST/CURRENT task only, use new description if available
                if task == latest_task and element in self.element_change_details:
                    description = self.element_change_details[element]
                # For previous revisions, ALWAYS preserve description from previous model's PM pset
                elif task.Name in prev_pm_data:
                    description = prev_pm_data[task.Name]
                # Fallback: This should rarely happen after task assignment validation fix
                else:
                    # If no previous data and this is current task, try element_change_details
                    if task == latest_task and element in self.element_change_details:
                        description = self.element_change_details[element]
                    else:
                        # Data inconsistency: element has task but no PM data
                        # This indicates incorrect task assignment that should be cleaned up
                        # Use generic fallback instead of task-level summary
                        self.logger.warning(
                            f"Element {element.GlobalId} has task {task.Name} but no PM data - "
                            f"possible data inconsistency"
                        )
                        description = "changed"  # Generic fallback
                
                pset_data[task.Name] = description
        
        return pset_data if pset_data else None
    
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
        
        # Fallback: sort by task time or name (with natural/numeric sorting)
        def sort_key(task):
            task_time = getattr(task, "TaskTime", None)
            if task_time:
                start = getattr(task_time, "ActualStart", None) or getattr(task_time, "ScheduleStart", None)
                if start:
                    return (0, 0, str(start))
            
            # Natural sort by extracting numeric part from task name
            # E.g., "V.6", "V.10" -> sort by 6, 10 (not string "6", "10")
            task_name = task.Name or ""
            # Try to extract number from patterns like "V.6", "PM10", etc.
            match = re.search(r'(\d+)', task_name)
            if match:
                number = int(match.group(1))
                return (1, number, task_name)
            return (1, 999999, task_name)  # Non-numeric names go last
        
        return sorted(tasks, key=sort_key)

