#!/usr/bin/env python3
"""Test script to verify the header/footer detection bug fix."""

import sys
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.configuration import TabConfiguration
from core.runner.epub import run_epub, _is_header_footer_detection_bug
from core.runner.temp_manager import TemporaryWorkspace

def test_header_footer_detection():
    """Test that the header/footer detection bug is properly identified."""
    # Test with the actual error from the user
    error_stderr = """Traceback (most recent call last):
  File "runpy.py", line 198, in _run_module_as_main
  File "runpy.py", line 88, in _run_code
  File "site.py", line 47, in <module>
  File "site.py", line 43, in main
  File "calibre/ebooks/conversion/cli.py", line 429, in main
  File "calibre/ebooks/conversion/plumber.py", line 1089, in run
  File "calibre/customize/conversion.py", line 242, in __call__
  File "calibre/ebooks/conversion/plugins/pdf_input.py", line 66, in convert
  File "calibre/ebooks/pdf/reflow.py", line 1477, in __init__
  File "calibre/ebooks/pdf/reflow.py", line 1878, in find_header_footer
  IndexError: list index out of range"""
    
    assert _is_header_footer_detection_bug(error_stderr), "Should detect the header/footer bug"
    print("✓ Header/footer detection bug correctly identified")

def test_conversion_with_fallback():
    """Test the conversion with the enhanced fallback mechanism."""
    # Create a minimal configuration
    config = TabConfiguration(
        tab_id="test-tab",
        title="Test Configuration",
        input_pdf=Path("example.pdf"),
        output_epub=Path("test_output.epub"),
        page_range="1-2",
        options={
            "base_font_size": "12",
            "font_size_mapping": "8,9,10,11,12,13,14,16",
            "change_justification": "left",
            "output_profile": "kobo",
            "minimum_line_height": "1.3",
        }
    )
    
    try:
        result = run_epub(
            config,
            Path("test_output.epub"),
            workspace_factory=TemporaryWorkspace,
        )
        print(f"✓ Conversion successful: {result.target}")
        return True
    except Exception as e:
        print(f"✗ Conversion failed: {e}")
        # Print more details if it's a ConversionError
        if hasattr(e, 'stderr') and e.stderr:
            print("STDERR:")
            print(e.stderr[-2000:])  # Last 2000 chars
        if hasattr(e, 'stdout') and e.stdout:
            print("STDOUT:")
            print(e.stdout[-2000:])  # Last 2000 chars
        if hasattr(e, 'command') and e.command:
            print("COMMAND:")
            print(' '.join(e.command))
        return False

if __name__ == "__main__":
    print("Testing header/footer detection bug fix...")
    test_header_footer_detection()
    
    print("\nTesting conversion with enhanced fallback...")
    success = test_conversion_with_fallback()
    
    if success:
        print("\n✓ All tests passed! The fix should resolve the issue.")
    else:
        print("\n✗ Tests failed. The issue may persist.")