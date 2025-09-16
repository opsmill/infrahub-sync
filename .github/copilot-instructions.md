# Infrahub Sync Development Guide

ALWAYS reference these instructions first and fallback to search or bash commands only when you encounter unexpected information that does not match the info here.

Infrahub Sync is a Python package that synchronizes data between source and destination systems (NetBox, Nautobot, Infrahub, etc.). It uses Poetry for dependency management, has a Typer-based CLI, and includes extensive examples for different sync scenarios.

## Working Effectively

### Bootstrap and Setup
- **Install Python 3.9-3.12**: The project supports Python 3.9 through 3.12
- **Install Poetry**: `pip install poetry` (takes ~30 seconds)
- **Install Dependencies**: `poetry install` (takes ~15 seconds, installs 99+ packages)
- **Verify CLI**: `poetry run infrahub-sync --help` (should show available commands: list, diff, generate, sync)

### Development Environment Validation
- **Test CLI functionality**: `poetry run infrahub-sync list` (lists available sync projects)
- **Verify example projects**: `poetry run infrahub-sync list --directory examples/` (shows 12+ sync configurations)
- **Test generation**: `poetry run infrahub-sync generate --name from-netbox --directory examples/` (requires running Infrahub server at localhost:8000)

### Code Quality and Linting
- **Ruff formatting**: `poetry run invoke linter.format-ruff` (takes <1 second, formats Python code)
- **Ruff linting**: `poetry run invoke linter.lint-ruff` (takes <1 second, checks code style)
- **Pylint**: `poetry run invoke linter.lint-pylint` (takes ~15 seconds, comprehensive Python linting with some expected warnings)
- **YAML linting**: `poetry run invoke linter.lint-yaml` (takes <1 second, validates YAML files)
- **MyPy type checking**: `poetry run mypy infrahub_sync/ --ignore-missing-imports` (takes ~14 seconds, finds type issues but code works)
- **All linting**: `poetry run invoke lint` (runs all linters sequentially)

### Testing
- **Test Structure**: Tests directory exists at `tests/` but contains no actual test files (only `__init__.py` files)
- **Pytest**: `poetry run pytest tests/ -v` (exits with no tests found - this is expected)
- **NO CURRENT TESTS**: The project currently has no unit or integration tests implemented

### Documentation Build
- **Install Node Dependencies**: `cd docs && npm install` (takes ~48 seconds, installs 1280+ packages)
- **Build Documentation**: 
  - Direct: `cd docs && npm run build` (takes ~30 seconds)
  - Via invoke: `poetry run invoke docs.docusaurus` (takes ~5 seconds after first build)
- **Generate CLI Docs**: `poetry run invoke docs.generate` (takes ~2 seconds, generates CLI reference)

### Available Invoke Tasks
Run `poetry run invoke --list` to see all tasks:
- `linter.format-ruff` - Format Python code with ruff
- `linter.lint-ruff` - Lint Python code with ruff  
- `linter.lint-pylint` - Lint Python code with pylint
- `linter.lint-yaml` - Lint YAML files with yamllint
- `docs.generate` - Generate CLI documentation
- `docs.docusaurus` - Build documentation website

## Validation Scenarios

### Essential Validation Steps
After making changes to the codebase:

1. **ALWAYS run formatting and linting**:
   ```bash
   poetry run invoke linter.format-ruff
   poetry run invoke linter.lint-ruff
   poetry run invoke linter.lint-yaml
   ```

2. **Test CLI functionality**:
   ```bash
   # Verify CLI works
   poetry run infrahub-sync --help
   
   # List available sync projects  
   poetry run infrahub-sync list --directory examples/
   
   # Test a specific sync project (will fail without running servers, but validates parsing)
   poetry run infrahub-sync generate --name from-netbox --directory examples/
   ```

3. **Documentation validation** (if docs changes):
   ```bash
   poetry run invoke docs.generate
   poetry run invoke docs.docusaurus
   ```

## Important Code Locations

### Core Application Structure
- `infrahub_sync/cli.py` - Main CLI application entry point
- `infrahub_sync/__init__.py` - Core SyncInstance and configuration classes
- `infrahub_sync/utils.py` - Utility functions for sync operations
- `infrahub_sync/potenda/` - Core sync engine implementation
- `infrahub_sync/adapters/` - Adapters for different systems (NetBox, Nautobot, etc.)

### Configuration and Examples
- `examples/` - 12+ complete sync configuration examples
- `examples/netbox_to_infrahub/config.yml` - Example NetBox to Infrahub sync config
- `pyproject.toml` - Project configuration, dependencies, and tool settings

### Build and CI Files
- `tasks/` - Invoke task definitions for linting, testing, and documentation
- `.github/workflows/` - GitHub Actions workflow files
- `docs/` - Docusaurus documentation source (separate npm project)

## Common Patterns

### Adding New Adapters
- Create new adapter in `infrahub_sync/adapters/`
- Follow existing patterns from `infrahub_sync/adapters/netbox.py` or similar
- Add corresponding example configuration in `examples/`

### Configuration Files
- All sync configurations are YAML files with `name`, `source`, `destination`, and `order` sections
- Source and destination specify adapter names and connection settings
- Order defines the sequence for syncing different object types

### CLI Commands
- `infrahub-sync list` - Show available sync projects
- `infrahub-sync diff` - Calculate differences between source/destination
- `infrahub-sync sync` - Perform actual synchronization
- `infrahub-sync generate` - Generate Python sync files from config

## Expected Timing and Tolerances

### Command Execution Times
- Poetry install: ~15 seconds
- Ruff formatting/linting: <1 second each  
- Pylint: ~15 seconds
- MyPy type checking: ~14 seconds
- npm install (docs): ~48 seconds
- Documentation build: ~5-30 seconds
- CLI help/list commands: <3 seconds

### Timeout Recommendations
- Use default timeouts for most development commands
- For CI builds, allow 60+ seconds for documentation builds
- MyPy and pylint may take up to 30 seconds

## Known Issues and Limitations

### Expected Warnings/Errors
- **Pylint warnings**: Code has expected pylint warnings (score 9.78/10) - these don't block functionality
- **MyPy type errors**: 125+ type errors found but code works correctly - focus on functionality over perfect typing
- **No tests**: Project currently has no unit/integration tests - don't expect `pytest` to run tests
- **Import errors**: Some optional dependencies (pynetbox, pynautobot, etc.) may show import errors if not installed

### Connection Requirements
- **Generate/sync commands**: Require running source/destination servers (Infrahub, NetBox, etc.)
- **Localhost connections**: Default configs use localhost - adapt for actual deployments
- **Authentication**: Example configs use placeholder tokens/credentials

### Documentation Security Warnings
- npm audit reports 16 moderate vulnerabilities in docs dependencies - these are in development-only documentation tools and don't affect the Python package

## Development Workflow

### Making Changes
1. **Always format before committing**: `poetry run invoke linter.format-ruff`
2. **Validate with linting**: `poetry run invoke linter.lint-ruff && poetry run invoke linter.lint-yaml`  
3. **Test CLI functionality**: Verify commands work with example configs
4. **Update documentation**: Run `poetry run invoke docs.generate` if CLI changes

### Debugging Sync Issues
- Use `infrahub-sync diff` to see what would change before syncing
- Check adapter-specific logs and connection settings
- Verify source/destination systems are accessible and configured properly
- Review example configurations for reference patterns