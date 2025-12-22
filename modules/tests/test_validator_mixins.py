"""
Tests for the mixin-based AlphaCore validator implementation.
"""

import pytest
from datetime import datetime
from pathlib import Path
from uuid import uuid4

pytest.importorskip("bittensor")

# Test imports
from subnet.validator.generation import TaskGenerationMixin
from subnet.validator.dispatch import TaskDispatchMixin
from subnet.validator.evaluation import TaskEvaluationMixin
from subnet.validator.round_manager import RoundManager, RoundPhase, RoundState
from subnet.validator.checkpoint import CheckpointManager
from modules.models import ACTaskSpec


class TestRoundManager:
    """Test RoundManager phase tracking."""

    def test_round_start(self):
        """Test starting a round."""
        manager = RoundManager()
        round_id = str(uuid4())
        
        round_state = manager.start_round(round_id, current_block=1000)
        
        assert round_state.round_id == round_id
        assert round_state.phase == RoundPhase.PREPARING
        assert round_state.start_block == 1000
        assert round_state.end_block == 1100  # 1000 + default 100

    def test_phase_transition(self):
        """Test transitioning between phases."""
        manager = RoundManager()
        round_state = manager.start_round(str(uuid4()), current_block=1000)
        
        manager.transition_phase(RoundPhase.GENERATION)
        assert manager.current_round.phase == RoundPhase.GENERATION
        
        manager.transition_phase(RoundPhase.HANDSHAKE)
        assert manager.current_round.phase == RoundPhase.HANDSHAKE
        
        manager.transition_phase(RoundPhase.CONSENSUS)
        assert manager.current_round.phase == RoundPhase.CONSENSUS

    def test_round_completion(self):
        """Test completing a round."""
        manager = RoundManager()
        round_id = str(uuid4())
        round_state = manager.start_round(round_id, current_block=1000)
        
        manager.transition_phase(RoundPhase.GENERATION)
        manager.transition_phase(RoundPhase.HANDSHAKE)
        
        completed = manager.finish_round()
        
        assert completed.phase == RoundPhase.COMPLETE
        assert round_id in manager.previous_rounds
        assert manager.current_round is None

    def test_round_duration(self):
        """Test duration tracking."""
        manager = RoundManager()
        round_state = manager.start_round(str(uuid4()), current_block=1000)
        
        import time
        time.sleep(0.1)
        
        duration = round_state.duration_seconds()
        assert duration >= 0.1

    def test_round_status(self):
        """Test round status reporting."""
        manager = RoundManager()
        round_id = str(uuid4())
        manager.start_round(round_id, current_block=1000)
        
        status = manager.get_round_status()
        assert status["round_id"] == round_id
        assert status["phase"] == "preparing"


class TestCheckpointManager:
    """Test CheckpointManager persistence."""

    def test_checkpoint_dir_creation(self, tmp_path):
        """Test checkpoint directory creation."""
        manager = CheckpointManager(checkpoint_dir=tmp_path)
        assert manager.checkpoint_dir.exists()

    def test_save_round_state(self, tmp_path):
        """Test saving round state."""
        manager = CheckpointManager(checkpoint_dir=tmp_path)
        round_id = str(uuid4())
        
        round_state = RoundState(
            round_id=round_id,
            phase=RoundPhase.GENERATION,
            start_block=1000,
            end_block=1100,
        )
        
        path = manager.save_round_state(round_state)
        assert path.exists()
        assert (tmp_path / round_id / "validator_state.json").exists()

    def test_load_round_state(self, tmp_path):
        """Test loading round state."""
        manager = CheckpointManager(checkpoint_dir=tmp_path)
        round_id = str(uuid4())
        
        # Save
        round_state = RoundState(
            round_id=round_id,
            phase=RoundPhase.CONSENSUS,
            start_block=1000,
            end_block=1100,
        )
        manager.save_round_state(round_state)
        
        # Load
        loaded = manager.load_round_state(round_id)
        assert loaded is not None
        assert loaded.round_id == round_id
        assert loaded.phase == RoundPhase.CONSENSUS
        assert loaded.start_block == 1000

    def test_save_tasks(self, tmp_path):
        """Test saving tasks."""
        manager = CheckpointManager(checkpoint_dir=tmp_path)
        round_id = str(uuid4())
        
        # Create mock tasks
        tasks = [
            ACTaskSpec(
                task_id=str(uuid4()),
                provider="gcp",
                kind="terraform",
                params={},
                prompt="Test task",
            )
            for _ in range(3)
        ]
        
        path = manager.save_tasks(round_id, tasks)
        assert path.exists()

    def test_save_scores(self, tmp_path):
        """Test saving scores."""
        manager = CheckpointManager(checkpoint_dir=tmp_path)
        round_id = str(uuid4())
        
        scores = {0: 0.95, 1: 0.87, 2: 0.76}
        
        path = manager.save_scores(round_id, scores)
        assert path.exists()

    def test_round_exists(self, tmp_path):
        """Test checking if round exists."""
        manager = CheckpointManager(checkpoint_dir=tmp_path)
        round_id = str(uuid4())
        
        assert not manager.round_exists(round_id)
        
        round_state = RoundState(round_id=round_id)
        manager.save_round_state(round_state)
        
        assert manager.round_exists(round_id)

    def test_get_last_round_id(self, tmp_path):
        """Test getting last round ID."""
        manager = CheckpointManager(checkpoint_dir=tmp_path)
        
        assert manager.get_last_round_id() is None
        
        # Save multiple rounds
        for i in range(3):
            round_id = f"round-{i}"
            round_state = RoundState(round_id=round_id)
            manager.save_round_state(round_state)
        
        last_id = manager.get_last_round_id()
        assert last_id is not None


class TestTaskGenerationMixin:
    """Test TaskGenerationMixin."""

    def test_mixin_initialization(self):
        """Test mixin initialization."""
        mixin = TaskGenerationMixin()
        assert mixin._current_round_id is None
        assert mixin._current_round_tasks == []
        assert mixin._generation_pipeline is None

    def test_get_current_round_id(self):
        """Test getting round ID."""
        mixin = TaskGenerationMixin()
        round_id = mixin.get_current_round_id()
        assert round_id is not None
        assert isinstance(round_id, str)

    def test_clear_round_tasks(self):
        """Test clearing round tasks."""
        mixin = TaskGenerationMixin()
        mixin._current_round_tasks = [ACTaskSpec(
            task_id="test",
            provider="gcp",
            kind="terraform",
            params={},
            prompt="test",
        )]
        
        mixin.clear_round_tasks()
        assert mixin._current_round_tasks == []


class TestTaskDispatchMixin:
    """Test TaskDispatchMixin."""

    def test_mixin_initialization(self):
        """Test mixin initialization."""
        mixin = TaskDispatchMixin()
        assert mixin._dispatcher_start is None
        assert mixin._task_responses == {}

    def test_get_task_responses(self):
        """Test getting task responses."""
        mixin = TaskDispatchMixin()
        mixin.get_current_round_id = lambda: "test-round"
        
        responses = mixin.get_task_responses()
        assert responses == {}

    def test_clear_task_responses(self):
        """Test clearing task responses."""
        mixin = TaskDispatchMixin()
        mixin.get_current_round_id = lambda: "test-round"
        
        mixin._task_responses["test-round"] = {0: "response"}
        mixin.clear_task_responses()
        
        assert mixin.get_task_responses() == {}


class TestTaskEvaluationMixin:
    """Test TaskEvaluationMixin."""

    def test_mixin_initialization(self):
        """Test mixin initialization."""
        mixin = TaskEvaluationMixin()
        assert mixin._evaluator is None
        assert mixin._evaluation_start is None
        assert mixin._scores_by_round == {}

    def test_get_scores(self):
        """Test getting scores."""
        mixin = TaskEvaluationMixin()
        mixin.get_current_round_id = lambda: "test-round"
        
        scores = mixin.get_scores()
        assert scores == {}

    def test_clear_scores(self):
        """Test clearing scores."""
        mixin = TaskEvaluationMixin()
        mixin.get_current_round_id = lambda: "test-round"
        
        mixin._scores_by_round["test-round"] = {0: 0.95}
        mixin.clear_scores()
        
        assert mixin.get_scores() == {}


@pytest.mark.skip(reason="Requires Bittensor setup")
class TestValidator:
    """Integration tests for Validator."""

    def test_validator_initialization(self):
        """Test validator initialization."""
        from neurons.validator import Validator
        
        # This requires Bittensor config and wallet
        pass

    def test_validator_run_cycle(self):
        """Test complete validator cycle."""
        # This requires full Bittensor setup and dendrite
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
