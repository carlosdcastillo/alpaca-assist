# Testing Guide for pywebview_demo

This directory contains comprehensive test coverage for the pywebview_demo project.

## Overview

The test suite uses **pytest** as the testing framework with the following key features:

- **pytest**: Core testing framework
- **pytest-asyncio**: For testing async code (MCP manager)
- **pytest-cov**: For coverage reporting
- **pytest-mock**: For mocking utilities
- **hypothesis**: For property-based testing
- **factory-boy**: For test data generation
- **freezegun**: For time manipulation in tests
- **responses**: For mocking HTTP requests

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures and configuration
├── test_database.py         # Database operations tests
├── test_mcp_manager.py      # MCP manager tests
├── test_agent_skills.py     # Agent skills tests
├── test_shell_executor.py   # Shell executor tests
├── test_chat_state.py       # Chat state tests
└── README.md               # This file
```

## Running Tests

### Run all tests
```bash
pytest
```

### Run with coverage
```bash
pytest --cov=. --cov-report=html --cov-report=term
```

### Run specific test file
```bash
pytest tests/test_database.py
```

### Run specific test class
```bash
pytest tests/test_database.py::TestDatabaseInitialization
```

### Run specific test method
```bash
pytest tests/test_database.py::TestDatabaseInitialization::test_init_creates_database_file
```

### Run with markers
```bash
# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Exclude slow tests
pytest -m "not slow"

# Run async tests
pytest -m asyncio
```

### Run with verbose output
```bash
pytest -v
```

### Run with fail-fast (stop on first failure)
```bash
pytest -x
```

## Test Categories

### Unit Tests
Fast, isolated tests that don't require external services:
- `test_database.py`
- `test_chat_state.py`
- `test_agent_skills.py`

### Integration Tests
Tests that verify component interactions:
- `test_mcp_manager.py` (requires mocking)
- `test_shell_executor.py` (may run real commands)

### Slow Tests
Long-running tests marked with `@pytest.mark.slow`:
- Stress tests
- Performance tests
- Tests with many iterations

## Fixtures

Common fixtures are defined in `conftest.py`:

- `temp_dir`: Temporary directory for test files
- `temp_file`: Temporary file path
- `mock_db`: Mock database instance
- `mock_preferences`: Mock preferences data
- `empty_chat_state`: Empty chat state
- `populated_chat_state`: Chat state with sample data
- `tool_call_detector`: ToolCallDetector instance
- `shell_executor`: ShellExecutor instance
- `skill_manager`: SkillManager instance

## Writing Tests

### Basic Test Structure
```python
class TestMyFeature:
    """Tests for MyFeature."""

    def test_something(self) -> None:
        """Test that something works."""
        assert True
```

### Using Fixtures
```python
def test_with_fixture(self, temp_dir: Path) -> None:
    """Test using temp_dir fixture."""
    file_path = temp_dir / "test.txt"
    file_path.write_text("content")
    assert file_path.exists()
```

### Async Tests
```python
@pytest.mark.asyncio
async def test_async_function(self) -> None:
    """Test async function."""
    result = await some_async_function()
    assert result is True
```

### Mocking
```python
def test_with_mock(self) -> None:
    """Test with mocking."""
    with patch("module.function") as mock_func:
        mock_func.return_value = True
        result = function_under_test()
        assert result is True
```

## Coverage Goals

- **Minimum coverage**: 70% (set in pyproject.toml)
- **Target coverage**: 80%+
- **Critical paths**: 90%+

## Continuous Integration

Tests should be run:
1. On every commit (pre-commit hook recommended)
2. On every pull request
3. Before every release

## Troubleshooting

### Tests failing due to missing dependencies
```bash
pip install -r requirements.txt
```

### Database tests failing
Database tests use temporary files that are automatically cleaned up. If tests fail mid-run, temporary directories may remain. Clean with:
```bash
pytest --clean-tmp
```

### Async tests timing out
Increase timeout in pytest.ini or use `@pytest.mark.timeout(seconds)` decorator.

### Coverage not including all files
Ensure `source = ["."]` is set correctly in pyproject.toml and that files aren't excluded in the `omit` list.

## Best Practices

1. **Test names**: Use descriptive names like `test_<action>_<condition>_<expected_result>`
2. **Docstrings**: Every test should have a docstring explaining what it tests
3. **Arrange-Act-Assert**: Structure tests clearly
4. **One assertion per test**: When possible, test one thing at a time
5. **Use fixtures**: Avoid setup/teardown code in tests
6. **Parametrize**: Use `@pytest.mark.parametrize` for multiple similar test cases
7. **Mock external services**: Don't depend on external services in unit tests

## Additional Resources

- [pytest documentation](https://docs.pytest.org/)
- [pytest-asyncio documentation](https://pytest-asyncio.readthedocs.io/)
- [pytest-cov documentation](https://pytest-cov.readthedocs.io/)
