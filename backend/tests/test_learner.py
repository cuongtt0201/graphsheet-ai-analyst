"""Unit tests for Autonomous Memory Learner (Pre-filtering, Harvest, & Graph Persistence)."""

from unittest.mock import MagicMock, patch
import time

from app.memory import learner, graph


def test_should_harvest_memory_positive_cases():
    """Verify explicit business rules and preferences trigger the heuristic filter."""
    positive_prompts = [
        "Bên tôi quy ước doanh thu thuần tính bằng doanh thu trừ đi chiết khấu.",
        "Công thức tính churn rate của phòng ban này là gì?",
        "Định nghĩa: Khách hàng active là khách có giao dịch trong 30 ngày qua.",
        "Từ nay nhớ là loại trừ các đơn hàng trạng thái CANCELLED.",
        "Lưu ý là không tính vào doanh số các sản phẩm dùng thử.",
        "Bên cty mình luôn luôn ưu tiên vẽ biểu đồ đường cho số liệu thời gian.",
        "Tôi thích xem biểu đồ cột ngang khi so sánh chi nhánh.",
    ]
    for prompt in positive_prompts:
        assert learner.should_harvest_memory(prompt) is True, f"Failed to match positive prompt: {prompt}"


def test_should_harvest_memory_negative_cases():
    """Verify ordinary analytical questions with words like 'trừ', 'nhé' do NOT trigger the filter."""
    negative_prompts = [
        "Tính tổng doanh thu tháng 8 trừ chiết khấu nhé",
        "Cho mình xem top 10 sản phẩm bán chạy nhất nhé",
        "Có bao nhiêu đơn hàng được tạo trong ngày hôm qua?",
        "Hãy xuất bảng dữ liệu các khách hàng ở Hà Nội",
        "Lọc ra danh sách 5 nhân viên có doanh số cao nhất",
    ]
    for prompt in negative_prompts:
        assert learner.should_harvest_memory(prompt) is False, f"False positive on negative prompt: {prompt}"


def test_harvest_memory_sync_saves_rules_and_behaviors():
    """Verify harvest_memory_sync calls graph.save_or_merge_business_rule and graph.save_or_merge_behavior."""
    mock_ai_response = {
        "has_learned_knowledge": True,
        "business_rules": [
            {
                "concept_name": "Doanh Thu Thuần",
                "formula_desc": "Doanh thu trừ đi tiền hoàn và chiết khấu",
                "target_columns": ["revenue", "discount", "refund"],
            }
        ],
        "behaviors": [
            {
                "description": "Ưu tiên vẽ biểu đồ cột cho dữ liệu doanh thu",
                "category": "preference",
            }
        ],
    }

    user_prompt = "Bên tôi quy ước Doanh thu thuần = Doanh thu - tiền hoàn - chiết khấu"
    assistant_reply = "Đã hiểu quy ước tính Doanh thu thuần của bạn."

    with patch("app.ai.pool.call_ai", return_value=mock_ai_response), \
         patch.object(graph, "save_or_merge_business_rule", return_value={"action": "created", "id": "r1"}) as mock_save_rule, \
         patch.object(graph, "save_or_merge_behavior", return_value={"action": "created", "id": "b1"}) as mock_save_behavior:

        result = learner.harvest_memory_sync(
            user_id="usr_test_001",
            user_prompt=user_prompt,
            assistant_reply=assistant_reply,
        )

        assert result["learned"] is True
        assert result["rules"] == ["Doanh Thu Thuần"]
        assert result["behaviors"] == ["Ưu tiên vẽ biểu đồ cột cho dữ liệu doanh thu"]

        mock_save_rule.assert_called_once_with(
            user_id="usr_test_001",
            concept_name="Doanh Thu Thuần",
            formula_desc="Doanh thu trừ đi tiền hoàn và chiết khấu",
            target_columns=["revenue", "discount", "refund"],
        )

        mock_save_behavior.assert_called_once_with(
            user_id="usr_test_001",
            description="Ưu tiên vẽ biểu đồ cột cho dữ liệu doanh thu",
            category="preference",
        )


def test_harvest_memory_sync_no_knowledge():
    """Verify that when AI detects no knowledge, nothing is saved."""
    mock_ai_response = {
        "has_learned_knowledge": False,
        "business_rules": [],
        "behaviors": [],
    }

    with patch("app.ai.pool.call_ai", return_value=mock_ai_response), \
         patch.object(graph, "save_or_merge_business_rule") as mock_save_rule, \
         patch.object(graph, "save_or_merge_behavior") as mock_save_behavior:

        result = learner.harvest_memory_sync(
            user_id="usr_test_001",
            user_prompt="Bên mình có cần lưu ý gì không?",
            assistant_reply="Không có lưu ý gì đặc biệt.",
        )

        assert result["learned"] is False
        mock_save_rule.assert_not_called()
        mock_save_behavior.assert_not_called()


def test_harvest_memory_sync_handles_exceptions():
    """Verify exceptions are caught gracefully and logged."""
    with patch("app.ai.pool.call_ai", side_effect=RuntimeError("Database connection lost")):
        result = learner.harvest_memory_sync(
            user_id="usr_test_001",
            user_prompt="Quy ước tính doanh thu thuần",
            assistant_reply="Ok",
        )

        assert result["learned"] is False
        assert "Database connection lost" in result["error"]


def test_harvest_memory_async_dispatch():
    """Verify asynchronous background thread dispatches without blocking."""
    with patch("app.memory.learner.harvest_memory_sync", return_value={"learned": True}) as mock_sync:
        learner.harvest_memory_async(
            user_id="usr_test_001",
            user_prompt="Công thức tính doanh thu",
            assistant_reply="Đã ghi nhận",
        )
        # Give thread a brief moment to execute
        time.sleep(0.1)
        assert mock_sync.called
