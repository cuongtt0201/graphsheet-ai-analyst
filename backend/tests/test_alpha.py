import pandas as pd
from app.agent.alpha import InnerMonologueState, MindShift, run_alpha_cognition


def test_inner_monologue_state_recording():
    state = InnerMonologueState(
        user_intent="Tìm hiểu doanh thu quý 3",
        current_hypothesis="Doanh thu tăng trưởng đều",
    )
    
    assert state.confidence == 0.8
    assert len(state.mind_shifts) == 0
    
    shift = state.record_mind_shift(
        trigger="Lợi nhuận âm ở nhóm hàng A",
        new_hypothesis="Đào sâu vào nhóm hàng A bị lỗ",
    )
    
    assert shift.previous_hypothesis == "Doanh thu tăng trưởng đều"
    assert shift.adapted_hypothesis == "Đào sâu vào nhóm hàng A bị lỗ"
    assert state.current_hypothesis == "Đào sâu vào nhóm hàng A bị lỗ"
    assert len(state.mind_shifts) == 1
    
    summary = state.to_summary()
    assert "Ý THỨC NỘI TÂM" in summary
    assert "LỊCH SỬ BẺ LÁI" in summary
