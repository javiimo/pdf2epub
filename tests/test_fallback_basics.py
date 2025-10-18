from core.runner.fallback import (
    compute_union_area,
    compute_coverage_for_boxes,
    FallbackOptions,
    decide_page_fallback,
)
from core.runner.layout import LayoutBox


def test_compute_union_area_merges_overlaps():
    # Two overlapping 10x10 squares at (0,0) and (5,5) => union 175
    rects = [(0, 0, 10, 10), (5, 5, 10, 10)]
    assert compute_union_area(rects) == 175


def test_compute_coverage_for_boxes_simple():
    # Image 100x100, one 40x100 box => 0.40 coverage
    image_size = (100, 100)
    boxes = [LayoutBox(label="mathblock", score=1.0, x=0, y=0, width=40, height=100)]
    cov = compute_coverage_for_boxes(image_size, boxes)
    assert 0.399 <= cov <= 0.401

