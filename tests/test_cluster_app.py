"""
Tests for cluster_app.py that use local helper logic rather than real imports.

Covers:
- Manual region definition workflow (MockAppState + extracted logic helpers)
- Edge cases for manual regions
- Renewables clustering logic helpers
- YAML region parsing logic

NOTE: Real-import tests (color utils, graph algorithms, clustering, ESR policy,
plant clustering, etc.) live in test_cluster_app_algorithms.py.
"""

import math
import re

import numpy as np
import pandas as pd
import pytest
import yaml

# ============================================================================
# Manual Region Definition Tests
# ============================================================================


class MockAppState:
    """Mock AppState class for testing manual region functionality."""

    def __init__(self):
        self.is_manual_mode = False
        self.manual_regions = {}
        self.selected_manual_region = None
        self.selected_bas = set()
        self.cluster_colors = {}
        self.ba_to_region = {}
        self.is_clustered = False
        self.region_aggregations = None


def add_manual_region_logic(state, region_name):
    """Logic for adding a manual region (without DOM dependencies)."""
    # Strip whitespace first
    region_name = region_name.strip() if region_name else ""

    if not region_name:
        return False, "Please enter a region name"

    if region_name in state.manual_regions:
        return False, f"Region '{region_name}' already exists"

    state.manual_regions[region_name] = []
    state.selected_manual_region = region_name
    return True, f"Region '{region_name}' created"


def assign_bas_logic(state, bas_to_assign):
    """Logic for assigning BAs to a region (without DOM dependencies)."""
    if not state.selected_manual_region:
        return False, "Please select a region first"

    if not bas_to_assign:
        return False, "Please select BAs on the map first"

    # Get BAs that are not already assigned
    assigned_bas = set()
    for bas in state.manual_regions.values():
        assigned_bas.update(bas)

    new_bas = bas_to_assign - assigned_bas
    if not new_bas:
        return False, "All selected BAs are already assigned to regions"

    # Assign BAs to selected region
    state.manual_regions[state.selected_manual_region].extend(new_bas)
    return (
        True,
        f"Assigned {len(new_bas)} BAs to region '{state.selected_manual_region}'",
    )


def finalize_manual_logic(state):
    """Logic for finalizing manual regions (without DOM dependencies)."""
    if not state.manual_regions:
        return False, "Please define at least one region"

    # Check that all regions have BAs
    empty_regions = [name for name, bas in state.manual_regions.items() if not bas]
    if empty_regions:
        return False, f"Regions {empty_regions} have no BAs assigned"

    # Convert to region_aggregations format
    state.region_aggregations = {
        name: list(bas) for name, bas in state.manual_regions.items()
    }
    state.is_clustered = True

    # Build ba_to_region mapping
    state.ba_to_region = {}
    for region_name, bas in state.region_aggregations.items():
        for ba in bas:
            state.ba_to_region[ba] = region_name

    num_regions = len(state.region_aggregations)
    total_bas = sum(len(bas) for bas in state.region_aggregations.values())
    return (
        True,
        f"Manual regions finalized! {num_regions} regions created with {total_bas} BAs.",
    )


def clear_manual_regions_logic(state):
    """Logic for clearing manual regions (without DOM dependencies)."""
    state.manual_regions = {}
    state.selected_manual_region = None
    state.cluster_colors = {}
    state.ba_to_region = {}
    state.is_clustered = False
    state.region_aggregations = None


def select_manual_region_logic(state, region_name):
    """Logic for selecting a manual region (without DOM dependencies)."""
    state.selected_manual_region = region_name


def remove_manual_region_logic(state, region_name):
    """Logic for removing a manual region (without DOM dependencies)."""
    if region_name in state.manual_regions:
        del state.manual_regions[region_name]
        if state.selected_manual_region == region_name:
            state.selected_manual_region = None
        return True, f"Region '{region_name}' removed"
    return False, f"Region '{region_name}' not found"


def on_region_mode_change_logic(state, is_manual):
    """Logic for switching between clustering and manual modes (without DOM dependencies)."""
    state.is_manual_mode = is_manual
    if is_manual:
        # Clear any existing clustering results
        state.cluster_colors = {}
        state.ba_to_region = {}
        state.is_clustered = False
        state.region_aggregations = None
    else:
        # Clear manual regions when switching back
        state.manual_regions = {}
        state.selected_manual_region = None


class TestManualRegionCreation:
    """Test manual region creation functionality."""

    def test_add_region_success(self):
        """Test successfully adding a new region."""
        state = MockAppState()
        success, msg = add_manual_region_logic(state, "Region1")

        assert success is True
        assert "Region1" in state.manual_regions
        assert state.manual_regions["Region1"] == []
        assert state.selected_manual_region == "Region1"

    def test_add_multiple_regions(self):
        """Test adding multiple regions."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        add_manual_region_logic(state, "Region2")
        add_manual_region_logic(state, "Region3")

        assert len(state.manual_regions) == 3
        assert "Region1" in state.manual_regions
        assert "Region2" in state.manual_regions
        assert "Region3" in state.manual_regions
        assert state.selected_manual_region == "Region3"  # Last added is selected

    def test_add_region_empty_name(self):
        """Test that empty region names are rejected."""
        state = MockAppState()
        success, msg = add_manual_region_logic(state, "")

        assert success is False
        assert "enter a region name" in msg.lower()
        assert len(state.manual_regions) == 0

    def test_add_region_whitespace_name(self):
        """Test that whitespace-only names are rejected."""
        state = MockAppState()
        success, msg = add_manual_region_logic(state, "   ")

        assert success is False
        assert len(state.manual_regions) == 0

    def test_add_region_duplicate_name(self):
        """Test that duplicate region names are rejected."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        success, msg = add_manual_region_logic(state, "Region1")

        assert success is False
        assert "already exists" in msg
        assert len(state.manual_regions) == 1

    def test_add_region_case_sensitive(self):
        """Test that region names are case-sensitive."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        success, msg = add_manual_region_logic(state, "region1")

        assert success is True
        assert len(state.manual_regions) == 2
        assert "Region1" in state.manual_regions
        assert "region1" in state.manual_regions

    def test_remove_region_success(self):
        """Test successfully removing a region."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        success, msg = remove_manual_region_logic(state, "Region1")

        assert success is True
        assert "Region1" not in state.manual_regions
        assert state.selected_manual_region is None

    def test_remove_region_with_bas(self):
        """Test removing a region that has BAs assigned."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        state.manual_regions["Region1"] = ["ba1", "ba2"]
        remove_manual_region_logic(state, "Region1")

        assert "Region1" not in state.manual_regions
        assert len(state.manual_regions) == 0

    def test_remove_region_nonexistent(self):
        """Test removing a region that doesn't exist."""
        state = MockAppState()
        success, msg = remove_manual_region_logic(state, "NonExistent")

        assert success is False
        assert "not found" in msg.lower()

    def test_remove_selected_region(self):
        """Test removing the currently selected region."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        add_manual_region_logic(state, "Region2")
        select_manual_region_logic(state, "Region1")

        remove_manual_region_logic(state, "Region1")

        assert state.selected_manual_region is None
        assert "Region1" not in state.manual_regions
        assert "Region2" in state.manual_regions

    def test_remove_unselected_region(self):
        """Test removing a region that is not selected."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        add_manual_region_logic(state, "Region2")
        select_manual_region_logic(state, "Region1")

        remove_manual_region_logic(state, "Region2")

        assert state.selected_manual_region == "Region1"
        assert "Region2" not in state.manual_regions


class TestBAAssignment:
    """Test BA assignment to regions."""

    def test_assign_bas_success(self):
        """Test successfully assigning BAs to a region."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        state.selected_bas = {"ba1", "ba2", "ba3"}

        success, msg = assign_bas_logic(state, state.selected_bas)

        assert success is True
        assert set(state.manual_regions["Region1"]) == {"ba1", "ba2", "ba3"}

    def test_assign_bas_no_region_selected(self):
        """Test that assignment fails if no region is selected."""
        state = MockAppState()
        state.selected_bas = {"ba1", "ba2"}

        success, msg = assign_bas_logic(state, state.selected_bas)

        assert success is False
        assert "select a region first" in msg.lower()

    def test_assign_bas_no_bas_selected(self):
        """Test that assignment fails if no BAs are selected."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")

        success, msg = assign_bas_logic(state, set())

        assert success is False
        assert "select bas" in msg.lower()

    def test_assign_bas_already_assigned(self):
        """Test that already assigned BAs are not reassigned."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        state.manual_regions["Region1"] = ["ba1", "ba2"]

        add_manual_region_logic(state, "Region2")
        state.selected_bas = {"ba1", "ba2"}

        success, msg = assign_bas_logic(state, state.selected_bas)

        assert success is False
        assert "already assigned" in msg.lower()

    def test_assign_bas_partial_overlap(self):
        """Test assigning BAs when some are already assigned."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        state.manual_regions["Region1"] = ["ba1", "ba2"]

        add_manual_region_logic(state, "Region2")
        state.selected_bas = {"ba1", "ba2", "ba3", "ba4"}

        success, msg = assign_bas_logic(state, state.selected_bas)

        assert success is True
        assert set(state.manual_regions["Region2"]) == {"ba3", "ba4"}
        assert set(state.manual_regions["Region1"]) == {"ba1", "ba2"}

    def test_assign_bas_to_multiple_regions(self):
        """Test assigning different BAs to multiple regions."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        state.selected_bas = {"ba1", "ba2"}
        assign_bas_logic(state, state.selected_bas)

        add_manual_region_logic(state, "Region2")
        state.selected_bas = {"ba3", "ba4"}
        assign_bas_logic(state, state.selected_bas)

        assert set(state.manual_regions["Region1"]) == {"ba1", "ba2"}
        assert set(state.manual_regions["Region2"]) == {"ba3", "ba4"}

    def test_assign_single_ba(self):
        """Test assigning a single BA to a region."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        state.selected_bas = {"ba1"}

        success, msg = assign_bas_logic(state, state.selected_bas)

        assert success is True
        assert state.manual_regions["Region1"] == ["ba1"]

    def test_assign_bas_incrementally(self):
        """Test assigning BAs to the same region incrementally."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")

        state.selected_bas = {"ba1"}
        assign_bas_logic(state, state.selected_bas)

        state.selected_bas = {"ba2", "ba3"}
        assign_bas_logic(state, state.selected_bas)

        assert set(state.manual_regions["Region1"]) == {"ba1", "ba2", "ba3"}


class TestRegionSelection:
    """Test region selection functionality."""

    def test_select_region(self):
        """Test selecting a region."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        add_manual_region_logic(state, "Region2")

        select_manual_region_logic(state, "Region1")

        assert state.selected_manual_region == "Region1"

    def test_select_different_region(self):
        """Test changing selected region."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        add_manual_region_logic(state, "Region2")

        select_manual_region_logic(state, "Region1")
        select_manual_region_logic(state, "Region2")

        assert state.selected_manual_region == "Region2"

    def test_select_nonexistent_region(self):
        """Test selecting a nonexistent region (allowed but won't affect functionality)."""
        state = MockAppState()
        select_manual_region_logic(state, "NonExistent")

        assert state.selected_manual_region == "NonExistent"


class TestFinalization:
    """Test finalization of manual regions."""

    def test_finalize_success(self):
        """Test successful finalization of manual regions."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        state.manual_regions["Region1"] = ["ba1", "ba2"]
        add_manual_region_logic(state, "Region2")
        state.manual_regions["Region2"] = ["ba3", "ba4"]

        success, msg = finalize_manual_logic(state)

        assert success is True
        assert state.is_clustered is True
        assert state.region_aggregations == {
            "Region1": ["ba1", "ba2"],
            "Region2": ["ba3", "ba4"],
        }
        assert state.ba_to_region == {
            "ba1": "Region1",
            "ba2": "Region1",
            "ba3": "Region2",
            "ba4": "Region2",
        }

    def test_finalize_no_regions(self):
        """Test that finalization fails if no regions exist."""
        state = MockAppState()
        success, msg = finalize_manual_logic(state)

        assert success is False
        assert "at least one region" in msg.lower()

    def test_finalize_empty_region(self):
        """Test that finalization fails if a region has no BAs."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        state.manual_regions["Region1"] = ["ba1"]
        add_manual_region_logic(state, "Region2")
        # Region2 has no BAs

        success, msg = finalize_manual_logic(state)

        assert success is False
        assert "no bas assigned" in msg.lower()
        assert "Region2" in msg

    def test_finalize_multiple_empty_regions(self):
        """Test finalization with multiple empty regions."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        add_manual_region_logic(state, "Region2")
        add_manual_region_logic(state, "Region3")
        state.manual_regions["Region2"] = ["ba1"]

        success, msg = finalize_manual_logic(state)

        assert success is False
        assert "Region1" in msg or "Region3" in msg

    def test_finalize_single_region(self):
        """Test finalizing a single region."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        state.manual_regions["Region1"] = ["ba1", "ba2", "ba3"]

        success, msg = finalize_manual_logic(state)

        assert success is True
        assert len(state.region_aggregations) == 1
        assert state.region_aggregations["Region1"] == ["ba1", "ba2", "ba3"]

    def test_finalize_ba_to_region_mapping(self):
        """Test that BA to region mapping is correct after finalization."""
        state = MockAppState()
        add_manual_region_logic(state, "West")
        state.manual_regions["West"] = ["ca", "nv", "co"]
        add_manual_region_logic(state, "East")
        state.manual_regions["East"] = ["tx", "ok", "ne"]

        finalize_manual_logic(state)

        assert state.ba_to_region["ca"] == "West"
        assert state.ba_to_region["nv"] == "West"
        assert state.ba_to_region["co"] == "West"
        assert state.ba_to_region["tx"] == "East"
        assert state.ba_to_region["ok"] == "East"
        assert state.ba_to_region["ne"] == "East"

    def test_finalize_message_content(self):
        """Test that finalization message contains correct counts."""
        state = MockAppState()
        add_manual_region_logic(state, "R1")
        state.manual_regions["R1"] = ["ba1", "ba2"]
        add_manual_region_logic(state, "R2")
        state.manual_regions["R2"] = ["ba3"]

        success, msg = finalize_manual_logic(state)

        assert "2 regions" in msg.lower()
        assert "3 bas" in msg.lower()


class TestClearManualRegions:
    """Test clearing manual regions."""

    def test_clear_regions(self):
        """Test clearing all manual regions."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        state.manual_regions["Region1"] = ["ba1", "ba2"]
        add_manual_region_logic(state, "Region2")
        state.manual_regions["Region2"] = ["ba3"]
        state.is_clustered = True
        state.region_aggregations = {"Region1": ["ba1", "ba2"]}

        clear_manual_regions_logic(state)

        assert state.manual_regions == {}
        assert state.selected_manual_region is None
        assert state.cluster_colors == {}
        assert state.ba_to_region == {}
        assert state.is_clustered is False
        assert state.region_aggregations is None

    def test_clear_empty_state(self):
        """Test clearing when no regions exist."""
        state = MockAppState()
        clear_manual_regions_logic(state)

        assert state.manual_regions == {}
        assert state.selected_manual_region is None

    def test_clear_preserves_other_state(self):
        """Test that clearing doesn't affect unrelated state."""
        state = MockAppState()
        state.selected_bas = {"ba1", "ba2"}
        add_manual_region_logic(state, "Region1")

        clear_manual_regions_logic(state)

        # selected_bas should not be cleared
        assert state.selected_bas == {"ba1", "ba2"}


class TestModeSwitching:
    """Test switching between clustering and manual modes."""

    def test_switch_to_manual_mode(self):
        """Test switching to manual mode."""
        state = MockAppState()
        state.is_clustered = True
        state.cluster_colors = {"ba1": "#ff0000"}
        state.ba_to_region = {"ba1": "Region1"}
        state.region_aggregations = {"Region1": ["ba1"]}

        on_region_mode_change_logic(state, is_manual=True)

        assert state.is_manual_mode is True
        assert state.cluster_colors == {}
        assert state.ba_to_region == {}
        assert state.is_clustered is False
        assert state.region_aggregations is None

    def test_switch_to_clustering_mode(self):
        """Test switching to clustering mode."""
        state = MockAppState()
        state.is_manual_mode = True
        add_manual_region_logic(state, "Region1")
        state.manual_regions["Region1"] = ["ba1", "ba2"]

        on_region_mode_change_logic(state, is_manual=False)

        assert state.is_manual_mode is False
        assert state.manual_regions == {}
        assert state.selected_manual_region is None

    def test_switch_back_and_forth(self):
        """Test switching between modes multiple times."""
        state = MockAppState()

        # To manual
        on_region_mode_change_logic(state, is_manual=True)
        assert state.is_manual_mode is True

        # Back to clustering
        on_region_mode_change_logic(state, is_manual=False)
        assert state.is_manual_mode is False

        # To manual again
        on_region_mode_change_logic(state, is_manual=True)
        assert state.is_manual_mode is True

    def test_switching_clears_manual_data(self):
        """Test that switching to clustering mode clears manual data."""
        state = MockAppState()
        on_region_mode_change_logic(state, is_manual=True)
        add_manual_region_logic(state, "Region1")
        add_manual_region_logic(state, "Region2")
        state.manual_regions["Region1"] = ["ba1", "ba2"]

        on_region_mode_change_logic(state, is_manual=False)

        assert len(state.manual_regions) == 0
        assert state.selected_manual_region is None

    def test_switching_clears_clustering_data(self):
        """Test that switching to manual mode clears clustering data."""
        state = MockAppState()
        state.is_clustered = True
        state.cluster_colors = {"ba1": "#ff0000", "ba2": "#00ff00"}
        state.ba_to_region = {"ba1": "R1", "ba2": "R2"}
        state.region_aggregations = {"R1": ["ba1"], "R2": ["ba2"]}

        on_region_mode_change_logic(state, is_manual=True)

        assert state.is_clustered is False
        assert state.cluster_colors == {}
        assert state.ba_to_region == {}
        assert state.region_aggregations is None


class TestManualRegionIntegration:
    """Integration tests for manual region workflow."""

    def test_complete_manual_workflow(self):
        """Test complete workflow from creation to finalization."""
        state = MockAppState()

        # Switch to manual mode
        on_region_mode_change_logic(state, is_manual=True)
        assert state.is_manual_mode is True

        # Create regions
        add_manual_region_logic(state, "West")
        add_manual_region_logic(state, "Central")
        add_manual_region_logic(state, "East")

        # Assign BAs
        select_manual_region_logic(state, "West")
        state.selected_bas = {"ca", "nv", "co"}
        assign_bas_logic(state, state.selected_bas)

        select_manual_region_logic(state, "Central")
        state.selected_bas = {"tx", "ok"}
        assign_bas_logic(state, state.selected_bas)

        select_manual_region_logic(state, "East")
        state.selected_bas = {"ne", "fl", "ga"}
        assign_bas_logic(state, state.selected_bas)

        # Finalize
        success, msg = finalize_manual_logic(state)

        assert success is True
        assert len(state.region_aggregations) == 3
        assert state.is_clustered is True
        assert len(state.ba_to_region) == 8

    def test_workflow_with_region_removal(self):
        """Test workflow with region creation and removal."""
        state = MockAppState()
        on_region_mode_change_logic(state, is_manual=True)

        # Create regions
        add_manual_region_logic(state, "Region1")
        add_manual_region_logic(state, "Region2")
        add_manual_region_logic(state, "Region3")

        # Assign BAs
        state.manual_regions["Region1"] = ["ba1"]
        state.manual_regions["Region2"] = ["ba2"]
        state.manual_regions["Region3"] = ["ba3"]

        # Remove middle region
        remove_manual_region_logic(state, "Region2")

        # Finalize
        success, msg = finalize_manual_logic(state)

        assert success is True
        assert len(state.region_aggregations) == 2
        assert "Region2" not in state.region_aggregations

    def test_workflow_with_reassignment(self):
        """Test workflow with BA reassignment."""
        state = MockAppState()
        on_region_mode_change_logic(state, is_manual=True)

        # Create regions
        add_manual_region_logic(state, "Region1")
        state.manual_regions["Region1"] = ["ba1", "ba2"]

        add_manual_region_logic(state, "Region2")

        # Try to assign already-assigned BAs (should fail)
        state.selected_bas = {"ba1", "ba3"}
        success, msg = assign_bas_logic(state, state.selected_bas)

        # Only ba3 should be assigned
        assert success is True
        assert "ba3" in state.manual_regions["Region2"]
        assert "ba1" not in state.manual_regions["Region2"]

    def test_workflow_yaml_generation(self):
        """Test that finalization creates correct region_aggregations format."""
        state = MockAppState()
        on_region_mode_change_logic(state, is_manual=True)

        add_manual_region_logic(state, "WECC")
        state.manual_regions["WECC"] = ["ciso", "nevp", "pace"]

        add_manual_region_logic(state, "ERCOT")
        state.manual_regions["ERCOT"] = ["tre"]

        finalize_manual_logic(state)

        # Verify format matches what clustering produces
        assert isinstance(state.region_aggregations, dict)
        assert all(isinstance(v, list) for v in state.region_aggregations.values())
        assert state.region_aggregations["WECC"] == ["ciso", "nevp", "pace"]
        assert state.region_aggregations["ERCOT"] == ["tre"]

    def test_workflow_with_single_ba_regions(self):
        """Test workflow with regions containing single BAs."""
        state = MockAppState()
        on_region_mode_change_logic(state, is_manual=True)

        add_manual_region_logic(state, "R1")
        state.manual_regions["R1"] = ["ba1"]

        add_manual_region_logic(state, "R2")
        state.manual_regions["R2"] = ["ba2"]

        add_manual_region_logic(state, "R3")
        state.manual_regions["R3"] = ["ba3"]

        success, msg = finalize_manual_logic(state)

        assert success is True
        assert len(state.region_aggregations) == 3
        assert all(len(bas) == 1 for bas in state.region_aggregations.values())

    def test_workflow_with_many_bas_single_region(self):
        """Test workflow with single region containing many BAs."""
        state = MockAppState()
        on_region_mode_change_logic(state, is_manual=True)

        add_manual_region_logic(state, "USA")
        bas = [f"ba{i}" for i in range(1, 51)]  # 50 BAs
        state.manual_regions["USA"] = bas

        success, msg = finalize_manual_logic(state)

        assert success is True
        assert len(state.region_aggregations) == 1
        assert len(state.region_aggregations["USA"]) == 50

    def test_workflow_clear_and_restart(self):
        """Test clearing and restarting the workflow."""
        state = MockAppState()
        on_region_mode_change_logic(state, is_manual=True)

        # First attempt
        add_manual_region_logic(state, "Region1")
        state.manual_regions["Region1"] = ["ba1", "ba2"]

        # Clear
        clear_manual_regions_logic(state)

        # Start over
        add_manual_region_logic(state, "NewRegion")
        state.manual_regions["NewRegion"] = ["ba3", "ba4"]

        success, msg = finalize_manual_logic(state)

        assert success is True
        assert len(state.region_aggregations) == 1
        assert "NewRegion" in state.region_aggregations
        assert "Region1" not in state.region_aggregations

    def test_manual_regions_compatible_with_downstream_processing(self):
        """Test that manual regions produce same data structure as clustering."""
        state_manual = MockAppState()
        on_region_mode_change_logic(state_manual, is_manual=True)
        add_manual_region_logic(state_manual, "R1")
        state_manual.manual_regions["R1"] = ["ba1", "ba2"]
        add_manual_region_logic(state_manual, "R2")
        state_manual.manual_regions["R2"] = ["ba3", "ba4"]
        finalize_manual_logic(state_manual)

        # Simulate clustering result
        state_cluster = MockAppState()
        state_cluster.region_aggregations = {
            "R1": ["ba1", "ba2"],
            "R2": ["ba3", "ba4"],
        }
        state_cluster.is_clustered = True
        state_cluster.ba_to_region = {
            "ba1": "R1",
            "ba2": "R1",
            "ba3": "R2",
            "ba4": "R2",
        }

        # Both should have identical structure
        assert state_manual.region_aggregations == state_cluster.region_aggregations
        assert state_manual.is_clustered == state_cluster.is_clustered
        assert state_manual.ba_to_region == state_cluster.ba_to_region


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_special_characters_in_region_name(self):
        """Test region names with special characters."""
        state = MockAppState()
        special_names = [
            "Region-1",
            "Region_2",
            "Region 3",
            "Region(4)",
            "Region[5]",
            "Region.6",
        ]

        for name in special_names:
            success, msg = add_manual_region_logic(state, name)
            assert success is True
            assert name in state.manual_regions

    def test_unicode_in_region_name(self):
        """Test region names with Unicode characters."""
        state = MockAppState()
        unicode_names = ["Région1", "地区2", "منطقة3"]

        for name in unicode_names:
            success, msg = add_manual_region_logic(state, name)
            assert success is True

    def test_very_long_region_name(self):
        """Test region with very long name."""
        state = MockAppState()
        long_name = "A" * 1000
        success, msg = add_manual_region_logic(state, long_name)

        assert success is True
        assert long_name in state.manual_regions

    def test_numeric_region_name(self):
        """Test region with numeric name."""
        state = MockAppState()
        add_manual_region_logic(state, "123")
        add_manual_region_logic(state, "456")

        assert "123" in state.manual_regions
        assert "456" in state.manual_regions

    def test_assign_same_ba_set_twice(self):
        """Test assigning the same BA set twice (should fail second time)."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        bas = {"ba1", "ba2"}
        state.selected_bas = bas
        assign_bas_logic(state, state.selected_bas)

        # Try again with same BAs
        state.selected_bas = bas
        success, msg = assign_bas_logic(state, state.selected_bas)

        assert success is False

    def test_finalize_preserves_manual_regions(self):
        """Test that finalization doesn't clear manual_regions."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        state.manual_regions["Region1"] = ["ba1"]

        finalize_manual_logic(state)

        # manual_regions should still exist
        assert state.manual_regions == {"Region1": ["ba1"]}

    def test_empty_ba_id(self):
        """Test handling of empty string BA ID."""
        state = MockAppState()
        add_manual_region_logic(state, "Region1")
        state.selected_bas = {"", "ba1"}

        success, msg = assign_bas_logic(state, state.selected_bas)

        # Should assign both (including empty string if that's what's selected)
        assert success is True


# ============================================================================
# Renewables Clustering Logic Tests
# ============================================================================


def compute_demand_avg(demand_df):
    """Compute average annual demand per region (lowercased region names)."""
    df = demand_df.copy()
    df["region"] = df["region"].astype(str).str.lower()
    return df.groupby("region")["annual_demand_mwh"].mean().to_dict()


def select_resources_by_demand(df_region, target_mwh):
    if df_region.empty or target_mwh <= 0:
        return df_region.head(0)
    df_sorted = df_region.sort_values("lcoe").copy()
    df_sorted["annual_mwh"] = df_sorted["cpa_mw"] * df_sorted["cf"] * 8760
    df_sorted["cum_mwh"] = df_sorted["annual_mwh"].cumsum()
    selected = df_sorted[df_sorted["cum_mwh"] < target_mwh]
    if selected.empty:
        return df_sorted.head(1)
    if selected["cum_mwh"].iloc[-1] < target_mwh and len(selected) < len(df_sorted):
        return df_sorted.iloc[: len(selected) + 1]
    return selected


def allocate_bins_by_capacity(region_caps, region_ranges, target_bins_total):
    regions = [r for r, cap in region_caps.items() if cap > 0]
    if not regions:
        return {}
    total_cap = sum(region_caps[r] for r in regions)
    bins = {}
    for region in regions:
        share = region_caps[region] / total_cap if total_cap > 0 else 0
        bins[region] = max(1, int(round(share * target_bins_total)))

    ordered = sorted(regions, key=lambda r: region_ranges.get(r, 0.0))
    while sum(bins.values()) < target_bins_total:
        for region in ordered:
            bins[region] += 1
            if sum(bins.values()) >= target_bins_total:
                break

    while sum(bins.values()) > target_bins_total:
        made_change = False
        for region in ordered:
            if bins[region] > 1:
                bins[region] -= 1
                made_change = True
                if sum(bins.values()) <= target_bins_total:
                    break
        # If we can't reduce further (all regions at minimum 1), break to avoid infinite loop
        if not made_change:
            break

    return bins


def compute_region_bin_cluster_config(
    region_caps,
    region_lcoe_data,
    region_ranges,
    target_total_resources,
    default_mw_per_bin,
    default_n_clusters,
):
    regions = [r for r, cap in region_caps.items() if cap > 0]
    if not regions:
        return {}, 0, 0, 0

    bins = {
        r: max(1, int(math.ceil(region_caps[r] / default_mw_per_bin))) for r in regions
    }
    minimum_total_resources = int(sum(bins.values()))

    if not target_total_resources or target_total_resources <= 0:
        n_clusters = {r: default_n_clusters for r in regions}
        total = sum(bins[r] * n_clusters[r] for r in regions)
        return (
            {
                r: {
                    "bins": bins[r],
                    "q": bins[r],
                    "mw_per_bin": max(1, int(round(region_caps[r] / bins[r]))),
                    "n_clusters": n_clusters[r],
                }
                for r in regions
            },
            total,
            total,
            minimum_total_resources,
        )

    effective_target = max(int(target_total_resources), minimum_total_resources)
    n_clusters = optimize_cluster_allocation_logic(
        region_lcoe_data, bins, effective_target
    )

    for region in regions:
        if region not in n_clusters:
            n_clusters[region] = 1

    total = int(sum(bins[r] * n_clusters[r] for r in regions))

    return (
        {
            r: {
                "bins": bins[r],
                "q": bins[r],
                "mw_per_bin": max(1, int(round(region_caps[r] / bins[r]))),
                "n_clusters": n_clusters[r],
            }
            for r in regions
        },
        total,
        effective_target,
        minimum_total_resources,
    )


def optimize_cluster_allocation_logic(region_lcoe_data, bins, target_total_resources):
    regions = list(bins.keys())
    if not regions:
        return {}

    n_clusters = {r: 1 for r in regions}
    current_total = sum(bins[r] for r in regions)

    if current_total >= target_total_resources:
        return n_clusters

    region_metrics = {}
    for r, data in region_lcoe_data.items():
        lcoe = data["lcoe"]
        cap = data["capacity"]
        total_cap = np.sum(cap)
        if total_cap == 0:
            region_metrics[r] = 0
            continue

        avg_lcoe = np.average(lcoe, weights=cap)
        variance = np.average((lcoe - avg_lcoe) ** 2, weights=cap)
        std_dev = np.sqrt(variance)
        region_metrics[r] = total_cap * std_dev

    while current_total < target_total_resources:
        best_region = None
        best_gain_per_cost = -1.0

        for r in regions:
            cost = bins[r]
            if current_total + cost > target_total_resources:
                continue

            n = n_clusters[r]
            metric = region_metrics.get(r, 0)
            K = n * bins[r]
            K_next = (n + 1) * bins[r]
            gain = metric * (1.0 / K - 1.0 / K_next)
            efficiency = gain / cost

            if efficiency > best_gain_per_cost:
                best_gain_per_cost = efficiency
                best_region = r

        if best_region is None:
            break

        n_clusters[best_region] += 1
        current_total += bins[best_region]

    return n_clusters


def suggest_total_resources(region_caps, chunk_mw):
    total = 0
    for cap in region_caps.values():
        if cap > 0:
            total += int(math.ceil(cap / chunk_mw))
    return total


def compute_suggested_budget(region_data, region_targets, avg_resource_mw):
    suggested = 0
    for region_name, target_mwh in region_targets.items():
        data = region_data.get(region_name)
        if not data or target_mwh <= 0:
            continue

        cum_mwh = data["cum_mwh"]
        if cum_mwh.size == 0:
            continue

        cutoff_idx = int(np.searchsorted(cum_mwh, target_mwh, side="left"))
        if cutoff_idx >= cum_mwh.size:
            cutoff_idx = cum_mwh.size - 1

        cap_vals = data["capacity_mw"]
        selected_capacity = float(cap_vals[: cutoff_idx + 1].sum())
        if selected_capacity > 0:
            suggested += int(math.ceil(selected_capacity / avg_resource_mw))

    return suggested


def compute_region_targets(region_data_keys, demand_map, share):
    return {
        region: demand_map.get(region, 0.0) * share
        for region in region_data_keys
        if demand_map.get(region, 0.0) * share > 0
    }


def compute_region_targets_by_demand_and_lcoe(region_demand, region_data, share):
    return {
        region: region_demand.get(region, 0.0) * share
        for region in region_data.keys()
        if region_demand.get(region, 0.0) * share > 0
    }


# Duplicated from cluster_app.py renewables supply-curve helpers
def _safe_float(value, default=0.0):
    try:
        parsed = float(value)
        if math.isfinite(parsed):
            return parsed
    except Exception:
        pass
    return default


def _extract_cluster_lcoe_max(cluster_item):
    if not isinstance(cluster_item, dict):
        return None
    filters = cluster_item.get("filter")
    if not isinstance(filters, list):
        return None
    for filt in filters:
        if isinstance(filt, dict) and filt.get("feature") == "lcoe":
            return _safe_float(filt.get("max"), None)
    return None


def _extract_cluster_q(cluster_item):
    if not isinstance(cluster_item, dict):
        return 1
    bins = cluster_item.get("bin")
    if isinstance(bins, list) and bins:
        q = _safe_float(bins[0].get("q"), 1)
        return max(1, int(round(q)))
    return 1


def _extract_cluster_feature(cluster_item):
    if not isinstance(cluster_item, dict):
        return "lcoe"
    cluster_cfg = cluster_item.get("cluster")
    if isinstance(cluster_cfg, list) and cluster_cfg:
        feature = str(cluster_cfg[0].get("feature", "lcoe") or "lcoe")
        return feature
    return "lcoe"


def _extract_cluster_n_clusters(cluster_item):
    if not isinstance(cluster_item, dict):
        return 1
    cluster_cfg = cluster_item.get("cluster")
    if isinstance(cluster_cfg, list) and cluster_cfg:
        n_clusters = _safe_float(cluster_cfg[0].get("n_clusters"), 1)
        return max(1, int(round(n_clusters)))
    return 1


def _agglomerative_1d_labels(values, weights, k):
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    valid = np.isfinite(values) & np.isfinite(weights)
    values = values[valid]
    weights = weights[valid]

    if values.size == 0:
        return np.array([], dtype=int)

    weights = np.maximum(weights, 1e-9)
    k = max(1, min(int(k), values.size))
    if k == 1:
        return np.zeros(values.size, dtype=int)

    order = np.argsort(values, kind="mergesort")
    sorted_vals = values[order]
    sorted_weights = weights[order]

    clusters = [
        {
            "positions": [idx],
            "weight": float(sorted_weights[idx]),
            "mean": float(sorted_vals[idx]),
        }
        for idx in range(sorted_vals.size)
    ]

    def merge_cost(left_cluster, right_cluster):
        left_w = left_cluster["weight"]
        right_w = right_cluster["weight"]
        denom = left_w + right_w
        if denom <= 0:
            return 0.0
        mean_diff = left_cluster["mean"] - right_cluster["mean"]
        return (left_w * right_w / denom) * (mean_diff**2)

    while len(clusters) > k:
        best_idx = 0
        best_cost = None
        for idx in range(len(clusters) - 1):
            cost = merge_cost(clusters[idx], clusters[idx + 1])
            if best_cost is None or cost < best_cost:
                best_cost = cost
                best_idx = idx

        left_cluster = clusters[best_idx]
        right_cluster = clusters[best_idx + 1]
        merged_weight = left_cluster["weight"] + right_cluster["weight"]
        if merged_weight <= 0:
            merged_mean = 0.5 * (left_cluster["mean"] + right_cluster["mean"])
        else:
            merged_mean = (
                left_cluster["mean"] * left_cluster["weight"]
                + right_cluster["mean"] * right_cluster["weight"]
            ) / merged_weight

        merged_cluster = {
            "positions": left_cluster["positions"] + right_cluster["positions"],
            "weight": merged_weight,
            "mean": float(merged_mean),
        }
        clusters[best_idx] = merged_cluster
        del clusters[best_idx + 1]

    labels_sorted = np.zeros(sorted_vals.size, dtype=int)
    for label, cluster_data in enumerate(clusters):
        for pos in cluster_data["positions"]:
            labels_sorted[pos] = label

    labels = np.zeros(sorted_vals.size, dtype=int)
    labels[order] = labels_sorted
    return labels


def _assign_weighted_bins(df, bin_feature, q):
    if df is None or df.empty:
        return np.array([], dtype=int)

    q = max(1, int(q))
    if q == 1:
        return np.zeros(len(df), dtype=int)

    temp = df.copy().reset_index(drop=True)
    if bin_feature not in temp.columns:
        bin_feature = "lcoe"

    temp["_bin_feature"] = pd.to_numeric(temp[bin_feature], errors="coerce")
    temp["_weights"] = pd.to_numeric(temp["capacity_mw"], errors="coerce").fillna(0.0)
    temp["_weights"] = temp["_weights"].clip(lower=0.0)
    temp["_weights"] = temp["_weights"].where(temp["_weights"] > 0.0, 1e-9)

    temp_sorted = temp.sort_values("_bin_feature", kind="mergesort").reset_index()
    cumulative = temp_sorted["_weights"].cumsum().to_numpy()
    total = float(cumulative[-1]) if cumulative.size else 0.0
    if total <= 0:
        return np.zeros(len(df), dtype=int)

    bin_edges = np.linspace(0.0, total, q + 1)[1:-1]
    bin_ids_sorted = np.searchsorted(bin_edges, cumulative, side="left")

    bin_ids = np.zeros(len(df), dtype=int)
    for pos, row in enumerate(temp_sorted.itertuples(index=False)):
        original_idx = int(getattr(row, "index"))
        bin_ids[original_idx] = int(bin_ids_sorted[pos])

    return bin_ids


def _build_individual_supply_curve_bars(region_df):
    bars = []
    if region_df is None or region_df.empty:
        return bars
    for idx, row in enumerate(region_df.itertuples(index=False), start=1):
        cap = _safe_float(getattr(row, "capacity_mw", 0.0), 0.0)
        lcoe = _safe_float(getattr(row, "lcoe", 0.0), 0.0)
        if cap <= 0:
            continue
        bars.append({"label": f"CPA {idx}", "capacity_mw": cap, "lcoe": lcoe})
    return bars


def _build_aggregated_supply_curve_bars(region_df, cluster_item):
    if region_df is None or region_df.empty:
        return []

    work_df = region_df.copy().reset_index(drop=True)
    if "capacity_mw" not in work_df.columns or "lcoe" not in work_df.columns:
        return []

    bin_cfg = cluster_item.get("bin") if isinstance(cluster_item, dict) else None
    bin_feature = "lcoe"
    if isinstance(bin_cfg, list) and bin_cfg:
        bin_feature = str(bin_cfg[0].get("feature", "lcoe") or "lcoe")
    q = _extract_cluster_q(cluster_item)
    cluster_feature = _extract_cluster_feature(cluster_item)
    n_clusters = _extract_cluster_n_clusters(cluster_item)

    if cluster_feature not in work_df.columns:
        cluster_feature = "lcoe"

    work_df["capacity_mw"] = pd.to_numeric(
        work_df["capacity_mw"], errors="coerce"
    ).fillna(0.0)
    work_df["lcoe"] = pd.to_numeric(work_df["lcoe"], errors="coerce").fillna(0.0)
    work_df[cluster_feature] = pd.to_numeric(
        work_df[cluster_feature], errors="coerce"
    ).fillna(0.0)
    work_df = work_df[work_df["capacity_mw"] > 0.0].copy()
    if work_df.empty:
        return []

    work_df["_bin_id"] = _assign_weighted_bins(work_df, bin_feature, q)

    bars = []
    for bin_id, bin_df in work_df.groupby("_bin_id", sort=True):
        bin_df = bin_df.reset_index(drop=True)
        effective_k = max(1, min(int(n_clusters), len(bin_df)))

        values = bin_df[cluster_feature].to_numpy(dtype=float)
        weights = bin_df["capacity_mw"].to_numpy(dtype=float)
        labels = _agglomerative_1d_labels(values, weights, effective_k)
        if labels.size == 0:
            labels = np.zeros(len(bin_df), dtype=int)

        for cluster_idx in sorted(set(labels.tolist())):
            cluster_rows = bin_df[labels == cluster_idx]
            capacity = float(cluster_rows["capacity_mw"].sum())
            if capacity <= 0:
                continue
            lcoe = float(
                (cluster_rows["lcoe"] * cluster_rows["capacity_mw"]).sum() / capacity
            )
            bars.append(
                {
                    "label": f"Bin {int(bin_id) + 1} • Cluster {int(cluster_idx) + 1}",
                    "capacity_mw": capacity,
                    "lcoe": lcoe,
                    "count": int(len(cluster_rows)),
                    "bin": int(bin_id) + 1,
                }
            )

    bars.sort(
        key=lambda item: (
            _safe_float(item.get("lcoe"), 0.0),
            _safe_float(item.get("capacity_mw"), 0.0),
        )
    )
    return bars


def _compute_cutoff_idx_from_capacity(cum_capacity_mw, target_capacity_mw):
    cum = np.asarray(cum_capacity_mw, dtype=float)
    if cum.size == 0:
        return None
    target = max(0.0, _safe_float(target_capacity_mw, 0.0))
    idx = int(np.searchsorted(cum, target, side="left"))
    if idx >= cum.size:
        idx = cum.size - 1
    if idx < 0:
        idx = 0
    return idx


def _apply_capacity_override_to_curve_data(region_data, requested_capacity_mw):
    if not isinstance(region_data, dict):
        return

    cum_capacity = np.asarray(region_data.get("cum_capacity_mw", []), dtype=float)
    lcoe_vals = np.asarray(region_data.get("lcoe", []), dtype=float)
    if cum_capacity.size == 0 or lcoe_vals.size == 0:
        return

    baseline_capacity = _safe_float(region_data.get("baseline_capacity_mw", 0.0), 0.0)
    available_capacity = _safe_float(region_data.get("available_capacity_mw", 0.0), 0.0)

    requested = _safe_float(requested_capacity_mw, baseline_capacity)
    target_capacity = max(0.0, min(requested, available_capacity))
    cutoff_idx = _compute_cutoff_idx_from_capacity(cum_capacity, target_capacity)
    if cutoff_idx is None:
        return

    included_capacity = float(cum_capacity[cutoff_idx])
    lcoe_max = float(lcoe_vals[min(cutoff_idx, lcoe_vals.size - 1)])

    region_data["cutoff_idx"] = int(cutoff_idx)
    region_data["included_capacity_mw"] = included_capacity
    region_data["lcoe_max"] = lcoe_max


def _build_supply_curve_bars_from_curve_data(curve_data, max_points=None):
    if not isinstance(curve_data, dict):
        return []

    caps = np.asarray(curve_data.get("capacity_mw", []), dtype=float)
    lcoe = np.asarray(curve_data.get("lcoe", []), dtype=float)
    if caps.size == 0 or lcoe.size == 0:
        return []

    n = min(caps.size, lcoe.size)
    caps = caps[:n]
    lcoe = lcoe[:n]

    if max_points is None or max_points <= 0 or n <= max_points:
        bars = []
        for idx in range(n):
            cap = float(caps[idx])
            if cap <= 0:
                continue
            bars.append(
                {
                    "label": f"CPA {idx + 1}",
                    "capacity_mw": cap,
                    "lcoe": float(lcoe[idx]),
                }
            )
        return bars

    chunk = int(math.ceil(n / max_points))
    bars = []
    for start in range(0, n, chunk):
        end = min(start + chunk, n)
        block_caps = caps[start:end]
        block_lcoe = lcoe[start:end]
        total_cap = float(block_caps.sum())
        if total_cap <= 0:
            continue
        weighted_lcoe = float((block_lcoe * block_caps).sum() / total_cap)
        bars.append(
            {
                "label": f"CPA {start + 1}-{end}",
                "capacity_mw": total_cap,
                "lcoe": weighted_lcoe,
            }
        )

    return bars


class TestRenewablesClusteringLogic:
    """Test renewables clustering helpers (pure logic)."""

    def test_compute_demand_avg_lowercases_regions(self):
        demand_df = pd.DataFrame(
            {
                "region": ["P1", "p1", "P2", "p2"],
                "weather_year": [2007, 2008, 2007, 2008],
                "annual_demand_mwh": [100.0, 300.0, 200.0, 400.0],
            }
        )

        avg = compute_demand_avg(demand_df)

        assert avg["p1"] == 200.0
        assert avg["p2"] == 300.0

    def test_select_resources_by_demand_target_zero(self):
        df = pd.DataFrame(
            {
                "region": ["r1", "r1"],
                "cpa_mw": [100.0, 200.0],
                "cf": [0.4, 0.5],
                "lcoe": [40.0, 30.0],
            }
        )

        selected = select_resources_by_demand(df, 0)

        assert selected.empty

    def test_select_resources_by_demand_minimum_selection(self):
        df = pd.DataFrame(
            {
                "region": ["r1", "r1", "r1"],
                "cpa_mw": [100.0, 200.0, 150.0],
                "cf": [0.5, 0.4, 0.3],
                "lcoe": [40.0, 35.0, 50.0],
            }
        )

        selected = select_resources_by_demand(df, 1000.0)

        assert len(selected) == 1
        assert selected.iloc[0]["lcoe"] == 35.0

    def test_select_resources_by_demand_includes_next_row(self):
        df = pd.DataFrame(
            {
                "region": ["r1", "r1", "r1"],
                "cpa_mw": [100.0, 200.0, 150.0],
                "cf": [0.5, 0.4, 0.3],
                "lcoe": [40.0, 35.0, 50.0],
            }
        )

        selected = select_resources_by_demand(df, 800000.0)

        assert len(selected) == 2
        assert list(selected["lcoe"]) == [35.0, 40.0]

    def test_allocate_bins_by_capacity_exact_total(self):
        region_caps = {"A": 100.0, "B": 300.0, "C": 600.0}
        region_ranges = {"A": 0.2, "B": 0.1, "C": 0.3}

        bins = allocate_bins_by_capacity(region_caps, region_ranges, 10)

        assert bins == {"A": 1, "B": 3, "C": 6}

    def test_allocate_bins_by_capacity_adds_bins_by_range(self):
        region_caps = {"A": 50.0, "B": 50.0, "C": 50.0}
        region_ranges = {"A": 0.3, "B": 0.2, "C": 0.1}

        bins = allocate_bins_by_capacity(region_caps, region_ranges, 4)

        assert bins["C"] == 2
        assert bins["B"] == 1
        assert bins["A"] == 1
        assert sum(bins.values()) == 4

    def test_allocate_bins_by_capacity_reduces_bins_by_range(self):
        region_caps = {"A": 600.0, "B": 300.0, "C": 100.0}
        region_ranges = {"A": 0.3, "B": 0.2, "C": 0.1}

        bins = allocate_bins_by_capacity(region_caps, region_ranges, 5)

        assert bins == {"A": 3, "B": 1, "C": 1}
        assert sum(bins.values()) == 5

    def test_compute_region_bin_cluster_config_default_target(self):
        region_caps = {"A": 100.0, "B": 250.0}
        region_ranges = {"A": 0.1, "B": 0.2}
        region_lcoe_data = {
            "A": {"lcoe": np.array([10.0]), "capacity": np.array([100.0])},
            "B": {"lcoe": np.array([20.0, 25.0]), "capacity": np.array([150.0, 100.0])},
        }

        cfg, total, effective_target, minimum_budget = (
            compute_region_bin_cluster_config(
                region_caps, region_lcoe_data, region_ranges, None, 100.0, 2
            )
        )

        assert total == 8
        assert effective_target == 8
        assert minimum_budget == 4
        assert cfg["A"]["bins"] == 1
        assert cfg["B"]["bins"] == 3
        assert cfg["A"]["q"] == 1
        assert cfg["B"]["q"] == 3
        assert cfg["A"]["mw_per_bin"] == 100
        assert cfg["B"]["mw_per_bin"] == 83
        assert cfg["A"]["n_clusters"] == 2

    def test_compute_region_bin_cluster_config_applies_budget_floor(self):
        region_caps = {"A": 100.0, "B": 300.0}
        region_ranges = {"A": 0.2, "B": 0.1}
        region_lcoe_data = {
            "A": {"lcoe": np.array([10.0, 12.0]), "capacity": np.array([50.0, 50.0])},
            "B": {"lcoe": np.array([9.0, 11.0]), "capacity": np.array([150.0, 150.0])},
        }

        cfg, total, effective_target, minimum_budget = (
            compute_region_bin_cluster_config(
                region_caps, region_lcoe_data, region_ranges, 2, 100.0, 2
            )
        )

        assert minimum_budget == 4
        assert effective_target == 4
        assert total == 4
        assert cfg["A"]["bins"] == 1
        assert cfg["B"]["bins"] == 3
        assert cfg["A"]["q"] == 1
        assert cfg["B"]["q"] == 3
        assert cfg["A"]["mw_per_bin"] == 100
        assert cfg["B"]["mw_per_bin"] == 100
        assert cfg["A"]["n_clusters"] == 1
        assert cfg["B"]["n_clusters"] == 1

    def test_compute_region_bin_cluster_config_allocates_extra_to_higher_spread(self):
        region_caps = {"A": 100.0, "B": 100.0}
        region_ranges = {"A": 0.1, "B": 0.2}
        region_lcoe_data = {
            "A": {"lcoe": np.array([10.0, 10.4]), "capacity": np.array([50.0, 50.0])},
            "B": {"lcoe": np.array([0.0, 20.0]), "capacity": np.array([50.0, 50.0])},
        }

        cfg, total, effective_target, minimum_budget = (
            compute_region_bin_cluster_config(
                region_caps, region_lcoe_data, region_ranges, 3, 100.0, 2
            )
        )

        assert minimum_budget == 2
        assert effective_target == 3
        assert total == 3
        assert cfg["A"]["q"] == 1
        assert cfg["A"]["mw_per_bin"] == 100
        assert cfg["B"]["q"] == 1
        assert cfg["B"]["mw_per_bin"] == 100
        assert cfg["A"]["n_clusters"] == 1
        assert cfg["B"]["n_clusters"] == 2

    def test_compute_region_bin_cluster_config_cost_constraint_when_extra_budget_small(
        self,
    ):
        region_caps = {"A": 200.0, "B": 100.0}
        region_ranges = {"A": 0.3, "B": 0.1}
        region_lcoe_data = {
            "A": {"lcoe": np.array([0.0, 20.0]), "capacity": np.array([100.0, 100.0])},
            "B": {"lcoe": np.array([9.5, 10.5]), "capacity": np.array([50.0, 50.0])},
        }

        cfg, total, effective_target, minimum_budget = (
            compute_region_bin_cluster_config(
                region_caps, region_lcoe_data, region_ranges, 4, 100.0, 2
            )
        )

        assert minimum_budget == 3
        assert effective_target == 4
        assert total == 4
        assert cfg["A"]["bins"] == 2
        assert cfg["B"]["bins"] == 1
        assert cfg["A"]["n_clusters"] == 1
        assert cfg["B"]["n_clusters"] == 2

    def test_suggest_total_resources(self):
        region_caps = {"A": 0.0, "B": 1999.0, "C": 2000.0}

        suggested = suggest_total_resources(region_caps, 2000.0)

        assert suggested == 2

    def test_compute_suggested_budget_uses_selected_capacity_chunks(self):
        region_data = {
            "R1": {
                "cum_mwh": np.array([100.0, 200.0]),
                "capacity_mw": np.array([1200.0, 900.0]),
            },
            "R2": {
                "cum_mwh": np.array([50.0, 100.0, 150.0]),
                "capacity_mw": np.array([1800.0, 1700.0, 1700.0]),
            },
        }
        region_targets = {"R1": 150.0, "R2": 60.0}

        wind_suggested = compute_suggested_budget(region_data, region_targets, 2000.0)
        solar_suggested = compute_suggested_budget(region_data, region_targets, 5000.0)

        assert wind_suggested == 4
        assert solar_suggested == 2

    def test_region_targets_skip_missing_and_zero(self):
        region_data_keys = {"A", "B", "C"}
        demand_map = {"A": 100.0, "B": 0.0, "D": 50.0}

        targets = compute_region_targets(region_data_keys, demand_map, 0.5)

        assert targets == {"A": 50.0}

    def test_region_targets_skip_missing_or_zero_regions(self):
        region_demand = {"A": 100.0, "B": 0.0, "C": 200.0}
        region_data = {"A": {"lcoe": np.array([10.0])}, "B": {}, "D": {}}

        targets = compute_region_targets_by_demand_and_lcoe(
            region_demand, region_data, 0.5
        )

        assert targets == {"A": 50.0}

    def test_assign_weighted_bins_preserves_row_count_and_range(self):
        region_df = pd.DataFrame(
            {
                "capacity_mw": [1.0, 2.0, 3.0, 4.0],
                "lcoe": [10.0, 20.0, 30.0, 40.0],
            }
        )

        q = 3
        bin_ids = _assign_weighted_bins(region_df, "lcoe", q)

        assert len(bin_ids) == len(region_df)
        assert int(np.min(bin_ids)) >= 0
        assert int(np.max(bin_ids)) <= q - 1

    def test_supply_curve_aggregation_preserves_filtered_capacity(self):
        region_df = pd.DataFrame(
            {
                "capacity_mw": [10.0, 0.0, -2.0, 20.0, 5.0],
                "lcoe": [20.0, 30.0, 40.0, 10.0, 25.0],
            }
        )
        cluster_item = {
            "bin": [{"feature": "lcoe", "q": 2}],
            "cluster": [{"feature": "lcoe", "n_clusters": 2}],
        }

        aggregated = _build_aggregated_supply_curve_bars(region_df, cluster_item)

        expected_capacity = float(
            region_df.loc[region_df["capacity_mw"] > 0.0, "capacity_mw"].sum()
        )
        assert sum(bar["capacity_mw"] for bar in aggregated) == pytest.approx(
            expected_capacity
        )

    def test_supply_curve_aggregation_uses_bin_plus_cluster_pipeline(self):
        region_df = pd.DataFrame(
            {
                "capacity_mw": [10.0, 10.0, 10.0, 10.0],
                "lcoe": [10.0, 20.0, 80.0, 90.0],
                "cf": [0.10, 0.11, 0.80, 0.81],
            }
        )
        cluster_item = {
            "bin": [{"feature": "lcoe", "q": 2}],
            "cluster": [{"feature": "cf", "n_clusters": 2}],
        }

        aggregated = _build_aggregated_supply_curve_bars(region_df, cluster_item)

        assert aggregated
        assert {bar["bin"] for bar in aggregated} == {1, 2}
        assert len(aggregated) <= 2 * 2

    def test_supply_curve_aggregation_missing_cluster_feature_falls_back_to_lcoe(self):
        region_df = pd.DataFrame(
            {
                "capacity_mw": [8.0, 12.0, 9.0],
                "lcoe": [15.0, 18.0, 12.0],
            }
        )
        cluster_item = {
            "bin": [{"feature": "lcoe", "q": 2}],
            "cluster": [{"feature": "missing_feature", "n_clusters": 2}],
        }

        aggregated = _build_aggregated_supply_curve_bars(region_df, cluster_item)

        assert aggregated
        assert all(
            "Bin " in bar["label"] and "Cluster " in bar["label"] for bar in aggregated
        )

    def test_agglomerative_1d_labels_separates_clear_value_groups(self):
        values = np.array([100.0, 1.0, 101.0, 2.0], dtype=float)
        weights = np.array([5.0, 1.0, 5.0, 1.0], dtype=float)

        labels = _agglomerative_1d_labels(values, weights, 2)

        assert len(set(labels.tolist())) == 2
        assert labels[0] == labels[2]
        assert labels[1] == labels[3]
        assert labels[0] != labels[1]
        assert float(
            weights[labels == labels[0]].sum() + weights[labels == labels[1]].sum()
        ) == pytest.approx(float(weights.sum()))

    def test_supply_curve_aggregation_empty_input_returns_empty(self):
        empty_df = pd.DataFrame(columns=["capacity_mw", "lcoe"])
        assert _build_aggregated_supply_curve_bars(empty_df, {}) == []

    def test_extract_cluster_lcoe_max_from_filter_list(self):
        cluster_item = {
            "filter": [
                {"feature": "state", "max": "ignore"},
                {"feature": "lcoe", "max": "37.5"},
            ]
        }

        assert _extract_cluster_lcoe_max(cluster_item) == 37.5

    def test_extract_cluster_lcoe_max_returns_none_when_missing(self):
        cluster_item = {"filter": [{"feature": "capacity", "max": 200}]}

        assert _extract_cluster_lcoe_max(cluster_item) is None

    @pytest.mark.parametrize(
        ("cluster_item", "expected_q"),
        [
            ({}, 1),
            ({"bin": []}, 1),
            ({"bin": [{"q": None}]}, 1),
            ({"bin": [{"q": 0.2}]}, 1),
            ({"bin": [{"q": 2.49}]}, 2),
            ({"bin": [{"q": 2.5}]}, 2),
            ({"bin": [{"q": 2.51}]}, 3),
        ],
    )
    def test_extract_cluster_q_defaults_and_rounds_positive_values(
        self, cluster_item, expected_q
    ):
        assert _extract_cluster_q(cluster_item) == expected_q

    @pytest.mark.parametrize(
        ("cum_capacity", "target", "expected_idx"),
        [
            ([], 10.0, None),
            ([100.0, 250.0, 400.0], -5.0, 0),
            ([100.0, 250.0, 400.0], 0.0, 0),
            ([100.0, 250.0, 400.0], 250.0, 1),
            ([100.0, 250.0, 400.0], 300.0, 2),
            ([100.0, 250.0, 400.0], 999.0, 2),
        ],
    )
    def test_compute_cutoff_idx_from_capacity_searchsorted_with_bounds(
        self, cum_capacity, target, expected_idx
    ):
        assert _compute_cutoff_idx_from_capacity(cum_capacity, target) == expected_idx

    def test_apply_capacity_override_clamps_to_available_upper_bound(self):
        region_data = {
            "cum_capacity_mw": np.array([100.0, 180.0, 260.0]),
            "lcoe": np.array([20.0, 25.0, 30.0]),
            "baseline_capacity_mw": 180.0,
            "available_capacity_mw": 260.0,
            "baseline_cutoff_idx": 1,
        }

        _apply_capacity_override_to_curve_data(region_data, 500.0)
        assert region_data["included_capacity_mw"] == pytest.approx(260.0)
        assert region_data["cutoff_idx"] == 2

    def test_apply_capacity_override_below_baseline_can_reduce_to_first_bucket(self):
        region_data = {
            "cum_capacity_mw": np.array([100.0, 180.0, 260.0]),
            "lcoe": np.array([20.0, 25.0, 30.0]),
            "baseline_capacity_mw": 180.0,
            "available_capacity_mw": 260.0,
            "baseline_cutoff_idx": 1,
            "cutoff_idx": 2,
            "included_capacity_mw": 260.0,
            "lcoe_max": 30.0,
        }

        _apply_capacity_override_to_curve_data(region_data, 120.0)

        assert region_data["cutoff_idx"] == 1
        assert region_data["included_capacity_mw"] == pytest.approx(180.0)
        assert region_data["lcoe_max"] == pytest.approx(25.0)

        _apply_capacity_override_to_curve_data(region_data, 50.0)

        assert region_data["cutoff_idx"] == 0
        assert region_data["included_capacity_mw"] == pytest.approx(100.0)
        assert region_data["lcoe_max"] == pytest.approx(20.0)

    def test_supply_curve_compression_preserves_capacity_and_caps_point_count(self):
        curve_data = {
            "capacity_mw": np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]),
            "lcoe": np.array([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]),
        }

        bars = _build_supply_curve_bars_from_curve_data(curve_data, max_points=3)

        assert len(bars) <= 3
        assert sum(bar["capacity_mw"] for bar in bars) == pytest.approx(
            float(np.sum(curve_data["capacity_mw"]))
        )


# ============================================================================
# YAML Region Parsing Tests
# ============================================================================


def parse_yaml_regions_logic(state, yaml_text):
    """
    Parse YAML region definitions and load them into manual regions.

    Returns (success, message) tuple.
    """
    try:
        # Parse YAML
        parsed = yaml.safe_load(yaml_text)

        if not isinstance(parsed, dict):
            return False, "YAML must be a dictionary/mapping"

        # Determine format and extract region_aggregations
        region_aggregations = None

        # Format 1: Full definition with model_regions and region_aggregations
        if "region_aggregations" in parsed:
            region_aggregations = parsed["region_aggregations"]

            if not isinstance(region_aggregations, dict):
                return False, "region_aggregations must be a dictionary"
        # Format 2 & 3: Direct region mappings
        else:
            region_aggregations = parsed

        # Validate region_aggregations structure
        if not region_aggregations:
            return False, "No region definitions found in YAML"

        for region_name, bas in region_aggregations.items():
            if not isinstance(bas, list):
                return False, f"Region '{region_name}' must map to a list of BAs"
            if not all(isinstance(ba, str) for ba in bas):
                return False, f"All BAs in region '{region_name}' must be strings"

        # Validate that all BAs exist in our data
        invalid_bas = []
        for region_name, bas in region_aggregations.items():
            for ba in bas:
                if ba not in state.all_bas:
                    invalid_bas.append(ba)

        if invalid_bas:
            unique_invalid = sorted(set(invalid_bas))
            return False, f"Invalid BA codes in YAML: {', '.join(unique_invalid[:10])}"

        # Check for duplicate BAs across regions
        all_bas = []
        for bas in region_aggregations.values():
            all_bas.extend(bas)

        if len(all_bas) != len(set(all_bas)):
            duplicates = [ba for ba in set(all_bas) if all_bas.count(ba) > 1]
            return (
                False,
                f"Duplicate BAs found in multiple regions: {', '.join(duplicates[:5])}",
            )

        # Load regions from YAML
        state.manual_regions.clear()
        for region_name, bas in region_aggregations.items():
            state.manual_regions[region_name] = list(bas)

        num_regions = len(state.manual_regions)
        total_bas = sum(len(bas) for bas in state.manual_regions.values())
        region_word = "region" if num_regions == 1 else "regions"
        ba_word = "BA" if total_bas == 1 else "BAs"
        return (
            True,
            f"Successfully loaded {num_regions} {region_word} with {total_bas} {ba_word} from YAML",
        )

    except yaml.YAMLError as e:
        return False, f"Invalid YAML format: {str(e)}"
    except Exception as e:
        return False, f"Error parsing YAML: {str(e)}"


class TestYAMLRegionParsing:
    """Test YAML region parsing functionality."""

    def test_format1_full_definition(self):
        """Test Format 1: Full definition with model_regions and region_aggregations."""
        state = MockAppState()
        state.all_bas = {"p8", "p9", "p10", "p11", "p27", "p28", "p29", "p30", "p31"}

        yaml_text = """
model_regions:
  - AZ
  - CA
  - p31
region_aggregations:
  CA:
    - p8
    - p9
    - p10
    - p11
  AZ:
    - p29
    - p28
    - p27
    - p30
"""

        success, msg = parse_yaml_regions_logic(state, yaml_text)

        assert success is True
        assert "2 regions with 8 BAs" in msg
        assert "CA" in state.manual_regions
        assert "AZ" in state.manual_regions
        assert set(state.manual_regions["CA"]) == {"p8", "p9", "p10", "p11"}
        assert set(state.manual_regions["AZ"]) == {"p29", "p28", "p27", "p30"}

    def test_format2_region_aggregations_only(self):
        """Test Format 2: Only region_aggregations without model_regions."""
        state = MockAppState()
        state.all_bas = {"p8", "p9", "p10", "p11", "p27", "p28", "p29", "p30"}

        yaml_text = """
region_aggregations:
  CA:
    - p8
    - p9
    - p10
    - p11
  AZ:
    - p29
    - p28
    - p27
    - p30
"""

        success, msg = parse_yaml_regions_logic(state, yaml_text)

        assert success is True
        assert "2 regions with 8 BAs" in msg
        assert "CA" in state.manual_regions
        assert "AZ" in state.manual_regions

    def test_format3_direct_mappings(self):
        """Test Format 3: Direct region mappings without region_aggregations wrapper."""
        state = MockAppState()
        state.all_bas = {"p8", "p9", "p10", "p11", "p27", "p28", "p29", "p30"}

        yaml_text = """
CA:
  - p8
  - p9
  - p10
  - p11
AZ:
  - p29
  - p28
  - p27
  - p30
"""

        success, msg = parse_yaml_regions_logic(state, yaml_text)

        assert success is True
        assert "2 regions with 8 BAs" in msg
        assert "CA" in state.manual_regions
        assert "AZ" in state.manual_regions

    def test_invalid_yaml_format(self):
        """Test handling of invalid YAML syntax."""
        state = MockAppState()
        state.all_bas = {"p8", "p9"}

        yaml_text = """
CA:
  - p8
  invalid syntax here
"""

        success, msg = parse_yaml_regions_logic(state, yaml_text)

        assert success is False
        assert "Invalid YAML format" in msg

    def test_non_dict_yaml(self):
        """Test handling of YAML that's not a dictionary."""
        state = MockAppState()
        state.all_bas = {"p8", "p9"}

        yaml_text = """
- p8
- p9
"""

        success, msg = parse_yaml_regions_logic(state, yaml_text)

        assert success is False
        assert "must be a dictionary" in msg

    def test_region_not_list(self):
        """Test handling of region that doesn't map to a list."""
        state = MockAppState()
        state.all_bas = {"p8", "p9"}

        yaml_text = """
CA: p8
"""

        success, msg = parse_yaml_regions_logic(state, yaml_text)

        assert success is False
        assert "must map to a list" in msg

    def test_invalid_ba_codes(self):
        """Test handling of BA codes that don't exist."""
        state = MockAppState()
        state.all_bas = {"p8", "p9"}

        yaml_text = """
CA:
  - p8
  - invalid_ba
  - another_invalid
"""

        success, msg = parse_yaml_regions_logic(state, yaml_text)

        assert success is False
        assert "Invalid BA codes" in msg
        assert "invalid_ba" in msg or "another_invalid" in msg

    def test_duplicate_bas_across_regions(self):
        """Test handling of duplicate BAs in multiple regions."""
        state = MockAppState()
        state.all_bas = {"p8", "p9", "p10"}

        yaml_text = """
CA:
  - p8
  - p9
AZ:
  - p9
  - p10
"""

        success, msg = parse_yaml_regions_logic(state, yaml_text)

        assert success is False
        assert "Duplicate BAs" in msg
        assert "p9" in msg

    def test_empty_yaml(self):
        """Test handling of empty YAML."""
        state = MockAppState()
        state.all_bas = {"p8", "p9"}

        yaml_text = ""

        success, msg = parse_yaml_regions_logic(state, yaml_text)

        # Empty YAML returns None, which is not a dict
        assert success is False

    def test_empty_region_aggregations(self):
        """Test handling of empty region_aggregations."""
        state = MockAppState()
        state.all_bas = {"p8", "p9"}

        yaml_text = """
region_aggregations: {}
"""

        success, msg = parse_yaml_regions_logic(state, yaml_text)

        assert success is False
        assert "No region definitions found" in msg

    def test_non_string_ba_ids(self):
        """Test handling of non-string BA IDs."""
        state = MockAppState()
        state.all_bas = {"8", "9"}

        yaml_text = """
CA:
  - 8
  - 9
"""

        success, msg = parse_yaml_regions_logic(state, yaml_text)

        # Numbers are valid in YAML but should be caught as non-strings
        assert success is False
        assert "must be strings" in msg

    def test_clears_existing_regions(self):
        """Test that parsing new YAML clears existing manual regions."""
        state = MockAppState()
        state.all_bas = {"p8", "p9", "p10"}
        state.manual_regions = {"OldRegion": ["p8"]}

        yaml_text = """
NewRegion:
  - p9
  - p10
"""

        success, msg = parse_yaml_regions_logic(state, yaml_text)

        assert success is True
        assert "OldRegion" not in state.manual_regions
        assert "NewRegion" in state.manual_regions

    def test_single_region(self):
        """Test parsing YAML with a single region."""
        state = MockAppState()
        state.all_bas = {"p8", "p9", "p10"}

        yaml_text = """
SingleRegion:
  - p8
  - p9
  - p10
"""

        success, msg = parse_yaml_regions_logic(state, yaml_text)

        assert success is True
        assert "1 region" in msg and "3 BAs" in msg
        assert len(state.manual_regions) == 1

    def test_many_regions(self):
        """Test parsing YAML with many regions."""
        state = MockAppState()
        state.all_bas = {f"ba{i}" for i in range(20)}

        yaml_parts = []
        for i in range(10):
            yaml_parts.append(f"Region{i}:\n  - ba{i*2}\n  - ba{i*2+1}")
        yaml_text = "\n".join(yaml_parts)

        success, msg = parse_yaml_regions_logic(state, yaml_text)

        assert success is True
        assert "10 regions with 20 BAs" in msg
        assert len(state.manual_regions) == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
