"""Unit tests for Bubble Merge Memory Consolidation, Quota Caps, and Memory Erasure."""

from unittest.mock import patch, MagicMock
from app.memory import graph


def test_token_similarity():
    """Verify Jaccard token similarity for conceptual overlap."""
    sim1 = graph._token_similarity("Thích xem biểu đồ tròn", "Xem dạng biểu đồ tròn theo miền")
    assert sim1 > 0.4
    
    sim2 = graph._token_similarity("Lọc chi nhánh Hà Nội", "Xem p95 latency của server")
    assert sim2 == 0.0


def test_bubble_merge_behavior_merging():
    """Verify similar behaviors merge and increment weight instead of adding new nodes."""
    fake_existing = [
        {"id": "b1", "description": "Thích xem biểu đồ tròn theo vùng miền", "category": "preference", "weight": 1}
    ]
    
    with patch.object(graph, "get_behaviors", return_value=fake_existing), \
         patch.object(graph, "_run") as mock_run:
        
        res = graph.save_or_merge_behavior("user123", "Thích biểu đồ tròn theo chi nhánh", category="preference")
        assert res["action"] == "merged"
        assert res["weight"] == 2
        assert mock_run.called


def test_bubble_merge_behavior_creation():
    """Verify new distinct behavior creates a new node."""
    fake_existing = [
        {"id": "b1", "description": "Thích xem biểu đồ tròn", "category": "preference", "weight": 1}
    ]
    
    with patch.object(graph, "get_behaviors", return_value=fake_existing), \
         patch.object(graph, "_run") as mock_run:
        
        res = graph.save_or_merge_behavior("user123", "Tính p95 latency cho máy chủ", category="habit")
        assert res["action"] == "created"
        assert res["weight"] == 1


def test_memory_erasure_by_keyword():
    """Verify natural language memory erasure deletes matching memories."""
    fake_behaviors = [
        {"id": "b1", "description": "Thích xem biểu đồ tròn", "category": "preference"},
        {"id": "b2", "description": "Tập trung vào doanh thu miền Bắc", "category": "habit"},
    ]
    fake_rules = [
        {"id": "r1", "concept_name": "Lợi Nhuận Gộp", "formula_desc": "Doanh Thu - Chiết Khấu"},
    ]
    
    with patch.object(graph, "get_behaviors", return_value=fake_behaviors), \
         patch.object(graph, "get_business_rules", return_value=fake_rules), \
         patch.object(graph, "delete_memory_by_id", return_value=True) as mock_del:
        
        deleted = graph.forget_memory_by_text("user123", "quên thói quen biểu đồ tròn đi")
        assert len(deleted) == 1
        assert "Thói quen: Thích xem biểu đồ tròn" in deleted[0]
        mock_del.assert_called_with("user123", "b1")


def test_proactive_recipe_recall():
    """Verify find_matching_recipe parses and returns the blueprint."""
    fake_row = [{
        "id": "rec_001",
        "title": "Báo cáo Hiệu năng Nginx",
        "layout": '{"kpis": [{"name": "P95 Latency"}], "charts": [{"title": "5xx Rate"}]}',
        "created_at": 1720000000,
        "sample_name": "access_log.csv"
    }]
    
    with patch.object(graph, "ENABLED", True), \
         patch.object(graph, "_run", return_value=fake_row):
        
        matched = graph.find_matching_recipe("user123", "fp_hash_123")
        assert matched is not None
        assert matched["title"] == "Báo cáo Hiệu năng Nginx"
        assert matched["layout_obj"]["kpis"][0]["name"] == "P95 Latency"
