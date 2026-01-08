#!/usr/bin/env python3
"""
Floor Plan Configuration Parser

Reads and parses floorplan-config.yaml to extract:
- Model configurations with section heights
- View template definitions
- Storey information
- Processing options

Usage:
    from config_parser import FloorPlanConfig
    
    config = FloorPlanConfig('floorplan-config.yaml')
    
    # Get model info
    model = config.get_model('electrical')
    section_height = config.calculate_section_height('electrical', 5.40, 'ceiling_level')
    
    # Get view template
    template = config.get_view_template('coordinated_all')
    layers = template['layers']
"""

import yaml
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

class FloorPlanConfig:
    """Parser for floor plan configuration YAML"""
    
    def __init__(self, config_path: str = 'floorplan-config.yaml'):
        """Load and parse configuration file"""
        self.config_path = Path(config_path)
        
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        with open(self.config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        # Extract main sections
        self.project = self.config.get('project', {})
        self.models = self.config.get('models', {})
        self.storeys = self.config.get('storeys', [])
        self.view_templates = self.config.get('view_templates', {})
        self.css_templates = self.config.get('css_templates', {})
        self.export_options = self.config.get('export', {})
        self.processing = self.config.get('processing', {})
    
    def get_model(self, model_name: str) -> Optional[Dict]:
        """
        Get model configuration by name.
        
        Args:
            model_name: Name of model (e.g., 'architecture', 'electrical')
        
        Returns:
            Dictionary with model configuration or None if not found
        """
        return self.models.get(model_name)
    
    def get_model_file(self, model_name: str) -> Optional[str]:
        """Get IFC file path for a model"""
        model = self.get_model(model_name)
        return model.get('file') if model else None
    
    def get_model_elements(self, model_name: str) -> List[str]:
        """Get list of IFC element types for a model"""
        model = self.get_model(model_name)
        return model.get('elements', []) if model else []
    
    def get_model_section_heights(self, model_name: str) -> Optional[Dict]:
        """
        Get section height configuration for a model.
        
        Returns:
            Dictionary with keys: default_offset, floor_level, ceiling_level, view_direction
        """
        model = self.get_model(model_name)
        return model.get('section_heights', {}) if model else {}
    
    def calculate_section_height(
        self, 
        model_name: str, 
        storey_elevation: float, 
        offset_type: str = 'default_offset'
    ) -> float:
        """
        Calculate absolute section height for a model at a given storey.
        
        Args:
            model_name: Name of model (e.g., 'electrical')
            storey_elevation: Storey elevation in meters (e.g., 5.40)
            offset_type: Type of offset to use:
                - 'default_offset'
                - 'floor_level'
                - 'ceiling_level'
        
        Returns:
            Absolute section height in meters
        
        Example:
            >>> config.calculate_section_height('electrical', 5.40, 'ceiling_level')
            8.2  # 5.40 + 2.8
            >>> config.calculate_section_height('electrical_ceiling', 5.40, 'ceiling_level')
            8.8  # 8.90 - 0.1 (next storey minus 10cm)
        """
        section_heights = self.get_model_section_heights(model_name)
        
        if not section_heights:
            # Default to 1.2m offset if no config found
            print(f"Warning: No section_heights config for {model_name}, using default 1.2m", 
                  file=sys.stderr)
            return storey_elevation + 1.2
        
        offset = section_heights.get(offset_type, section_heights.get('default_offset', 1.2))
        
        # Handle dynamic ceiling heights (10cm below next storey)
        if offset == "next_storey_minus_0.1":
            next_storey_elevation = self._get_next_storey_elevation(storey_elevation)
            if next_storey_elevation is not None:
                return next_storey_elevation - 0.1
            else:
                # For top floor, use current storey + 3.0m - 0.1m
                print(f"Warning: No next storey found for {storey_elevation}m, using +2.9m", 
                      file=sys.stderr)
                return storey_elevation + 2.9
        
        # Handle ceiling mid-range (halfway between floor 1.2m and ceiling)
        if offset == "ceiling_mid_range":
            next_storey_elevation = self._get_next_storey_elevation(storey_elevation)
            if next_storey_elevation is not None:
                floor_height = storey_elevation + 1.2  # Floor level
                ceiling_height = next_storey_elevation - 0.1  # Ceiling level
                mid_height = (floor_height + ceiling_height) / 2
                return mid_height
            else:
                # For top floor, use current storey + 2.0m (mid-point of assumed 3.8m ceiling)
                print(f"Warning: No next storey found for {storey_elevation}m, using +2.0m (mid-ceiling)", 
                      file=sys.stderr)
                return storey_elevation + 2.0
        
        # Handle numeric offsets
        if isinstance(offset, (int, float)):
            return storey_elevation + offset
        
        # Handle string offsets (fallback)
        try:
            return storey_elevation + float(offset)
        except (ValueError, TypeError):
            print(f"Warning: Invalid offset '{offset}' for {model_name}, using default 1.2m", 
                  file=sys.stderr)
            return storey_elevation + 1.2
    
    def _get_next_storey_elevation(self, current_elevation: float) -> Optional[float]:
        """
        Get the elevation of the next storey above the current one.
        
        Args:
            current_elevation: Current storey elevation in meters
            
        Returns:
            Next storey elevation or None if this is the top floor
        """
        # Sort storeys by elevation
        sorted_storeys = sorted(self.storeys, key=lambda s: s['elevation'])
        
        # Find next storey
        for storey in sorted_storeys:
            if storey['elevation'] > current_elevation:
                return storey['elevation']
        
        return None  # This is the top floor
    
    def get_view_template(self, template_name: str) -> Optional[Dict]:
        """
        Get view template configuration by name.
        
        Args:
            template_name: Name of template (e.g., 'coordinated_all', 'electrical')
        
        Returns:
            Dictionary with template configuration including layers
        """
        return self.view_templates.get(template_name)
    
    def get_view_template_layers(self, template_name: str) -> List[Dict]:
        """Get list of layers for a view template"""
        template = self.get_view_template(template_name)
        return template.get('layers', []) if template else []
    
    def get_storey_by_name(self, storey_name: str) -> Optional[Dict]:
        """Get storey configuration by name"""
        for storey in self.storeys:
            if storey['name'] == storey_name:
                return storey
        return None
    
    def get_storey_elevation(self, storey_name: str) -> Optional[float]:
        """Get elevation for a storey"""
        storey = self.get_storey_by_name(storey_name)
        return storey.get('elevation') if storey else None
    
    def get_all_storeys(self) -> List[Dict]:
        """Get list of all storeys"""
        return self.storeys
    
    def get_enabled_view_templates(self) -> Dict[str, Dict]:
        """Get all enabled view templates"""
        enabled = {}
        for name, template in self.view_templates.items():
            if template.get('enabled', True):
                enabled[name] = template
        return enabled
    
    def get_output_filename(
        self, 
        template_name: str, 
        storey_name: str,
        extension: str = 'svg'
    ) -> str:
        """
        Generate output filename for a floor plan.
        
        Args:
            template_name: View template name
            storey_name: Storey name
            extension: File extension (default: 'svg')
        
        Returns:
            Filename string (e.g., 'coord_all_020_mezzanine_5_40m.svg')
        """
        template = self.get_view_template(template_name)
        if not template:
            raise ValueError(f"Template not found: {template_name}")
        
        prefix = template.get('output_prefix', template_name)
        
        # Sanitize storey name for filename
        storey_safe = storey_name.lower()
        storey_safe = storey_safe.replace(' ', '_')
        storey_safe = storey_safe.replace('+', '')
        storey_safe = storey_safe.replace('.', '_')
        storey_safe = storey_safe.replace(',', '_')
        storey_safe = storey_safe.replace('(', '').replace(')', '')
        
        return f"{prefix}_{storey_safe}.{extension}"
    
    def get_ifcconvert_command_base(self) -> List[str]:
        """Get base IfcConvert command with common flags"""
        threads = self.processing.get('ifcconvert_threads', 8)
        container = self.processing.get('docker_container', 'ifcpipeline-ifcconvert-worker-1')
        ifcconvert_path = self.processing.get('ifcconvert_path', '/usr/local/bin/IfcConvert')
        
        return [
            'docker', 'exec', container,
            ifcconvert_path,
            '-y',  # Yes to all prompts
            '-j', str(threads),  # Parallel threads
            '-q',  # Quiet mode
        ]
    
    def get_coordinate_scale(self) -> int:
        """Get coordinate scaling factor (e.g., 20 for 1:50 scale)"""
        return self.project.get('coordinate_scale', 20)
    
    def get_canvas_size(self) -> int:
        """Get target canvas size in pixels"""
        return self.export_options.get('canvas_size', 2048)
    
    def get_output_dir(self) -> str:
        """Get output directory path"""
        return self.project.get('output_dir', '/output/converted/floorplans')
    
    def get_temp_dir(self) -> str:
        """Get temporary directory path"""
        return self.processing.get('temp_dir', '/output/converted/temp')
    
    def print_summary(self):
        """Print configuration summary"""
        print("=" * 70)
        print(f"Floor Plan Configuration: {self.config_path.name}")
        print("=" * 70)
        print(f"\nProject: {self.project.get('name')}")
        print(f"Scale: {self.project.get('scale')}")
        print(f"Output: {self.get_output_dir()}")
        
        print(f"\nModels:")
        for name, model in self.models.items():
            file_path = model.get('file', 'N/A')
            print(f"  • {name:15} {Path(file_path).name}")
            section_heights = model.get('section_heights', {})
            if section_heights:
                print(f"    └─ Section heights: default={section_heights.get('default_offset')}m, "
                      f"floor={section_heights.get('floor_level')}m, "
                      f"ceiling={section_heights.get('ceiling_level')}m")
        
        print(f"\nStoreys: {len(self.storeys)}")
        for storey in self.storeys:
            print(f"  • {storey['name']:30} @ {storey['elevation']:.2f}m")
        
        print(f"\nView Templates: {len(self.view_templates)}")
        for name, template in self.view_templates.items():
            enabled = "✓" if template.get('enabled', True) else "✗"
            layers = len(template.get('layers', []))
            print(f"  {enabled} {name:20} ({layers} layers) → {template.get('output_prefix')}_*.svg")
        
        print("=" * 70)


def main():
    """CLI tool for config inspection"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Floor Plan Config Parser')
    parser.add_argument('config', nargs='?', default='floorplan-config.yaml',
                        help='Path to config file')
    parser.add_argument('--summary', action='store_true',
                        help='Print configuration summary')
    parser.add_argument('--model', help='Get info for specific model')
    parser.add_argument('--template', help='Get info for specific view template')
    parser.add_argument('--section-height', nargs=3, metavar=('MODEL', 'ELEVATION', 'TYPE'),
                        help='Calculate section height (e.g., electrical 5.40 ceiling_level)')
    
    args = parser.parse_args()
    
    try:
        config = FloorPlanConfig(args.config)
        
        if args.summary:
            config.print_summary()
        
        elif args.model:
            model = config.get_model(args.model)
            if model:
                print(yaml.dump({args.model: model}, default_flow_style=False))
            else:
                print(f"Model not found: {args.model}", file=sys.stderr)
                sys.exit(1)
        
        elif args.template:
            template = config.get_view_template(args.template)
            if template:
                print(yaml.dump({args.template: template}, default_flow_style=False))
            else:
                print(f"Template not found: {args.template}", file=sys.stderr)
                sys.exit(1)
        
        elif args.section_height:
            model_name, elevation_str, offset_type = args.section_height
            elevation = float(elevation_str)
            section_height = config.calculate_section_height(model_name, elevation, offset_type)
            print(f"{section_height:.2f}")
        
        else:
            # Default: print summary
            config.print_summary()
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

