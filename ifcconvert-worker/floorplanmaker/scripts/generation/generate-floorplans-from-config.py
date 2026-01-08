#!/usr/bin/env python3
"""
Floor Plan Generator from View Template Configuration
Generates multiple floor plan types from a YAML configuration file.
"""

import sys
import yaml
import subprocess
from pathlib import Path
from datetime import datetime

def load_config(config_file):
    """Load and parse YAML configuration"""
    with open(config_file, 'r') as f:
        return yaml.safe_load(f)

def sanitize_filename(name):
    """Convert storey name to safe filename"""
    return (name.replace(' ', '_')
                .replace('+', '')
                .replace('.', '_')
                .replace('(', '')
                .replace(')', '')
                .lower())

def generate_summary(config, output_dir):
    """Generate markdown summary of what will be generated"""
    summary_path = output_dir / "generation_summary.md"
    
    with open(summary_path, 'w') as f:
        f.write(f"# Floor Plan Generation Summary\n\n")
        f.write(f"**Project:** {config['project']['name']}\n")
        f.write(f"**Scale:** {config['project']['scale']}\n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("---\n\n")
        f.write("## Storeys\n\n")
        
        for storey in config['storeys']:
            f.write(f"- **{storey['name']}**: {storey['elevation']}m (section @ {storey['section_height']}m)\n")
        
        f.write("\n## View Templates\n\n")
        
        for vt in config['view_templates']:
            if not vt.get('enabled', True):
                continue
            
            f.write(f"### {vt['name']}\n")
            f.write(f"**Description:** {vt['description']}\n")
            f.write(f"**Output Prefix:** `{vt['output_prefix']}`\n\n")
            
            f.write("**Layers:**\n")
            for i, layer in enumerate(vt['layers'], 1):
                layer_type = layer.get('layer_type', 'main')
                f.write(f"{i}. **{layer['model']}** [{layer_type}]\n")
                f.write(f"   - Elements: {', '.join(layer['elements'])}\n")
            f.write("\n")
    
    return summary_path

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate-floorplans-from-config.py <config.yaml>")
        sys.exit(1)
    
    config_file = Path(sys.argv[1])
    
    if not config_file.exists():
        print(f"Error: Config file not found: {config_file}")
        sys.exit(1)
    
    print("="*80)
    print(" Floor Plan Generator from View Template Configuration")
    print("="*80)
    print(f"\nConfiguration: {config_file}\n")
    
    # Load configuration
    config = load_config(config_file)
    
    # Project info
    project = config['project']
    print(f"Project: {project['name']}")
    print(f"Output: {project['output_dir']}")
    print(f"Scale: {project['scale']}")
    print()
    
    # Create output directory
    # Use SHARED_BASE env var if available, otherwise default to ../../shared
    shared_base = os.environ.get('SHARED_BASE', '../../shared')
    output_base = Path(shared_base) if not shared_base.startswith('/home/') else Path("/home/bimbot-ubuntu/apps/ifcpipeline/shared")
    output_dir = output_base / project['output_dir'].lstrip('/')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate summary
    summary_path = generate_summary(config, output_dir)
    print(f"Summary created: {summary_path}")
    print()
    
    # Count what we'll generate
    storeys = config['storeys']
    view_templates = [vt for vt in config['view_templates'] if vt.get('enabled', True)]
    
    print(f"Storeys: {len(storeys)}")
    print(f"Enabled View Templates: {len(view_templates)}")
    print(f"Total floor plans to generate: {len(storeys) * len(view_templates)}")
    print()
    print("="*80)
    print()
    
    # Process each view template
    for vt in view_templates:
        vt_name = vt['name']
        vt_prefix = vt['output_prefix']
        
        print(f"━"*80)
        print(f"📋 View Template: {vt_name}")
        print(f"━"*80)
        print(f"Description: {vt['description']}")
        print(f"Output Prefix: {vt_prefix}")
        print()
        
        # Show layers
        print("Layers:")
        for i, layer in enumerate(vt['layers'], 1):
            layer_model = layer['model']
            layer_type = layer.get('layer_type', 'main')
            elements = ', '.join(layer['elements'][:3])
            if len(layer['elements']) > 3:
                elements += f" (+{len(layer['elements'])-3} more)"
            
            print(f"  {i}. {layer_model} [{layer_type}]")
            print(f"     Elements: {elements}")
        print()
        
        # Generate for each storey
        for storey in storeys:
            storey_name = storey['name']
            section_height = storey['section_height']
            
            storey_filename = sanitize_filename(storey_name)
            output_filename = f"{vt_prefix}_{storey_filename}.svg"
            output_path = output_dir / output_filename
            
            print(f"  📐 {storey_name} (section @ {section_height}m)")
            print(f"     → {output_filename}")
            
            # TODO: Call the multi-layer generator here
            print(f"     [Multi-layer generator would be called here]")
            print()
        
        print()
    
    print("="*80)
    print("✓ Configuration parsed successfully")
    print("="*80)
    print()
    print(f"Next step: Implement the multi-layer floor plan generator")
    print(f"Summary: {summary_path}")
    print()

if __name__ == "__main__":
    main()

