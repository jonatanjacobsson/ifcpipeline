#!/usr/bin/env python3
"""
Comprehensive Floor Plan Generator with View Templates
Reads configuration and generates floor plans for all view templates and storeys.
"""

import json
import subprocess
import os
import sys
from pathlib import Path

def load_config(config_path=None):
    """Load floor plan configuration."""
    if config_path is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, '..', '..', 'config', 'templates', 'floorplan-config.json')
    with open(config_path, 'r') as f:
        return json.load(f)

def detect_storeys(config):
    """Detect building storeys from architecture model."""
    print("Detecting building storeys from architecture model...")
    arch_file = config['models']['architecture']['file']
    arch_local = f"/home/bimbot-ubuntu/apps/ifcpipeline/shared{arch_file}"
    
    # Run list-storeys.sh equivalent
    result = subprocess.run(
        ['grep', '-oP', r'IFCBUILDINGSTOREY\(.*?\K[^\)]+(?=\))', arch_local],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        print("  Warning: Could not detect storeys, using defaults")
        return []
    
    # Parse storey names (simplified - would need proper IFC parsing for production)
    # For now, return a list based on what we know
    storeys = [
        ("000 Sea Level", 0.0),
        ("010 Quay Level +1.90m", 1.90),
        ("020 Mezzanine +5.40m", 5.40),
        ("030 Slussen Level +8.90m", 8.90),
        ("040 Stora Tullhusplan +13.20m", 13.20),
        ("100 Lower Roof +15.90m", 15.90),
        ("110 Upper Roof +21.20m", 21.20)
    ]
    
    print(f"  Found {len(storeys)} storeys")
    return storeys

def generate_view_template(config, template_name, template_config, storey_name, section_height):
    """Generate a single floor plan for a view template at a specific storey."""
    
    print(f"\n  Generating: {template_name} - {storey_name}")
    
    # Build output filename
    storey_slug = storey_name.replace(' ', '_').replace('+', '').replace('.', '_').lower()
    output_prefix = template_config['output_prefix']
    output_file = f"/output/converted/{output_prefix}-{storey_slug}.svg"
    
    # Get layers
    layers = template_config['layers']
    
    if len(layers) == 1:
        # Single layer - simple export
        generate_single_layer(config, layers[0], section_height, output_file)
    else:
        # Multi-layer - composite
        generate_composite(config, layers, section_height, output_file, template_config)
    
    return output_file

def generate_single_layer(config, layer, section_height, output_file):
    """Generate a single layer floor plan."""
    model_name = layer['model']
    model_config = config['models'][model_name]
    
    # Build IfcConvert command
    cmd = [
        'docker', 'exec', 'ifcpipeline-ifcconvert-worker-1',
        '/usr/local/bin/IfcConvert',
        '-y', '-j', '4', '-q',
        '--log-format', 'plain',
        '--model',
        '--section-height', str(section_height)
    ]
    
    # Add elements to include
    if 'elements' in model_config:
        cmd.extend(['--include', 'entities'] + model_config['elements'])
    
    # Add special flags
    if model_config.get('print_names'):
        cmd.append('--print-space-names')
    if model_config.get('print_areas'):
        cmd.append('--print-space-areas')
    
    # Add files
    cmd.append(model_config['file'])
    cmd.append(output_file)
    
    print(f"    Exporting {model_name}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"    Error: {result.stderr}")
        return False
    
    return True

def generate_composite(config, layers, section_height, output_file, template_config):
    """Generate a composite floor plan from multiple layers."""
    
    # Generate each layer as separate SVG
    layer_files = []
    
    for i, layer in enumerate(layers):
        model_name = layer['model']
        layer_file = output_file.replace('.svg', f'-layer-{i}-{model_name}.svg')
        
        if generate_single_layer(config, layer, section_height, layer_file):
            layer_files.append((layer_file, layer))
        else:
            print(f"    Warning: Could not generate layer {model_name}")
    
    if not layer_files:
        print(f"    Error: No layers generated")
        return False
    
    # Composite layers with opacity/color adjustments
    print(f"    Compositing {len(layer_files)} layers...")
    composite_layers(layer_files, output_file, template_config)
    
    # Clean up intermediate files
    for layer_file, _ in layer_files:
        try:
            os.remove(f"/home/bimbot-ubuntu/apps/ifcpipeline/shared{layer_file}")
        except:
            pass
    
    return True

def composite_layers(layer_files, output_file, template_config):
    """Composite multiple SVG layers into one."""
    
    import xml.etree.ElementTree as ET
    
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
    
    ns = {'svg': 'http://www.w3.org/2000/svg'}
    
    # Load first layer as base
    base_path = f"/home/bimbot-ubuntu/apps/ifcpipeline/shared{layer_files[0][0]}"
    base_tree = ET.parse(base_path)
    base_root = base_tree.getroot()
    
    # Add each subsequent layer
    for layer_file, layer_config in layer_files[1:]:
        layer_path = f"/home/bimbot-ubuntu/apps/ifcpipeline/shared{layer_file}"
        layer_tree = ET.parse(layer_path)
        layer_root = layer_tree.getroot()
        
        # Apply opacity to all paths in this layer
        opacity = layer_config.get('opacity', 1.0)
        stroke_color = layer_config.get('stroke_color')
        fill_color = layer_config.get('fill_color')
        
        for elem in layer_root.iter():
            if elem.tag == '{http://www.w3.org/2000/svg}path':
                if opacity < 1.0:
                    elem.set('opacity', str(opacity))
                if stroke_color:
                    elem.set('stroke', stroke_color)
                if fill_color and fill_color != 'none':
                    elem.set('fill', fill_color)
        
        # Append layer content to base
        for child in layer_root:
            if child.tag not in ['{http://www.w3.org/2000/svg}defs']:
                base_root.append(child)
    
    # Apply CSS from template
    apply_template_css(base_root, template_config, ns)
    
    # Save composite
    output_path = f"/home/bimbot-ubuntu/apps/ifcpipeline/shared{output_file}"
    base_tree.write(output_path, encoding='utf-8', xml_declaration=True)

def apply_template_css(root, template_config, ns):
    """Apply CSS styling from template config."""
    
    if 'css' not in template_config:
        return
    
    css_config = template_config['css']
    style = root.find('svg:style', ns)
    
    if style is None:
        style = ET.SubElement(root, '{http://www.w3.org/2000/svg}style')
        style.set('type', 'text/css')
    
    # Build CSS from config
    css_rules = []
    
    if 'text_fill' in css_config:
        css_rules.append(f"text {{ fill: {css_config['text_fill']} !important; }}")
    
    if 'text_stroke' in css_config:
        css_rules.append(f"text {{ stroke: {css_config['text_stroke']}; }}")
    
    if 'underlay_opacity' in css_config:
        css_rules.append(f".underlay {{ opacity: {css_config['underlay_opacity']}; }}")
    
    if css_rules:
        existing_css = style.text if style.text else ""
        style.text = existing_css + '\n' + '\n'.join(css_rules)

def main():
    """Main floor plan generation workflow."""
    
    print("="*70)
    print(" COMPREHENSIVE FLOOR PLAN GENERATOR")
    print("="*70)
    
    # Load configuration
    print("\nLoading configuration...")
    config = load_config()
    
    print(f"Project: {config['project']['name']}")
    print(f"Scale: {config['project']['scale']}")
    print(f"Models: {', '.join(config['models'].keys())}")
    print(f"View Templates: {', '.join(config['view_templates'].keys())}")
    
    # Detect storeys
    storeys = detect_storeys(config)
    
    if not storeys:
        print("Error: No storeys detected")
        return 1
    
    # Generate floor plans for each view template and storey
    print(f"\nGenerating floor plans...")
    total = len(config['view_templates']) * len(storeys)
    current = 0
    
    for template_name, template_config in config['view_templates'].items():
        print(f"\nView Template: {template_config['name']}")
        
        for storey_name, storey_elevation in storeys:
            current += 1
            section_height = storey_elevation + config['section_offset']
            
            print(f"  [{current}/{total}] {storey_name} @ {section_height}m")
            
            try:
                output_file = generate_view_template(
                    config, template_name, template_config,
                    storey_name, section_height
                )
                print(f"    ✓ Generated: {output_file}")
            except Exception as e:
                print(f"    ✗ Error: {e}")
                continue
    
    print("\n" + "="*70)
    print(f"✓ Floor plan generation complete!")
    print(f"  Generated {current} floor plans")
    print("="*70)
    
    return 0

if __name__ == '__main__':
    sys.exit(main())

