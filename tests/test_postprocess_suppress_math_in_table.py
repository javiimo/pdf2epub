from core.runner.postprocess import PostprocessOptions, postprocess_math_and_tables
from core.runner.layout import LayoutBox, LayoutResult


def test_math_inside_table_is_suppressed():
    # Create a small math box fully inside a larger table box
    math = LayoutBox(label="formula", score=0.9, x=110, y=110, width=80, height=40)
    table = LayoutBox(label="table", score=0.8, x=100, y=100, width=300, height=200)
    layout = LayoutResult(image_path=None, page_index=0, boxes=(math, table))

    opts = PostprocessOptions(min_area_px=1, dpi=360, margin_pts=0.0, table_cover_threshold=0.9)
    result = postprocess_math_and_tables(layout, options=opts)

    # Only the tableblock should remain since math is fully covered
    labels = [b.label for b in result.boxes]
    assert "tableblock" in labels
    assert "mathblock" not in labels

