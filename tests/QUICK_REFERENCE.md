# Quick Reference: Running Manual Region Tests

## Quick Test Commands

### Run ALL manual region tests (52 tests)
```bash
pytest tests/test_cluster_app.py::TestManualRegionCreation \
       tests/test_cluster_app.py::TestBAAssignment \
       tests/test_cluster_app.py::TestRegionSelection \
       tests/test_cluster_app.py::TestFinalization \
       tests/test_cluster_app.py::TestClearManualRegions \
       tests/test_cluster_app.py::TestModeSwitching \
       tests/test_cluster_app.py::TestManualRegionIntegration \
       tests/test_cluster_app.py::TestEdgeCases -v
```

### Run tests by category

**Region Creation/Removal (11 tests)**
```bash
pytest tests/test_cluster_app.py::TestManualRegionCreation -v
```

**BA Assignment (8 tests)**
```bash
pytest tests/test_cluster_app.py::TestBAAssignment -v
```

**Finalization (7 tests)**
```bash
pytest tests/test_cluster_app.py::TestFinalization -v
```

**Mode Switching (5 tests)**
```bash
pytest tests/test_cluster_app.py::TestModeSwitching -v
```

**Integration Tests (8 tests)**
```bash
pytest tests/test_cluster_app.py::TestManualRegionIntegration -v
```

**Edge Cases (7 tests)**
```bash
pytest tests/test_cluster_app.py::TestEdgeCases -v
```

### Run specific test
```bash
pytest tests/test_cluster_app.py::TestManualRegionCreation::test_add_region_success -v
```

### Run tests matching pattern
```bash
# All tests with "manual" in the name
pytest tests/test_cluster_app.py -k "manual" -v

# All tests with "finalize" in the name
pytest tests/test_cluster_app.py -k "finalize" -v
```

### Run all tests in the file
```bash
pytest tests/test_cluster_app.py -v
```

### Show test coverage
```bash
pytest tests/test_cluster_app.py --cov=web.cluster_app --cov-report=term-missing
```

## Test Categories Overview

| Category | Tests | Description |
|----------|-------|-------------|
| **TestManualRegionCreation** | 11 | Region CRUD operations |
| **TestBAAssignment** | 8 | BA assignment logic |
| **TestRegionSelection** | 3 | Region selection state |
| **TestFinalization** | 7 | Converting to region_aggregations |
| **TestClearManualRegions** | 3 | Clearing all data |
| **TestModeSwitching** | 5 | Mode transitions |
| **TestManualRegionIntegration** | 8 | Complete workflows |
| **TestEdgeCases** | 7 | Boundary conditions |
| **TOTAL** | **52** | |

## Key Test Scenarios Covered

✅ **Creation & Validation**
- Adding regions with valid/invalid names
- Duplicate name prevention
- Removing regions

✅ **BA Assignment**
- Assigning BAs to regions
- Preventing double-assignment
- Partial overlaps
- Incremental assignment

✅ **Finalization**
- Converting to region_aggregations
- Validating non-empty regions
- BA-to-region mapping
- YAML generation format

✅ **Mode Switching**
- Clustering ↔ Manual mode transitions
- Data clearing on mode change

✅ **Integration**
- Complete workflows
- Compatibility with downstream processing
- Data structure consistency

✅ **Edge Cases**
- Special characters, Unicode
- Very long names
- Empty inputs
- Boundary conditions

## Expected Test Results

```
52 tests collected

TestManualRegionCreation::test_add_region_success PASSED
TestManualRegionCreation::test_add_multiple_regions PASSED
[... 48 more tests ...]
TestEdgeCases::test_empty_ba_id PASSED

======================== 52 passed in 0.4s ========================
```

## Testing Best Practices

1. **Run tests before making changes** to establish baseline
2. **Run tests after each change** to catch regressions
3. **Use `-v` flag** for verbose output showing each test
4. **Use `-k` pattern** to run specific subsets quickly
5. **Check coverage** to identify untested code paths
6. **Keep tests fast** - all 52 tests run in < 1 second

## Troubleshooting

### Import errors
```bash
pip install pytest networkx numpy pandas pyyaml
```

### Tests not found
```bash
# Ensure you're in the project root
cd /path/to/PowerGenome-tools
pytest tests/test_cluster_app.py -v
```

### View test collection without running
```bash
pytest tests/test_cluster_app.py --collect-only
```

## CI/CD Integration

Add to your CI pipeline:
```yaml
- name: Run manual region tests
  run: |
    pytest tests/test_cluster_app.py::TestManualRegionCreation \
           tests/test_cluster_app.py::TestBAAssignment \
           tests/test_cluster_app.py::TestFinalization \
           tests/test_cluster_app.py::TestManualRegionIntegration \
           --tb=short --verbose
```

## Next Steps

After tests pass:
1. ✅ Review test coverage
2. ✅ Add any missing edge cases
3. ✅ Document findings
4. ✅ Commit tests with feature code
5. ✅ Update CI/CD pipeline
